from __future__ import annotations

import asyncio
from pathlib import Path
import threading

import pytest

from ebook_to_audio.audio_builder import AudioBuilder
from ebook_to_audio.job_runner import JobRunner
from ebook_to_audio.models import JobKind, JobStatus, SegmentStatus
from ebook_to_audio.repository import Repository
from ebook_to_audio.storage import LocalStorage


class FakeTTSClient:
    def synthesize(self, text, voice, context, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"RIFFxxxxWAVEfake")
        return output_path


class FakeAudioBuilder:
    def merge_audio(self, input_paths, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"RIFFxxxxWAVEmerged")
        return output_path


class BlockingTTSClient:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def synthesize(self, text, voice, context, output_path):
        self.calls += 1
        self.started.set()
        self.release.wait(timeout=5)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"RIFFxxxxWAVEfake")
        return output_path


def _create_chapter(repo: Repository, storage: LocalStorage, tmp_path: Path, text: str):
    book = repo.create_book("Title", "txt", "book.txt", "source.txt", "source.txt", "cleaned.txt")
    chapter_path = storage.write_text("books/1/chapters/0000.txt", text)
    return repo.create_chapter(book.id, 0, "第一章", str(chapter_path.relative_to(tmp_path)), len(text), 1)


@pytest.mark.asyncio
async def test_tts_runner_writes_segments_and_merged_chapter(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    repo.initialize()
    storage = LocalStorage(tmp_path)
    chapter = _create_chapter(repo, storage, tmp_path, "一二三四五六七八九十")
    runner = JobRunner(
        repo,
        storage,
        tts_client=FakeTTSClient(),
        audio_builder=FakeAudioBuilder(),
        tts_max_chars=3,
    )

    job = await runner.start_tts(
        chapter.id,
        voice="Cherry",
        context="温柔旁白",
        parallel_segments=2,
        merge=True,
    )

    completed = repo.get_job(job.id)
    assert completed.status == JobStatus.COMPLETED
    segments = repo.list_segments(job.id)
    assert [segment.status for segment in segments] == [SegmentStatus.COMPLETED] * 4
    assert all(segment.output_path and segment.output_path.endswith(".wav") for segment in segments)
    assert repo.get_chapter(chapter.id).audio_path.endswith("chapter.wav")


@pytest.mark.asyncio
async def test_tts_runner_without_merge_keeps_segment_paths(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    repo.initialize()
    storage = LocalStorage(tmp_path)
    chapter = _create_chapter(repo, storage, tmp_path, "一二三四")
    runner = JobRunner(
        repo,
        storage,
        tts_client=FakeTTSClient(),
        audio_builder=FakeAudioBuilder(),
        tts_max_chars=2,
    )

    job = await runner.start_tts(
        chapter.id,
        voice="Cherry",
        context="温柔旁白",
        parallel_segments=1,
        merge=False,
    )

    assert repo.get_job(job.id).status == JobStatus.COMPLETED
    assert repo.get_chapter(chapter.id).audio_path is None
    assert [segment.output_path for segment in repo.list_segments(job.id)] == [
        "books/1/audio/0000-0000.wav",
        "books/1/audio/0000-0001.wav",
    ]


@pytest.mark.asyncio
async def test_tts_runner_does_not_complete_active_segment_after_pause(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    repo.initialize()
    storage = LocalStorage(tmp_path)
    chapter = _create_chapter(repo, storage, tmp_path, "一二")
    tts = BlockingTTSClient()
    runner = JobRunner(
        repo,
        storage,
        tts_client=tts,
        audio_builder=FakeAudioBuilder(),
        tts_max_chars=2,
    )
    job = repo.create_job(chapter.book_id, chapter.id, JobKind.TTS, total_units=1, options={})
    repo.create_segments(job.id, chapter.id, ["一二"])

    task = asyncio.create_task(
        runner.run_tts_job(job.id, voice="Cherry", context="", parallel_segments=1)
    )
    assert await asyncio.to_thread(tts.started.wait, 5)
    repo.request_pause(job.id)
    tts.release.set()

    await task

    paused = repo.get_job(job.id)
    segment = repo.list_segments(job.id)[0]
    assert paused.status == JobStatus.PAUSED
    assert segment.status == SegmentStatus.PENDING
    assert segment.output_path is None

    repo.resume_job(job.id)
    tts.started.clear()
    tts.release.set()

    resumed = await runner.run_tts_job(
        job.id,
        voice="Cherry",
        context="",
        parallel_segments=1,
        merge=False,
    )

    assert resumed.status == JobStatus.COMPLETED
    assert repo.list_segments(job.id)[0].status == SegmentStatus.COMPLETED
    assert repo.list_segments(job.id)[0].output_path == "books/1/audio/0000-0000.wav"
    assert tts.calls == 2


@pytest.mark.asyncio
async def test_tts_runner_does_not_complete_active_segment_after_stop(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    repo.initialize()
    storage = LocalStorage(tmp_path)
    chapter = _create_chapter(repo, storage, tmp_path, "一二")
    tts = BlockingTTSClient()
    runner = JobRunner(
        repo,
        storage,
        tts_client=tts,
        audio_builder=FakeAudioBuilder(),
        tts_max_chars=2,
    )
    job = repo.create_job(chapter.book_id, chapter.id, JobKind.TTS, total_units=1, options={})
    repo.create_segments(job.id, chapter.id, ["一二"])

    task = asyncio.create_task(
        runner.run_tts_job(job.id, voice="Cherry", context="", parallel_segments=1)
    )
    assert await asyncio.to_thread(tts.started.wait, 5)
    repo.request_stop(job.id)
    tts.release.set()

    await task

    stopped = repo.get_job(job.id)
    segment = repo.list_segments(job.id)[0]
    assert stopped.status == JobStatus.STOPPED
    assert segment.status == SegmentStatus.STOPPED
    assert segment.output_path is None
    assert SegmentStatus.RUNNING not in [
        existing.status for existing in repo.list_segments(job.id)
    ]


@pytest.mark.asyncio
async def test_tts_runner_keeps_completed_job_when_optional_ffmpeg_is_invalid(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    repo.initialize()
    storage = LocalStorage(tmp_path)
    chapter = _create_chapter(repo, storage, tmp_path, "一二三四")
    runner = JobRunner(
        repo,
        storage,
        tts_client=FakeTTSClient(),
        audio_builder=AudioBuilder(ffmpeg_path=str(tmp_path / "missing-ffmpeg")),
        tts_max_chars=2,
    )

    job = await runner.start_tts(
        chapter.id,
        voice="Cherry",
        context="",
        parallel_segments=1,
        merge=True,
    )

    assert repo.get_job(job.id).status == JobStatus.COMPLETED
    assert repo.get_chapter(chapter.id).audio_path is None
