from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

from .audio_builder import AudioBuilder
from .config import TranslationConfig
from .llm_client import LLMClient
from .models import Job, JobKind, JobStatus, SegmentStatus
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
            {"parallel_segments": parallel_segments},
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
        self._write_ordered_translation(job_id)
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

    def _write_ordered_translation(self, job_id: int) -> None:
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
        output_path = (
            f"books/{chapter.book_id}/translations/{chapter.chapter_index:04d}.txt"
        )
        try:
            self.storage.write_text(
                output_path,
                "\n\n".join(segment.result_text or "" for segment in segments),
            )
            self.repository.update_chapter_translation_path(chapter.id, output_path)
        except Exception as exc:
            self.repository.fail_job(job_id, str(exc))

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
            },
        )
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
    ) -> Job:
        if self.tts_client is None:
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
                self._synthesize_pending_segments(job_id, voice, context)
                for _ in range(worker_count)
            )
        )
        if merge:
            self.merge_chapter_audio(job_id)
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
                    extension="wav",
                )
                await asyncio.to_thread(
                    self.tts_client.synthesize,
                    segment.source_text,
                    voice,
                    context,
                    self.storage.resolve_artifact(output_path),
                )
                self.repository.complete_segment(
                    segment.id,
                    output_path=output_path,
                )
            except Exception as exc:
                self.repository.fail_segment(segment.id, str(exc))

    def merge_chapter_audio(self, job_id: int) -> Path | None:
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
        output_path = f"books/{chapter.book_id}/audio/{chapter.chapter_index:04d}/chapter.wav"
        try:
            merged = self.audio_builder.merge_audio(
                input_paths,
                self.storage.resolve_artifact(output_path),
            )
            if merged is None:
                return None
            self.repository.update_chapter_audio_path(chapter.id, output_path)
            return merged
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
