from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from .audio_builder import AudioBuilder
from .config import TranslationConfig
from .llm_client import LLMClient
from .models import Chapter, Job, JobKind, JobStatus, SegmentStatus
from .repository import Repository
from .storage import LocalStorage
from .text_segmenter import split_text


class JobRunner:
    def __init__(
        self,
        repository: Repository,
        storage: LocalStorage,
        llm_client: LLMClient | None = None,
        tts_client: Any | None = None,
        audio_builder: AudioBuilder | None = None,
        tts_max_chars: int = 900,
    ) -> None:
        self.repository = repository
        self.storage = storage
        self.llm_client = llm_client or LLMClient()
        self.tts_client = tts_client
        self.audio_builder = audio_builder or AudioBuilder(ffmpeg_path=None)
        self.tts_max_chars = tts_max_chars

    async def start_translation(
        self,
        chapter_id: int,
        config: TranslationConfig,
        parallel_segments: int,
        api_key_override: str | None = None,
    ) -> Job:
        chapter = self.repository.get_chapter(chapter_id)
        source_text = self.storage.read_text(chapter.text_path)
        source_segments = _chapter_segments(source_text, config.segment_limit)
        job = self.repository.create_job(
            chapter.book_id,
            chapter.id,
            JobKind.TRANSLATE,
            len(source_segments),
            {
                "parallel_segments": parallel_segments,
                "chapter_revision": chapter.content_revision,
            },
        )
        if not source_segments:
            self.repository.fail_job(job.id, "chapter has no translatable text")
            return self.repository.get_job(job.id)
        self.repository.create_segments(job.id, chapter.id, source_segments)
        return await self.run_translation_job(
            job.id,
            _with_api_key_override(config, api_key_override),
            parallel_segments,
        )

    async def run_translation_job(
        self,
        job_id: int,
        config: TranslationConfig,
        parallel_segments: int,
    ) -> Job:
        job = self.repository.get_job(job_id)
        if job.kind != JobKind.TRANSLATE:
            raise ValueError(f"job is not a translation job: {job_id}")
        if job.chapter_id is None:
            raise ValueError(f"translation job has no chapter: {job_id}")
        if job.pause_requested or job.stop_requested or job.status in _TERMINAL_STATUSES:
            return self.repository.refresh_job_progress(job_id)

        self._ensure_segments(job, config)
        if not self.repository.list_segments(job_id):
            self.repository.fail_job(job_id, "chapter has no translatable text")
            return self.repository.get_job(job_id)

        worker_count = max(1, parallel_segments)
        await asyncio.gather(
            *(self._translate_pending_segments(job_id, config) for _ in range(worker_count))
        )
        await self._write_ordered_translation(job_id, config)
        return self.repository.refresh_job_progress(job_id)

    def _ensure_segments(self, job: Job, config: TranslationConfig) -> None:
        if self.repository.list_segments(job.id):
            return
        if job.chapter_id is None:
            raise ValueError(f"translation job has no chapter: {job.id}")

        chapter = self.repository.get_chapter(job.chapter_id)
        source_text = self.storage.read_text(chapter.text_path)
        self.repository.create_segments(
            job.id,
            chapter.id,
            _chapter_segments(source_text, config.segment_limit),
        )

    async def _translate_pending_segments(
        self,
        job_id: int,
        config: TranslationConfig,
    ) -> None:
        while True:
            job = self.repository.get_job(job_id)
            if job.pause_requested or job.stop_requested or job.status in _TERMINAL_STATUSES:
                return

            segment = self.repository.acquire_next_pending_segment(job_id)
            if segment is None:
                return

            try:
                user_prompt = config.prompt.user_template.format(
                    source_text=segment.source_text
                )
                translated_text = await self.llm_client.translate(
                    config.active,
                    config.prompt.system,
                    user_prompt,
                    config.request_timeout_seconds,
                    config.max_retries,
                )
                current_job = self.repository.get_job(job_id)
                if current_job.pause_requested or current_job.status == JobStatus.PAUSED:
                    self.repository.release_running_segment(
                        segment.id,
                        SegmentStatus.PENDING,
                    )
                    return
                if current_job.stop_requested or current_job.status == JobStatus.STOPPED:
                    self.repository.release_running_segment(
                        segment.id,
                        SegmentStatus.STOPPED,
                    )
                    return
                chapter = self.repository.get_chapter(segment.chapter_id)
                output_path = self.storage.translation_path(
                    chapter.book_id,
                    chapter.chapter_index,
                    segment.segment_index,
                )
                self.storage.write_text(output_path, translated_text)
                self.repository.complete_segment(
                    segment.id,
                    result_text=translated_text,
                    output_path=output_path,
                )
            except Exception as exc:
                self.repository.fail_segment(segment.id, str(exc))

    async def _write_ordered_translation(self, job_id: int, config: TranslationConfig) -> None:
        job = self.repository.refresh_job_progress(job_id)
        if job.chapter_id is None or job.status in {JobStatus.PAUSED, JobStatus.STOPPED}:
            return
        if job.status != JobStatus.COMPLETED:
            return

        segments = self.repository.list_segments(job_id)
        if not segments or any(
            segment.status != SegmentStatus.COMPLETED or segment.result_text is None
            for segment in segments
        ):
            return

        chapter = self.repository.get_chapter(job.chapter_id)
        translated_text = "\n\n".join(segment.result_text or "" for segment in segments)
        output_path = (
            f"books/{chapter.book_id}/translations/{chapter.chapter_index:04d}.txt"
        )
        try:
            self.storage.write_text(output_path, translated_text)
            promoted = self.repository.promote_chapter_translation_path_if_current_job(
                job_id,
                output_path,
            )
        except Exception as exc:
            self.repository.fail_job(job_id, str(exc))
            return
        if not promoted:
            return
        self.repository.update_job_status(job_id, JobStatus.RUNNING)
        self.repository.promote_chapter_translation_metadata_if_current_job(job_id, None, None)
        await self._write_translation_metadata(job_id, config, chapter, translated_text)

    async def _write_translation_metadata(
        self,
        job_id: int,
        config: TranslationConfig,
        chapter: Chapter,
        translated_text: str,
    ) -> None:
        try:
            source_text = self.storage.read_text(chapter.text_path)
            raw_metadata = await self.llm_client.translate(
                config.active,
                _TRANSLATION_METADATA_SYSTEM_PROMPT,
                _translation_metadata_prompt(chapter.title, source_text, translated_text),
                config.request_timeout_seconds,
                config.max_retries,
            )
            translated_title, summary = _parse_translation_metadata(raw_metadata)
            if translated_title is None and summary is None:
                return
            self.repository.promote_chapter_translation_metadata_if_current_job(
                job_id,
                translated_title,
                summary,
            )
        except Exception:
            return

    async def start_tts(
        self,
        chapter_id: int,
        voice: str,
        context: str,
        parallel_segments: int,
        merge: bool = True,
    ) -> Job:
        chapter = self.repository.get_chapter(chapter_id)
        source_text = self.storage.read_text(chapter.text_path)
        source_segments = _chapter_segments(source_text, self.tts_max_chars)
        job = self.repository.create_job(
            chapter.book_id,
            chapter.id,
            JobKind.TTS,
            len(source_segments),
            {
                "voice": voice,
                "context": context,
                "parallel_segments": parallel_segments,
                "merge": merge,
                "chapter_revision": chapter.content_revision,
            },
        )
        self.repository.update_chapter_audio_path(chapter.id, None)
        if not source_segments:
            self.repository.fail_job(job.id, "chapter has no text to synthesize")
            return self.repository.get_job(job.id)
        self.repository.create_segments(job.id, chapter.id, source_segments)
        return await self.run_tts_job(
            job.id,
            voice=voice,
            context=context,
            parallel_segments=parallel_segments,
            merge=merge,
        )

    async def run_tts_job(
        self,
        job_id: int,
        voice: str,
        context: str,
        parallel_segments: int,
        merge: bool = True,
        tts_client: Any | None = None,
    ) -> Job:
        active_tts_client = tts_client or self.tts_client
        if active_tts_client is None:
            raise RuntimeError("tts_client is required to run TTS jobs")
        job = self.repository.get_job(job_id)
        if job.kind != JobKind.TTS:
            raise ValueError(f"job is not a TTS job: {job_id}")
        if job.chapter_id is None:
            raise ValueError(f"TTS job has no chapter: {job_id}")
        if job.pause_requested or job.stop_requested or job.status in _TERMINAL_STATUSES:
            return self.repository.refresh_job_progress(job_id)

        self._ensure_tts_segments(job)
        if not self.repository.list_segments(job_id):
            self.repository.fail_job(job_id, "chapter has no text to synthesize")
            return self.repository.get_job(job_id)

        worker_count = max(1, parallel_segments)
        await asyncio.gather(
            *(
                self._synthesize_pending_segments(job_id, voice, context, active_tts_client)
                for _ in range(worker_count)
            )
        )
        current_job = self.repository.get_job(job_id)
        if (
            current_job.pause_requested
            or current_job.stop_requested
            or current_job.status in {JobStatus.PAUSED, JobStatus.STOPPED}
        ):
            return current_job
        if merge:
            merged_path = self.merge_chapter_audio(job_id)
            if merged_path is not None:
                self.repository.promote_chapter_audio_path_if_latest_tts_job(job_id, merged_path)
        return self.repository.refresh_job_progress(job_id)

    def _ensure_tts_segments(self, job: Job) -> None:
        if self.repository.list_segments(job.id):
            return
        if job.chapter_id is None:
            raise ValueError(f"TTS job has no chapter: {job.id}")

        chapter = self.repository.get_chapter(job.chapter_id)
        source_text = self.storage.read_text(chapter.text_path)
        self.repository.create_segments(
            job.id,
            chapter.id,
            _chapter_segments(source_text, self.tts_max_chars),
        )

    async def _synthesize_pending_segments(
        self,
        job_id: int,
        voice: str,
        context: str,
        tts_client: Any,
    ) -> None:
        while True:
            job = self.repository.get_job(job_id)
            if job.pause_requested or job.stop_requested or job.status in _TERMINAL_STATUSES:
                return

            segment = self.repository.acquire_next_pending_segment(job_id)
            if segment is None:
                return

            try:
                chapter = self.repository.get_chapter(segment.chapter_id)
                output_path = self.storage.audio_path(
                    chapter.book_id,
                    chapter.chapter_index,
                    segment.segment_index,
                    job_id=job_id,
                    extension="wav",
                )
                await asyncio.to_thread(
                    tts_client.synthesize,
                    segment.source_text,
                    voice,
                    context,
                    self.storage.resolve_artifact(output_path),
                )
                current_job = self.repository.get_job(job_id)
                if current_job.pause_requested or current_job.status == JobStatus.PAUSED:
                    self.storage.resolve_artifact(output_path).unlink(missing_ok=True)
                    self.repository.release_running_segment(
                        segment.id,
                        SegmentStatus.PENDING,
                    )
                    return
                if current_job.stop_requested or current_job.status == JobStatus.STOPPED:
                    self.storage.resolve_artifact(output_path).unlink(missing_ok=True)
                    self.repository.release_running_segment(
                        segment.id,
                        SegmentStatus.STOPPED,
                    )
                    return
                self.repository.complete_segment(
                    segment.id,
                    output_path=output_path,
                )
            except Exception as exc:
                self.repository.fail_segment(segment.id, str(exc))

    def merge_chapter_audio(self, job_id: int) -> str | None:
        job = self.repository.refresh_job_progress(job_id)
        if job.chapter_id is None or job.status in {JobStatus.PAUSED, JobStatus.STOPPED}:
            return None
        if job.status != JobStatus.COMPLETED:
            return None

        segments = self.repository.list_segments(job_id)
        if not segments or any(
            segment.status != SegmentStatus.COMPLETED or segment.output_path is None
            for segment in segments
        ):
            return None

        chapter = self.repository.get_chapter(job.chapter_id)
        input_paths = [
            self.storage.resolve_artifact(segment.output_path or "")
            for segment in segments
        ]
        output_path = f"books/{chapter.book_id}/audio/{chapter.chapter_index:04d}/jobs/{job_id}/chapter.wav"
        try:
            merged = self.audio_builder.merge_audio(
                input_paths,
                self.storage.resolve_artifact(output_path),
            )
            if merged is None:
                return None
            return output_path
        except Exception as exc:
            self.repository.fail_job(job_id, str(exc))
            return None


def _with_api_key_override(
    config: TranslationConfig,
    api_key_override: str | None,
) -> TranslationConfig:
    if not api_key_override:
        return config
    active = replace(config.active, api_key=api_key_override)
    providers = dict(config.providers)
    providers[config.active_provider_name] = active
    return replace(config, providers=providers)


_TRANSLATION_METADATA_SYSTEM_PROMPT = (
    "你是专业中文文学编辑。请只输出 JSON，不要输出 Markdown 或额外说明。"
)
_METADATA_SOURCE_LIMIT = 1800
_METADATA_TRANSLATION_LIMIT = 2600


def _translation_metadata_prompt(
    chapter_title: str,
    source_text: str,
    translated_text: str,
) -> str:
    return (
        "请基于以下章节内容生成中文章节名和章节简介。\n"
        "输出 JSON 对象，字段必须为 translated_title 和 summary。\n"
        "translated_title：中文章节名，简洁自然。\n"
        "summary：中文简介，用二到四句话概括当前章节，约 120 到 220 个汉字。"
        "请覆盖主要情节、关键人物或信息、情绪变化或章节作用，避免泛泛而谈。\n\n"
        f"原章节名：{chapter_title}\n\n"
        f"原文节选：\n{source_text[:_METADATA_SOURCE_LIMIT]}\n\n"
        f"译文节选：\n{translated_text[:_METADATA_TRANSLATION_LIMIT]}"
    )


def _parse_translation_metadata(raw_metadata: str) -> tuple[str | None, str | None]:
    text = raw_metadata.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and start < end:
        text = text[start:end + 1]
    data = json.loads(text)
    if not isinstance(data, dict):
        return None, None
    return (
        _clean_metadata_field(data.get("translated_title")),
        _clean_metadata_field(data.get("summary")),
    )


def _clean_metadata_field(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


_TERMINAL_STATUSES = {
    JobStatus.COMPLETED,
    JobStatus.COMPLETED_WITH_ERRORS,
    JobStatus.FAILED,
    JobStatus.STOPPED,
}


def _chapter_segments(source_text: str, segment_limit: int) -> list[str]:
    return [
        segment.strip()
        for segment in split_text(source_text, segment_limit)
        if segment.strip()
    ]
