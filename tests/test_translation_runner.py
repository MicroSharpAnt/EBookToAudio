from pathlib import Path
import asyncio

import pytest

from ebook_to_audio.config import PromptConfig, ProviderConfig, TranslationConfig
from ebook_to_audio.job_runner import JobRunner
from ebook_to_audio.models import JobKind, JobStatus, SegmentStatus
from ebook_to_audio.repository import Repository
from ebook_to_audio.storage import LocalStorage


class FakeLLMClient:
    def __init__(self):
        self.calls = []

    async def translate(
        self,
        provider,
        system_prompt,
        user_prompt,
        timeout_seconds,
        max_retries,
    ):
        self.calls.append(user_prompt)
        return "译文:" + user_prompt.split("Source:\n", 1)[1]


class FailingLLMClient(FakeLLMClient):
    def __init__(self, failing_source: str):
        super().__init__()
        self.failing_source = failing_source

    async def translate(
        self,
        provider,
        system_prompt,
        user_prompt,
        timeout_seconds,
        max_retries,
    ):
        self.calls.append(user_prompt)
        source_text = user_prompt.split("Source:\n", 1)[1]
        if source_text == self.failing_source:
            raise RuntimeError("translation failed")
        return "译文:" + source_text


class BlockingLLMClient(FakeLLMClient):
    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def translate(
        self,
        provider,
        system_prompt,
        user_prompt,
        timeout_seconds,
        max_retries,
    ):
        self.calls.append(user_prompt)
        self.started.set()
        await self.release.wait()
        return "译文:" + user_prompt.split("Source:\n", 1)[1]


class FailingSegmentStorage(LocalStorage):
    def write_text(self, relative_path: str, text: str) -> Path:
        if relative_path.endswith("translations/0000-0000.txt"):
            raise OSError("segment write failed")
        return super().write_text(relative_path, text)


class FailingAggregateStorage(LocalStorage):
    def write_text(self, relative_path: str, text: str) -> Path:
        if relative_path.endswith("translations/0000.txt"):
            raise OSError("aggregate write failed")
        return super().write_text(relative_path, text)


def _translation_config(segment_limit: int) -> TranslationConfig:
    return TranslationConfig(
        segment_limit=segment_limit,
        request_timeout_seconds=5,
        max_retries=1,
        prompt=PromptConfig("system", "Source:\n{source_text}"),
        providers={
            "deepseek": ProviderConfig("https://api.deepseek.com", "sk", "model")
        },
        active_provider_name="deepseek",
    )


def _create_chapter(
    repo: Repository,
    storage: LocalStorage,
    text: str,
    tmp_path: Path,
):
    book = repo.create_book(
        "Title",
        "txt",
        "book.txt",
        "source.txt",
        "source.txt",
        "cleaned.txt",
    )
    chapter_path = storage.write_text("books/1/chapters/0000.txt", text)
    return repo.create_chapter(
        book.id,
        0,
        "第一章",
        str(chapter_path.relative_to(tmp_path)),
        len(text),
        1,
    )


@pytest.mark.asyncio
async def test_translation_runner_writes_ordered_translation(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    repo.initialize()
    storage = LocalStorage(tmp_path)
    chapter = _create_chapter(
        repo,
        storage,
        "段落一。\n\n段落二。",
        tmp_path,
    )
    config = _translation_config(segment_limit=5)
    runner = JobRunner(repo, storage, llm_client=FakeLLMClient())

    job = await runner.start_translation(chapter.id, config, parallel_segments=2)

    completed = repo.get_job(job.id)
    assert completed.status == JobStatus.COMPLETED
    segments = repo.list_segments(job.id)
    assert [segment.result_text for segment in segments] == ["译文:段落一。", "译文:段落二。"]
    assert segments[0].output_path is not None
    assert (
        storage.resolve_artifact(segments[0].output_path).read_text(encoding="utf-8")
        == "译文:段落一。"
    )
    translated_chapter = repo.get_chapter(chapter.id)
    assert translated_chapter.translation_path is not None
    assert (
        storage.resolve_artifact(translated_chapter.translation_path).read_text(
            encoding="utf-8"
        )
        == "译文:段落一。\n\n译文:段落二。"
    )


@pytest.mark.asyncio
async def test_translation_runner_can_pause_and_resume(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    repo.initialize()
    storage = LocalStorage(tmp_path)
    chapter = _create_chapter(repo, storage, "一二三四五六七八九十", tmp_path)
    config = _translation_config(segment_limit=2)
    runner = JobRunner(repo, storage, llm_client=FakeLLMClient())
    job = repo.create_job(chapter.book_id, chapter.id, JobKind.TRANSLATE, 5, {})
    repo.request_pause(job.id)

    await runner.run_translation_job(job.id, config, parallel_segments=1)

    assert repo.get_job(job.id).status == JobStatus.PAUSED


@pytest.mark.asyncio
async def test_translation_runner_fails_empty_chapter(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    repo.initialize()
    storage = LocalStorage(tmp_path)
    chapter = _create_chapter(repo, storage, " \n\n ", tmp_path)
    runner = JobRunner(repo, storage, llm_client=FakeLLMClient())

    job = await runner.start_translation(
        chapter.id,
        _translation_config(segment_limit=5),
        parallel_segments=1,
    )

    failed = repo.get_job(job.id)
    assert failed.status == JobStatus.FAILED
    assert failed.error_message == "chapter has no translatable text"
    assert repo.list_segments(job.id) == []


@pytest.mark.asyncio
async def test_translation_runner_marks_post_acquire_errors_failed(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    repo.initialize()
    storage = FailingSegmentStorage(tmp_path)
    chapter = _create_chapter(repo, storage, "一二三四", tmp_path)
    runner = JobRunner(repo, storage, llm_client=FakeLLMClient())

    job = await runner.start_translation(
        chapter.id,
        _translation_config(segment_limit=2),
        parallel_segments=1,
    )

    failed = repo.get_job(job.id)
    segments = repo.list_segments(job.id)
    assert failed.status == JobStatus.COMPLETED_WITH_ERRORS
    assert segments[0].status == SegmentStatus.FAILED
    assert segments[0].error_message == "segment write failed"
    assert segments[0].result_text is None
    assert segments[0].output_path is None
    assert segments[1].status == SegmentStatus.COMPLETED
    assert repo.get_chapter(chapter.id).translation_path is None


def test_repository_fail_segment_clears_stale_translation_output(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    repo.initialize()
    storage = LocalStorage(tmp_path)
    chapter = _create_chapter(repo, storage, "一二", tmp_path)
    job = repo.create_job(chapter.book_id, chapter.id, JobKind.TRANSLATE, 1, {})
    segment = repo.create_segments(job.id, chapter.id, ["一二"])[0]
    repo.complete_segment(
        segment.id,
        result_text="译文:一二",
        output_path="books/1/translations/0000-0000.txt",
    )

    repo.fail_segment(segment.id, "retry failed")

    failed_segment = repo.list_segments(job.id)[0]
    assert failed_segment.status == SegmentStatus.FAILED
    assert failed_segment.result_text is None
    assert failed_segment.output_path is None
    assert failed_segment.error_message == "retry failed"


@pytest.mark.asyncio
async def test_translation_runner_suppresses_aggregate_for_partial_failure(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    repo.initialize()
    storage = LocalStorage(tmp_path)
    chapter = _create_chapter(repo, storage, "一二三四", tmp_path)
    runner = JobRunner(repo, storage, llm_client=FailingLLMClient("三四"))

    job = await runner.start_translation(
        chapter.id,
        _translation_config(segment_limit=2),
        parallel_segments=1,
    )

    completed_with_errors = repo.get_job(job.id)
    assert completed_with_errors.status == JobStatus.COMPLETED_WITH_ERRORS
    assert [segment.status for segment in repo.list_segments(job.id)] == [
        SegmentStatus.COMPLETED,
        SegmentStatus.FAILED,
    ]
    assert repo.get_chapter(chapter.id).translation_path is None


@pytest.mark.asyncio
async def test_translation_runner_marks_aggregate_write_failure_failed(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    repo.initialize()
    storage = FailingAggregateStorage(tmp_path)
    chapter = _create_chapter(repo, storage, "一二三四", tmp_path)
    runner = JobRunner(repo, storage, llm_client=FakeLLMClient())

    job = await runner.start_translation(
        chapter.id,
        _translation_config(segment_limit=2),
        parallel_segments=1,
    )

    failed = repo.get_job(job.id)
    assert failed.status == JobStatus.FAILED
    assert failed.error_message == "aggregate write failed"
    assert repo.get_chapter(chapter.id).translation_path is None


@pytest.mark.asyncio
async def test_translation_runner_stops_starting_segments_after_pause(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    repo.initialize()
    storage = LocalStorage(tmp_path)
    chapter = _create_chapter(repo, storage, "一二三四五六", tmp_path)
    config = _translation_config(segment_limit=2)
    runner = JobRunner(repo, storage, llm_client=FakeLLMClient())
    job = repo.create_job(chapter.book_id, chapter.id, JobKind.TRANSLATE, 3, {})
    repo.create_segments(job.id, chapter.id, ["一二", "三四", "五六"])
    first_segment = repo.acquire_next_pending_segment(job.id)
    assert first_segment is not None
    repo.request_pause(job.id)
    repo.complete_segment(first_segment.id, result_text="译文:一二")

    await runner.run_translation_job(job.id, config, parallel_segments=1)

    assert repo.get_job(job.id).status == JobStatus.PAUSED
    assert [segment.status for segment in repo.list_segments(job.id)] == [
        SegmentStatus.COMPLETED,
        SegmentStatus.PENDING,
        SegmentStatus.PENDING,
    ]


@pytest.mark.asyncio
async def test_translation_runner_stops_starting_segments_after_stop(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    repo.initialize()
    storage = LocalStorage(tmp_path)
    chapter = _create_chapter(repo, storage, "一二三四五六", tmp_path)
    config = _translation_config(segment_limit=2)
    runner = JobRunner(repo, storage, llm_client=FakeLLMClient())
    job = repo.create_job(chapter.book_id, chapter.id, JobKind.TRANSLATE, 3, {})
    repo.create_segments(job.id, chapter.id, ["一二", "三四", "五六"])
    first_segment = repo.acquire_next_pending_segment(job.id)
    assert first_segment is not None
    repo.request_stop(job.id)
    repo.complete_segment(first_segment.id, result_text="译文:一二")

    await runner.run_translation_job(job.id, config, parallel_segments=1)

    assert repo.get_job(job.id).status == JobStatus.STOPPED
    assert [segment.status for segment in repo.list_segments(job.id)] == [
        SegmentStatus.COMPLETED,
        SegmentStatus.PENDING,
        SegmentStatus.PENDING,
    ]


@pytest.mark.asyncio
async def test_translation_runner_does_not_complete_active_segment_after_stop(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    repo.initialize()
    storage = LocalStorage(tmp_path)
    chapter = _create_chapter(repo, storage, "一二三四", tmp_path)
    config = _translation_config(segment_limit=4)
    llm = BlockingLLMClient()
    runner = JobRunner(repo, storage, llm_client=llm)
    job = repo.create_job(chapter.book_id, chapter.id, JobKind.TRANSLATE, 1, {})
    repo.create_segments(job.id, chapter.id, ["一二三四"])

    task = asyncio.create_task(runner.run_translation_job(job.id, config, parallel_segments=1))
    await asyncio.wait_for(llm.started.wait(), 5)
    repo.request_stop(job.id)
    llm.release.set()

    await task

    stopped = repo.get_job(job.id)
    segment = repo.list_segments(job.id)[0]
    assert stopped.status == JobStatus.STOPPED
    assert segment.status == SegmentStatus.STOPPED
    assert segment.output_path is None
    assert repo.get_chapter(chapter.id).translation_path is None
