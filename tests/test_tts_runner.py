from __future__ import annotations

from pathlib import Path

import pytest

from ebook_to_audio.job_runner import JobRunner
from ebook_to_audio.models import JobStatus, SegmentStatus
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


@pytest.mark.asyncio
async def test_tts_runner_writes_segments_and_merged_chapter(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    repo.initialize()
    storage = LocalStorage(tmp_path)
    book = repo.create_book("Title", "txt", "book.txt", "source.txt", "source.txt", "cleaned.txt")
    chapter_path = storage.write_text("books/1/chapters/0000.txt", "一二三四五六七八九十")
    chapter = repo.create_chapter(book.id, 0, "第一章", str(chapter_path.relative_to(tmp_path)), 10, 1)
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
    book = repo.create_book("Title", "txt", "book.txt", "source.txt", "source.txt", "cleaned.txt")
    chapter_path = storage.write_text("books/1/chapters/0000.txt", "一二三四")
    chapter = repo.create_chapter(book.id, 0, "第一章", str(chapter_path.relative_to(tmp_path)), 4, 1)
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
