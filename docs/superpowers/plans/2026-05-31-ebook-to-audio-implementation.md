# EBookToAudio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and push a local single-book EPUB/TXT to cleaned TXT, Chinese translation, and MiMo TTS audio workstation.

**Architecture:** Create one independent FastAPI app package, `ebook_to_audio`, with deterministic text services, SQLite metadata, file-based artifacts, async schedulers for translation/TTS, and a static single-page workstation UI. Reuse behavior from the two reference projects, but keep one unified data model and one API surface.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, SQLite, PyYAML, httpx, OpenAI SDK for MiMo TTS, BeautifulSoup, ebooklib, pytest, pytest-asyncio, vanilla HTML/CSS/JS.

---

## File Structure

- Create `pyproject.toml`: package metadata, dependencies, pytest config, console script.
- Create `README.md`: setup, config, run, test, and push notes.
- Create `config.example.yaml`: DeepSeek/MiMo translation providers and MiMo TTS defaults.
- Create `src/ebook_to_audio/__init__.py`: package marker.
- Create `src/ebook_to_audio/models.py`: enums and dataclasses for books, chapters, jobs, and segments.
- Create `src/ebook_to_audio/config.py`: YAML/env config loader and safe config metadata.
- Create `src/ebook_to_audio/book_parser.py`: EPUB/TXT import and full-text conversion.
- Create `src/ebook_to_audio/text_cleaner.py`: watermark, whitespace, repeated-noise, and decorative-character cleaning.
- Create `src/ebook_to_audio/chapter_splitter.py`: heading-based and fallback chapter splitting.
- Create `src/ebook_to_audio/text_segmenter.py`: shared paragraph-aware segmenting for translation/TTS.
- Create `src/ebook_to_audio/repository.py`: SQLite schema and persistence methods.
- Create `src/ebook_to_audio/storage.py`: safe runtime file path management, zip helpers, metadata recalculation.
- Create `src/ebook_to_audio/llm_client.py`: OpenAI-compatible chat completion translation client.
- Create `src/ebook_to_audio/mimo_client.py`: MiMo TTS client adapted from the reference project.
- Create `src/ebook_to_audio/audio_builder.py`: ffmpeg merge and zip helpers.
- Create `src/ebook_to_audio/job_runner.py`: async translation/TTS scheduling, progress, pause/resume/stop.
- Create `src/ebook_to_audio/web.py`: FastAPI routes and static mounting.
- Create `src/ebook_to_audio/static/index.html`: single-page workstation shell.
- Create `src/ebook_to_audio/static/styles.css`: dense utilitarian layout.
- Create `src/ebook_to_audio/static/app.js`: upload, polling, chapter actions, editor, downloads.
- Create tests under `tests/` matching the task names below.

---

### Task 1: Project Scaffold And Config

**Files:**
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `config.example.yaml`
- Create: `src/ebook_to_audio/__init__.py`
- Create: `src/ebook_to_audio/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing config tests**

Create `tests/test_config.py`:

```python
from pathlib import Path

import pytest

from ebook_to_audio.config import ConfigError, load_config


def test_load_config_reads_translation_and_tts_sections(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
active_translation_provider: deepseek
data_dir: data
translation:
  segment_limit: 1200
  request_timeout_seconds: 45
  max_retries: 2
  prompt:
    system: "translator"
    user_template: "Translate: {source_text}"
  providers:
    deepseek:
      base_url: "https://api.deepseek.com"
      api_key: "sk-test"
      model: "deepseek-chat"
tts:
  base_url: "https://token-plan-cn.xiaomimimo.com/v1"
  api_key: "mimo-key"
  model: "mimo-audio"
  default_voice: "Cherry"
  max_request_chars: 900
  default_parallel_segments: 2
limits:
  max_upload_bytes: 1000000
  max_parallel_translation_segments: 3
  max_parallel_tts_segments: 4
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.active_translation_provider == "deepseek"
    assert config.translation.active.model == "deepseek-chat"
    assert config.translation.prompt.user_template == "Translate: {source_text}"
    assert config.tts.default_voice == "Cherry"
    assert config.limits.max_parallel_tts_segments == 4
    assert config.safe_metadata()["translation"]["providers"]["deepseek"]["has_api_key"] is True


def test_load_config_requires_source_text_placeholder(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
active_translation_provider: deepseek
translation:
  prompt:
    user_template: "Translate this"
  providers:
    deepseek:
      base_url: "https://api.deepseek.com"
      api_key: "sk-test"
      model: "deepseek-chat"
tts:
  base_url: "https://token-plan-cn.xiaomimimo.com/v1"
  api_key: "mimo-key"
  model: "mimo-audio"
  default_voice: "Cherry"
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="source_text"):
        load_config(config_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py -v`

Expected: FAIL during import with `ModuleNotFoundError: No module named 'ebook_to_audio'`.

- [ ] **Step 3: Add the scaffold and config implementation**

Create `pyproject.toml` with package name `ebook-to-audio`, Python `>=3.12`, dependencies `beautifulsoup4`, `ebooklib`, `fastapi`, `httpx`, `openai`, `python-multipart`, `pyyaml`, and `uvicorn[standard]`, dev dependencies `pytest` and `pytest-asyncio`, package data for `static/*`, and pytest `pythonpath = ["src"]`.

Create `src/ebook_to_audio/config.py` with dataclasses `ProviderConfig`, `PromptConfig`, `TranslationConfig`, `TTSConfig`, `LimitsConfig`, `AppConfig`, `ConfigError`, and `load_config(path: Path) -> AppConfig`. Use defaults when optional keys are absent. Validate non-empty text, positive integers, active provider existence, and `{source_text}` in `translation.prompt.user_template`. Implement `safe_metadata()` without returning API keys.

Create `config.example.yaml` matching the test shape with DeepSeek and MiMo provider examples.

Create `README.md` with setup, config copy, run command, test command, and note that `config.yaml` and `data/` are ignored.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml README.md config.example.yaml src/ebook_to_audio/__init__.py src/ebook_to_audio/config.py tests/test_config.py
git commit -m "feat: scaffold app config"
```

---

### Task 2: Book Parsing And Full TXT Conversion

**Files:**
- Create: `src/ebook_to_audio/book_parser.py`
- Test: `tests/test_book_parser.py`

- [ ] **Step 1: Write the failing parser tests**

Create `tests/test_book_parser.py`:

```python
from pathlib import Path

import pytest

from ebook_to_audio.book_parser import ParseError, ParsedChapter, parse_book_bytes


def test_parse_txt_normalizes_bom_and_line_endings():
    parsed = parse_book_bytes("sample.txt", "\ufeff第一章\r\n正文\r\n\r\n第二行".encode("utf-8"))

    assert parsed.title == "sample"
    assert parsed.source_format == "txt"
    assert parsed.full_text == "第一章\n正文\n\n第二行"
    assert parsed.initial_chapters == [ParsedChapter(title="sample", text="第一章\n正文\n\n第二行")]


def test_parse_rejects_unsupported_extension():
    with pytest.raises(ParseError, match="Unsupported file type"):
        parse_book_bytes("sample.pdf", b"%PDF")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_book_parser.py -v`

Expected: FAIL with missing module or missing `parse_book_bytes`.

- [ ] **Step 3: Implement TXT parsing and EPUB skeleton**

Create `ParsedChapter` and `ParsedBook` frozen dataclasses. Implement `parse_book_bytes(filename: str, content: bytes) -> ParsedBook`, `parse_txt`, and `parse_epub`. TXT should decode with `utf-8-sig`, normalize `\r\n` and `\r`, trim only surrounding whitespace, and reject empty content. EPUB should use `ebooklib.epub.read_epub` from a temporary file, follow spine order, skip non-document items and navigation documents, extract text with BeautifulSoup, and return initial chapters from the EPUB documents plus a full text joined by blank lines.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_book_parser.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ebook_to_audio/book_parser.py tests/test_book_parser.py
git commit -m "feat: parse ebook uploads"
```

---

### Task 3: Text Cleaning Services

**Files:**
- Create: `src/ebook_to_audio/text_cleaner.py`
- Test: `tests/test_text_cleaner.py`

- [ ] **Step 1: Write the failing cleaner tests**

Create `tests/test_text_cleaner.py`:

```python
from ebook_to_audio.text_cleaner import (
    clean_text,
    normalize_spacing,
    remove_decorative_characters,
    remove_repeated_noise_lines,
    remove_watermarks,
)


def test_remove_watermarks_removes_common_source_lines():
    text = "第一章\n本书来自 www.example.com\n正文\n扫码关注公众号\n下一段"

    result = remove_watermarks(text)

    assert result.text == "第一章\n正文\n下一段"
    assert result.removed_lines == 2


def test_normalize_spacing_collapses_blank_lines_and_invisible_chars():
    result = normalize_spacing("第一章\u200b　　正文\t\t内容\n\n\n\n第二段  结尾  ")

    assert result.text == "第一章 正文 内容\n\n第二段 结尾"
    assert result.after_chars < result.before_chars


def test_remove_repeated_noise_lines_only_removes_short_repeated_noise():
    text = "广告发布页\n正文一\n广告发布页\n正文二\n广告发布页\n正文三"

    result = remove_repeated_noise_lines(text, min_repeats=3)

    assert result.text == "正文一\n正文二\n正文三"
    assert result.removed_lines == 3


def test_remove_decorative_characters_removes_separator_lines():
    result = remove_decorative_characters("正文\n**************\n----------\n下一段")

    assert result.text == "正文\n下一段"


def test_clean_text_applies_selected_operations():
    result = clean_text("水印：www.test.com\n正文\u200b  内容", ["remove_watermarks", "normalize_spacing"])

    assert result.text == "正文 内容"
    assert [item.operation for item in result.results] == ["remove_watermarks", "normalize_spacing"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_text_cleaner.py -v`

Expected: FAIL with missing module or missing cleaner functions.

- [ ] **Step 3: Implement deterministic cleaning**

Create dataclasses `CleanResult(operation, text, before_chars, after_chars, removed_lines)` and `CombinedCleanResult(text, results)`. Implement `remove_watermarks`, `normalize_spacing`, `remove_repeated_noise_lines`, `remove_decorative_characters`, and `clean_text`. Keep rules conservative and testable: remove full lines for watermark/noise, normalize whitespace without joining paragraphs, and leave ordinary prose intact.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_text_cleaner.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ebook_to_audio/text_cleaner.py tests/test_text_cleaner.py
git commit -m "feat: clean ebook text"
```

---

### Task 4: Chapter Splitting And Text Segmenting

**Files:**
- Create: `src/ebook_to_audio/chapter_splitter.py`
- Create: `src/ebook_to_audio/text_segmenter.py`
- Test: `tests/test_chapter_splitter.py`
- Test: `tests/test_text_segmenter.py`

- [ ] **Step 1: Write failing splitter and segmenter tests**

Create `tests/test_chapter_splitter.py`:

```python
from ebook_to_audio.chapter_splitter import split_into_chapters


def test_split_into_chapters_detects_chinese_headings():
    text = "序\n开头\n第一章 初见\n正文一\n第二章 风波\n正文二"

    chapters = split_into_chapters(text, fallback_chars=100)

    assert [chapter.title for chapter in chapters] == ["序", "第一章 初见", "第二章 风波"]
    assert chapters[1].text == "正文一"


def test_split_into_chapters_falls_back_to_size_chunks():
    chapters = split_into_chapters("甲" * 12, fallback_chars=5)

    assert [chapter.title for chapter in chapters] == ["第 1 段", "第 2 段", "第 3 段"]
    assert [len(chapter.text) for chapter in chapters] == [5, 5, 2]
```

Create `tests/test_text_segmenter.py`:

```python
from ebook_to_audio.text_segmenter import split_text


def test_split_text_prefers_paragraph_boundaries():
    text = "第一段。" * 10 + "\n\n" + "第二段。" * 10

    segments = split_text(text, max_chars=40)

    assert len(segments) >= 2
    assert "".join(segments).replace("\n\n", "") == text.replace("\n\n", "")
    assert all(len(segment) <= 40 for segment in segments)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_chapter_splitter.py tests/test_text_segmenter.py -v`

Expected: FAIL with missing modules or functions.

- [ ] **Step 3: Implement splitting and segmenting**

`chapter_splitter.py` should define `SplitChapter(title, text)` and `split_into_chapters(text, fallback_chars=6000)`. Detect Chinese headings, English numeric headings, Roman numeral chapters, `卷一`, `序`, `前言`, `楔子`, `尾声`, and `后记`. Keep heading text as title and exclude it from chapter body. If fewer than two heading chapters are found, use size fallback.

`text_segmenter.py` should define `split_text(text, max_chars)` that preserves order, prefers blank-line and line boundaries, and hard-splits long paragraphs only when needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_chapter_splitter.py tests/test_text_segmenter.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ebook_to_audio/chapter_splitter.py src/ebook_to_audio/text_segmenter.py tests/test_chapter_splitter.py tests/test_text_segmenter.py
git commit -m "feat: split chapters and text segments"
```

---

### Task 5: Models, Storage, And Repository

**Files:**
- Create: `src/ebook_to_audio/models.py`
- Create: `src/ebook_to_audio/storage.py`
- Create: `src/ebook_to_audio/repository.py`
- Test: `tests/test_repository.py`
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write failing persistence tests**

Create `tests/test_repository.py`:

```python
from pathlib import Path

from ebook_to_audio.models import JobKind, JobStatus, SegmentStatus
from ebook_to_audio.repository import Repository


def test_repository_creates_book_chapters_job_and_segments(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    repo.initialize()

    book = repo.create_book("Title", "txt", "book.txt", "books/1/source.txt", "books/1/source.txt", "books/1/cleaned.txt")
    chapter = repo.create_chapter(book.id, 0, "第一章", "books/1/chapters/0000.txt", 10, 2)
    job = repo.create_job(book.id, chapter.id, JobKind.TRANSLATE, total_units=2, options={"parallel": 2})
    repo.create_segments(job.id, chapter.id, ["a", "b"])

    assert repo.get_book(book.id).title == "Title"
    assert repo.list_chapters(book.id)[0].paragraph_count == 2
    assert repo.get_job(job.id).status == JobStatus.PENDING
    assert [segment.status for segment in repo.list_segments(job.id)] == [SegmentStatus.PENDING, SegmentStatus.PENDING]


def test_repository_pause_resume_stop_flags_are_persisted(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    repo.initialize()
    book = repo.create_book("Title", "txt", "book.txt", "s", "f", "c")
    job = repo.create_job(book.id, None, JobKind.TTS, total_units=1, options={})

    repo.request_pause(job.id)
    assert repo.get_job(job.id).pause_requested is True
    assert repo.get_job(job.id).status == JobStatus.PAUSED

    repo.resume_job(job.id)
    assert repo.get_job(job.id).pause_requested is False
    assert repo.get_job(job.id).status == JobStatus.RUNNING

    repo.request_stop(job.id)
    assert repo.get_job(job.id).stop_requested is True
```

Create `tests/test_storage.py`:

```python
from pathlib import Path

import pytest

from ebook_to_audio.storage import LocalStorage, PathSafetyError, chapter_metadata


def test_storage_rejects_paths_outside_data_dir(tmp_path: Path):
    storage = LocalStorage(tmp_path)

    with pytest.raises(PathSafetyError):
        storage.resolve_artifact("../outside.txt")


def test_chapter_metadata_counts_chars_and_paragraphs():
    metadata = chapter_metadata("第一段\n\n第二段\n第三行")

    assert metadata.char_count == len("第一段第二段第三行")
    assert metadata.paragraph_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_repository.py tests/test_storage.py -v`

Expected: FAIL with missing modules or classes.

- [ ] **Step 3: Implement models, repository, and storage**

`models.py` should define `StrEnum` values for `JobKind(SPLIT, TRANSLATE, TTS)`, `JobStatus(PENDING, RUNNING, PAUSED, COMPLETED, COMPLETED_WITH_ERRORS, FAILED, STOPPED)`, and `SegmentStatus(PENDING, RUNNING, COMPLETED, FAILED, STOPPED)`, plus dataclasses for `Book`, `Chapter`, `Job`, `Segment`, and metadata.

`repository.py` should create SQLite tables for books, chapters, jobs, and segments. Implement create/list/get/update methods used by the tests, atomic next-pending-segment acquisition, progress refresh, pause/resume/stop flag updates, and reset-running-to-pending on startup.

`storage.py` should manage relative artifact paths under `data/`, book directories, chapter/translation/audio paths, safe artifact resolution, UTF-8 text writes, `chapter_metadata`, and zip creation from safe artifact lists.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_repository.py tests/test_storage.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ebook_to_audio/models.py src/ebook_to_audio/repository.py src/ebook_to_audio/storage.py tests/test_repository.py tests/test_storage.py
git commit -m "feat: persist books chapters and jobs"
```

---

### Task 6: Translation Client And Scheduler

**Files:**
- Create: `src/ebook_to_audio/llm_client.py`
- Create: `src/ebook_to_audio/job_runner.py`
- Test: `tests/test_llm_client.py`
- Test: `tests/test_translation_runner.py`

- [ ] **Step 1: Write failing translation tests**

Create `tests/test_llm_client.py`:

```python
import httpx
import pytest

from ebook_to_audio.config import ProviderConfig
from ebook_to_audio.llm_client import LLMClient


@pytest.mark.asyncio
async def test_llm_client_reads_openai_compatible_content():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/chat/completions"
        return httpx.Response(200, json={"choices": [{"message": {"content": "译文"}}]})

    client = LLMClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    result = await client.translate(
        ProviderConfig("deepseek", "https://api.deepseek.com", "sk", "deepseek-chat"),
        "system",
        "user",
        5,
        1,
    )

    assert result == "译文"
    await client.aclose()
```

Create `tests/test_translation_runner.py`:

```python
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

    async def translate(self, provider, system_prompt, user_prompt, timeout_seconds, max_retries):
        self.calls.append(user_prompt)
        return "译文:" + user_prompt.split("Source:\n", 1)[1]


@pytest.mark.asyncio
async def test_translation_runner_writes_ordered_translation(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    repo.initialize()
    storage = LocalStorage(tmp_path)
    book = repo.create_book("Title", "txt", "book.txt", "source.txt", "source.txt", "cleaned.txt")
    chapter_path = storage.write_artifact("books/1/chapters/0000.txt", "段落一。\n\n段落二。")
    chapter = repo.create_chapter(book.id, 0, "第一章", str(chapter_path.relative_to(tmp_path)), 8, 2)
    config = TranslationConfig(
        active_provider="deepseek",
        providers={"deepseek": ProviderConfig("deepseek", "https://api.deepseek.com", "sk", "model")},
        prompt=PromptConfig("system", "Source:\n{source_text}"),
        segment_limit=5,
        request_timeout_seconds=5,
        max_retries=1,
    )
    runner = JobRunner(repo, storage, llm_client=FakeLLMClient())

    job = await runner.start_translation(chapter.id, config, parallel_segments=2)

    completed = repo.get_job(job.id)
    assert completed.status == JobStatus.COMPLETED
    assert storage.resolve_artifact(repo.get_chapter(chapter.id).translation_path).read_text(encoding="utf-8").startswith("译文:")


@pytest.mark.asyncio
async def test_translation_runner_can_pause_and_resume(tmp_path: Path):
    repo = Repository(tmp_path / "app.db")
    repo.initialize()
    storage = LocalStorage(tmp_path)
    book = repo.create_book("Title", "txt", "book.txt", "source.txt", "source.txt", "cleaned.txt")
    chapter_path = storage.write_artifact("books/1/chapters/0000.txt", "一二三四五六七八九十")
    chapter = repo.create_chapter(book.id, 0, "第一章", str(chapter_path.relative_to(tmp_path)), 10, 1)
    config = TranslationConfig(
        active_provider="deepseek",
        providers={"deepseek": ProviderConfig("deepseek", "https://api.deepseek.com", "sk", "model")},
        prompt=PromptConfig("system", "Source:\n{source_text}"),
        segment_limit=2,
        request_timeout_seconds=5,
        max_retries=1,
    )
    runner = JobRunner(repo, storage, llm_client=FakeLLMClient())
    job = repo.create_job(book.id, chapter.id, JobKind.TRANSLATE, 5, {})
    repo.request_pause(job.id)

    await runner.run_translation_job(job.id, config, parallel_segments=1)

    assert repo.get_job(job.id).status == JobStatus.PAUSED
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_llm_client.py tests/test_translation_runner.py -v`

Expected: FAIL with missing `llm_client` or `JobRunner`.

- [ ] **Step 3: Implement translation client and runner**

Adapt the reference `LLMClient` with async `translate`, retry, Retry-After handling, and `aclose`. In `JobRunner`, implement `start_translation(chapter_id, config, parallel_segments, api_key_override=None)` and `run_translation_job(job_id, config, parallel_segments)`. Create segments from current chapter text, acquire pending segments until complete, honor pause and stop flags before submitting new work, write ordered translation output, update chapter translation path, and mark job status.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_llm_client.py tests/test_translation_runner.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ebook_to_audio/llm_client.py src/ebook_to_audio/job_runner.py tests/test_llm_client.py tests/test_translation_runner.py
git commit -m "feat: run chapter translation jobs"
```

---

### Task 7: MiMo TTS And Audio Building

**Files:**
- Create: `src/ebook_to_audio/mimo_client.py`
- Create: `src/ebook_to_audio/audio_builder.py`
- Modify: `src/ebook_to_audio/job_runner.py`
- Test: `tests/test_mimo_client.py`
- Test: `tests/test_audio_builder.py`
- Test: `tests/test_tts_runner.py`

- [ ] **Step 1: Write failing TTS and audio tests**

Create `tests/test_mimo_client.py` with a fake OpenAI client returning base64 WAV data and assert `synthesize` writes bytes beginning with `RIFF`.

Create `tests/test_audio_builder.py` with `AudioBuilder(ffmpeg_path=None)` returning `None` for merge and `build_zip` including safe relative paths.

Create `tests/test_tts_runner.py`:

```python
from pathlib import Path

import pytest

from ebook_to_audio.job_runner import JobRunner
from ebook_to_audio.models import JobStatus
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
    chapter_path = storage.write_artifact("books/1/chapters/0000.txt", "一二三四五六七八九十")
    chapter = repo.create_chapter(book.id, 0, "第一章", str(chapter_path.relative_to(tmp_path)), 10, 1)
    runner = JobRunner(repo, storage, tts_client=FakeTTSClient(), audio_builder=FakeAudioBuilder(), tts_max_chars=3)

    job = await runner.start_tts(chapter.id, voice="Cherry", context="温柔旁白", parallel_segments=2, merge=True)

    completed = repo.get_job(job.id)
    assert completed.status == JobStatus.COMPLETED
    assert repo.get_chapter(chapter.id).audio_path.endswith("chapter.wav")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mimo_client.py tests/test_audio_builder.py tests/test_tts_runner.py -v`

Expected: FAIL with missing modules or missing TTS methods.

- [ ] **Step 3: Implement TTS client, audio builder, and TTS runner**

Copy the robust base64/WAV validation behavior from `mimo_tts_read.mimo_client`. Implement `AudioBuilder.merge_audio` with ffmpeg concat and safe zip creation. Add `start_tts`, `run_tts_job`, and `merge_chapter_audio` to `JobRunner`. Segment current chapter text, synthesize WAV segments, honor pause/resume/stop, store segment output paths, and merge into `chapter.wav` when requested and ffmpeg is available.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mimo_client.py tests/test_audio_builder.py tests/test_tts_runner.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ebook_to_audio/mimo_client.py src/ebook_to_audio/audio_builder.py src/ebook_to_audio/job_runner.py tests/test_mimo_client.py tests/test_audio_builder.py tests/test_tts_runner.py
git commit -m "feat: run mimo tts jobs"
```

---

### Task 8: FastAPI Upload, Clean, Split, Edit, And Download Endpoints

**Files:**
- Create: `src/ebook_to_audio/web.py`
- Test: `tests/test_web_books.py`

- [ ] **Step 1: Write failing web endpoint tests**

Create `tests/test_web_books.py`:

```python
from pathlib import Path

from fastapi.testclient import TestClient

from ebook_to_audio.web import create_app


def test_upload_clean_split_edit_and_download_chapter(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "missing.yaml", autostart_jobs=False)
    client = TestClient(app)

    upload = client.post("/api/books", files={"file": ("book.txt", "第一章\n正文  内容".encode("utf-8"), "text/plain")})
    assert upload.status_code == 200
    book_id = upload.json()["id"]

    clean = client.post(f"/api/books/{book_id}/clean", json={"operations": ["normalize_spacing"]})
    assert clean.status_code == 200
    assert clean.json()["results"][0]["operation"] == "normalize_spacing"

    split = client.post(f"/api/books/{book_id}/split")
    assert split.status_code == 200
    job_id = split.json()["job"]["id"]
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "completed"

    chapters = client.get(f"/api/books/{book_id}/chapters").json()
    chapter_id = chapters[0]["id"]
    update = client.put(f"/api/chapters/{chapter_id}", json={"title": "第一章 修改", "text": "新正文"})
    assert update.status_code == 200
    assert update.json()["char_count"] == 3

    download = client.get(f"/api/chapters/{chapter_id}/download.txt")
    assert download.status_code == 200
    assert "新正文" in download.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_web_books.py -v`

Expected: FAIL with missing `web.py` or missing route.

- [ ] **Step 3: Implement book, clean, split, edit, and text download routes**

`create_app(data_dir=None, config_path=None, autostart_jobs=True)` should initialize repository, storage, config path, clients, and runner. Add upload size guard, `/api/config`, `/api/books`, `/api/books/current`, book detail, full/cleaned text downloads, `/api/books/{id}/clean`, synchronous split job endpoint, job get endpoint, chapter list/get/update, chapter TXT download, and chapter zip download. Use safe file resolution for all downloads.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_web_books.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ebook_to_audio/web.py tests/test_web_books.py
git commit -m "feat: expose book workspace api"
```

---

### Task 9: FastAPI Translation, TTS, Pause, Resume, Stop, And Artifact Endpoints

**Files:**
- Modify: `src/ebook_to_audio/web.py`
- Test: `tests/test_web_jobs.py`

- [ ] **Step 1: Write failing job endpoint tests**

Create `tests/test_web_jobs.py`:

```python
from pathlib import Path

from fastapi.testclient import TestClient

from ebook_to_audio.models import JobStatus
from ebook_to_audio.web import create_app


def _chapter_id(client: TestClient) -> int:
    upload = client.post("/api/books", files={"file": ("book.txt", "第一章\n一二三四五六".encode("utf-8"), "text/plain")})
    book_id = upload.json()["id"]
    client.post(f"/api/books/{book_id}/split")
    return client.get(f"/api/books/{book_id}/chapters").json()[0]["id"]


def test_translate_endpoint_creates_translation_and_download(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml", autostart_jobs=False, use_fake_clients=True)
    client = TestClient(app)
    chapter_id = _chapter_id(client)

    response = client.post(f"/api/chapters/{chapter_id}/translate", json={"parallel_segments": 1})

    assert response.status_code == 200
    assert response.json()["status"] == JobStatus.COMPLETED
    download = client.get(f"/api/chapters/{chapter_id}/translation/download.txt")
    assert download.status_code == 200
    assert "译文" in download.text


def test_tts_endpoint_creates_audio_and_controls_are_idempotent(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml", autostart_jobs=False, use_fake_clients=True)
    client = TestClient(app)
    chapter_id = _chapter_id(client)

    response = client.post(
        f"/api/chapters/{chapter_id}/tts",
        json={"voice": "Cherry", "context": "温柔旁白", "parallel_segments": 1, "merge": True},
    )

    assert response.status_code == 200
    job_id = response.json()["id"]
    assert client.post(f"/api/jobs/{job_id}/pause").status_code == 200
    assert client.post(f"/api/jobs/{job_id}/resume").status_code == 200
    assert client.post(f"/api/jobs/{job_id}/stop").status_code == 200
    audio = client.get(f"/api/chapters/{chapter_id}/audio/download")
    assert audio.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_web_jobs.py -v`

Expected: FAIL with missing routes.

- [ ] **Step 3: Implement job and artifact routes**

Add request models for temporary API key, provider override, parallel counts, voice, context, merge, and source selection. Add background task scheduling when `autostart_jobs=True`; in tests with `autostart_jobs=False`, run jobs synchronously or return created job according to injected runner setup. Add translation routes, TTS routes, pause/resume/stop job routes, translation text download, audio metadata, audio merge, audio download, and translation/audio zip routes.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_web_jobs.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ebook_to_audio/web.py tests/test_web_jobs.py
git commit -m "feat: expose translation and tts api"
```

---

### Task 10: Static Single-Page Workstation

**Files:**
- Create: `src/ebook_to_audio/static/index.html`
- Create: `src/ebook_to_audio/static/styles.css`
- Create: `src/ebook_to_audio/static/app.js`
- Test: `tests/static_app_logic_test.js`
- Test: `tests/test_static_ui.py`

- [ ] **Step 1: Write failing frontend logic tests**

Create `tests/static_app_logic_test.js`:

```javascript
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");

const source = fs.readFileSync("src/ebook_to_audio/static/app.js", "utf8");
const sandbox = { window: {}, document: { addEventListener() {} }, console, setInterval() {}, clearInterval() {} };
vm.createContext(sandbox);
vm.runInContext(source, sandbox);

assert.equal(sandbox.window.EBookToAudio.formatCount(12345), "12,345");
assert.equal(sandbox.window.EBookToAudio.progressPercent({ total_units: 4, completed_units: 1 }), 25);
assert.equal(sandbox.window.EBookToAudio.statusLabel("completed_with_errors"), "部分完成");
```

Create `tests/test_static_ui.py`:

```python
from pathlib import Path


def test_static_ui_contains_required_controls():
    html = Path("src/ebook_to_audio/static/index.html").read_text(encoding="utf-8")

    for label in ["去除文章水印", "去除文章多余空格等字符", "按章节分成多个txt", "将文章翻译为中文"]:
        assert label in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node tests/static_app_logic_test.js && pytest tests/test_static_ui.py -v`

Expected: FAIL because static files are missing.

- [ ] **Step 3: Implement the workstation UI**

Build `index.html` with top upload/config band, global settings, text tools, job progress, chapter list template, and editor modal. Build CSS with dense responsive controls, stable row dimensions, visible progress bars, and no marketing hero. Build JS to call all APIs, poll active jobs, render chapters and metadata, open/edit/save chapter text, start translation/TTS, pause/resume/stop jobs, download artifacts, and expose `window.EBookToAudio` pure helpers for tests.

- [ ] **Step 4: Run tests to verify they pass**

Run: `node tests/static_app_logic_test.js && pytest tests/test_static_ui.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ebook_to_audio/static/index.html src/ebook_to_audio/static/styles.css src/ebook_to_audio/static/app.js tests/static_app_logic_test.js tests/test_static_ui.py
git commit -m "feat: build single book workstation ui"
```

---

### Task 11: Full Verification, Local Smoke Test, Remote Push

**Files:**
- Modify only the file implicated by a failing verification command, then rerun the same command before continuing.

- [ ] **Step 1: Run the full automated suite**

Run: `pytest -v && node tests/static_app_logic_test.js`

Expected: all tests pass.

- [ ] **Step 2: Run a local server smoke test**

Run: `uvicorn "ebook_to_audio.web:create_app" --factory --host 127.0.0.1 --port 8000`

Open `http://127.0.0.1:8000`, upload a tiny TXT, clean spacing, split chapters, edit a chapter, and confirm the chapter TXT download works. Real translation and TTS require valid API keys and do not need to be called during smoke testing.

- [ ] **Step 3: Inspect Git status**

Run: `git status --short --branch`

Expected: clean working tree except for intentionally ignored runtime files.

- [ ] **Step 4: Configure remote**

Run:

```bash
git remote add origin git@github.com:MicroSharpAnt/EBookToAudio.git
```

If `origin` already exists, run:

```bash
git remote set-url origin git@github.com:MicroSharpAnt/EBookToAudio.git
```

- [ ] **Step 5: Push**

Run:

```bash
git push -u origin main
```

Expected: branch `main` pushed to GitHub.

---

## Self-Review

- Spec coverage: import, conversion, cleaning, splitting, chapter edit, metadata, translation, TTS, progress, pause/resume/stop, merge, downloads, config, tests, and push are all covered by tasks.
- Placeholder scan: no task uses open placeholders; every task names concrete files, tests, commands, and expected outcomes.
- Type consistency: job kinds, statuses, config dataclasses, repository methods, and API route names are consistent across tasks.
