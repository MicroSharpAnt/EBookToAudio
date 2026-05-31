from pathlib import Path

import pytest

from ebook_to_audio.config import PromptConfig, ProviderConfig, TranslationConfig
from ebook_to_audio.job_runner import JobRunner
from ebook_to_audio.models import JobKind, JobStatus
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


@pytest.mark.asyncio
async def test_translation_runner_writes_ordered_translation(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    repo.initialize()
    storage = LocalStorage(tmp_path)
    book = repo.create_book(
        "Title",
        "txt",
        "book.txt",
        "source.txt",
        "source.txt",
        "cleaned.txt",
    )
    chapter_path = storage.write_text("books/1/chapters/0000.txt", "段落一。\n\n段落二。")
    chapter = repo.create_chapter(
        book.id,
        0,
        "第一章",
        str(chapter_path.relative_to(tmp_path)),
        8,
        2,
    )
    config = TranslationConfig(
        segment_limit=5,
        request_timeout_seconds=5,
        max_retries=1,
        prompt=PromptConfig("system", "Source:\n{source_text}"),
        providers={
            "deepseek": ProviderConfig("https://api.deepseek.com", "sk", "model")
        },
        active_provider_name="deepseek",
    )
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
    book = repo.create_book(
        "Title",
        "txt",
        "book.txt",
        "source.txt",
        "source.txt",
        "cleaned.txt",
    )
    chapter_path = storage.write_text("books/1/chapters/0000.txt", "一二三四五六七八九十")
    chapter = repo.create_chapter(
        book.id,
        0,
        "第一章",
        str(chapter_path.relative_to(tmp_path)),
        10,
        1,
    )
    config = TranslationConfig(
        segment_limit=2,
        request_timeout_seconds=5,
        max_retries=1,
        prompt=PromptConfig("system", "Source:\n{source_text}"),
        providers={
            "deepseek": ProviderConfig("https://api.deepseek.com", "sk", "model")
        },
        active_provider_name="deepseek",
    )
    runner = JobRunner(repo, storage, llm_client=FakeLLMClient())
    job = repo.create_job(book.id, chapter.id, JobKind.TRANSLATE, 5, {})
    repo.request_pause(job.id)

    await runner.run_translation_job(job.id, config, parallel_segments=1)

    assert repo.get_job(job.id).status == JobStatus.PAUSED
