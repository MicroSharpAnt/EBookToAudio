# Ximalaya Studio Draft Publishing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a supervised chapter action that opens Ximalaya Studio, uploads the generated chapter audio, fills the draft metadata, and stops before final submission.

**Architecture:** Keep publishing logic behind a small `ximalaya_publisher.py` module with pure draft-building helpers and a Playwright-backed publisher. `web.py` owns request validation and dependency injection through `app.state.ximalaya_publisher`, while `static/app.js` owns the chapter-card button and status messages.

**Tech Stack:** Python 3.12, FastAPI, pytest, Playwright sync API for local browser automation, vanilla JavaScript UI tests.

---

## File Structure

- Create `src/ebook_to_audio/ximalaya_publisher.py`: draft dataclasses, metadata construction, publisher result type, Playwright publisher implementation, and user-facing publisher exceptions.
- Modify `src/ebook_to_audio/web.py`: import publishing helpers, create a default publisher, and add `POST /api/chapters/{chapter_id}/publish/ximalaya/draft`.
- Modify `src/ebook_to_audio/config.py`: keep the existing `PublishingConfig` work, only adjust if tests expose missing behavior.
- Modify `config.example.yaml`: add `publishing.ximalaya` example for album `122326236`.
- Modify `pyproject.toml`: add `playwright` dependency.
- Modify `src/ebook_to_audio/static/app.js`: add publish payload helper, chapter button, click handler, and test exports.
- Modify `tests/test_web_jobs.py`: endpoint tests using fake publishers.
- Modify `tests/test_config.py`: config publishing tests if not already present.
- Modify `tests/static_app_logic_test.js`: render and click behavior tests.

---

### Task 1: Publishing Config Coverage

**Files:**
- Modify: `tests/test_config.py`
- Modify: `config.example.yaml`
- Verify: `src/ebook_to_audio/config.py`

- [ ] **Step 1: Add a failing config test**

Append this test to `tests/test_config.py`:

```python
def test_load_config_reads_publishing_section(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    write_config(
        config_path,
        minimal_config(
            limits_block="""
publishing:
  ximalaya:
    album_id: "122326236"
    default_tags:
      - 有声书
      - 中文文学
    description_footer: "本音频由 EBookToAudio 辅助生成。"
""",
        ),
    )

    config = load_config(config_path)

    assert config.publishing.ximalaya_album_id == "122326236"
    assert config.publishing.default_tags == ("有声书", "中文文学")
    assert config.publishing.description_footer == "本音频由 EBookToAudio 辅助生成。"
    assert config.safe_metadata()["publishing"]["ximalaya"] == {
        "has_album_id": True,
        "default_tags": ["有声书", "中文文学"],
        "has_description_footer": True,
    }
```

- [ ] **Step 2: Run the config test**

Run: `pytest tests/test_config.py::test_load_config_reads_publishing_section -v`

Expected: PASS if the existing local `config.py` publishing changes are complete; otherwise FAIL naming the missing field or parser behavior.

- [ ] **Step 3: Fix config parser only if the test fails**

If the test fails because `PublishingConfig` or `_build_publishing` is missing, add this exact shape to `src/ebook_to_audio/config.py` near the other config dataclasses and builders:

```python
@dataclass(frozen=True)
class PublishingConfig:
    ximalaya_album_id: str = ""
    default_tags: tuple[str, ...] = ()
    description_footer: str = ""
```

Add `publishing: PublishingConfig` to `AppConfig`, include the non-secret safe metadata, call `_build_publishing(...)` from `load_config`, and add:

```python
def _build_publishing(raw: dict[str, Any]) -> PublishingConfig:
    ximalaya_raw = _get_mapping(raw, "ximalaya", "publishing.ximalaya", required=False)
    return PublishingConfig(
        ximalaya_album_id=_get_text(
            ximalaya_raw,
            "album_id",
            "publishing.ximalaya.album_id",
            required=False,
        ),
        default_tags=tuple(
            _get_text_list(
                ximalaya_raw,
                "default_tags",
                "publishing.ximalaya.default_tags",
            )
        ),
        description_footer=_get_text(
            ximalaya_raw,
            "description_footer",
            "publishing.ximalaya.description_footer",
            required=False,
        ),
    )
```

Also add `_get_text_list` if absent:

```python
def _get_text_list(data: dict[str, Any], key: str, path: str) -> list[str]:
    value = data.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(f"{path} must be a list of text values")
    values: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ConfigError(f"{path}[{index}] must be text")
        cleaned = item.strip()
        if cleaned:
            values.append(cleaned)
    return values
```

- [ ] **Step 4: Add example config**

Append to `config.example.yaml`:

```yaml
publishing:
  ximalaya:
    album_id: "122326236"
    default_tags:
      - 有声书
    description_footer: ""
```

- [ ] **Step 5: Run config tests**

Run: `pytest tests/test_config.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add tests/test_config.py src/ebook_to_audio/config.py config.example.yaml
git commit -m "Add Ximalaya publishing config coverage"
```

---

### Task 2: Draft Builder and Publisher Types

**Files:**
- Create: `src/ebook_to_audio/ximalaya_publisher.py`
- Test: `tests/test_ximalaya_publisher.py`

- [ ] **Step 1: Write failing draft-builder tests**

Create `tests/test_ximalaya_publisher.py`:

```python
from pathlib import Path

import pytest

from ebook_to_audio.config import PublishingConfig
from ebook_to_audio.models import Chapter
from ebook_to_audio.ximalaya_publisher import (
    XimalayaDraft,
    XimalayaDraftError,
    build_ximalaya_draft,
)


def chapter(**overrides):
    values = {
        "id": 7,
        "book_id": 3,
        "chapter_index": 0,
        "title": "Chapter One",
        "text_path": "books/3/chapters/0000.txt",
        "char_count": 100,
        "paragraph_count": 3,
        "translated_title": "第一章（中文）",
        "summary": "本章介绍主要人物和故事开端。",
        "tags": ["文学", "有声书"],
        "audio_path": "books/3/audio/0000/jobs/9/chapter.wav",
    }
    values.update(overrides)
    return Chapter(**values)


def test_build_ximalaya_draft_uses_chapter_metadata(tmp_path: Path):
    audio_file = tmp_path / "chapter.wav"
    audio_file.write_bytes(b"RIFF")
    draft = build_ximalaya_draft(
        chapter(),
        PublishingConfig(
            ximalaya_album_id="122326236",
            default_tags=("有声书", "中文文学"),
            description_footer="本音频由 EBookToAudio 辅助生成。",
        ),
        audio_file,
    )

    assert draft == XimalayaDraft(
        album_id="122326236",
        upload_url="https://studio.ximalaya.com/upload?albumId=122326236",
        audio_path=audio_file,
        title="第一章（中文）",
        description="本章介绍主要人物和故事开端。\n\n本音频由 EBookToAudio 辅助生成。",
        tags=("文学", "有声书", "中文文学"),
    )


def test_build_ximalaya_draft_falls_back_to_original_title(tmp_path: Path):
    audio_file = tmp_path / "chapter.wav"
    audio_file.write_bytes(b"RIFF")
    draft = build_ximalaya_draft(
        chapter(translated_title=None, summary=None, tags=[], title=""),
        PublishingConfig(ximalaya_album_id="122326236"),
        audio_file,
    )

    assert draft.title == "第 1 章"
    assert draft.description == ""
    assert draft.tags == ()


def test_build_ximalaya_draft_requires_album_id(tmp_path: Path):
    audio_file = tmp_path / "chapter.wav"
    audio_file.write_bytes(b"RIFF")

    with pytest.raises(XimalayaDraftError, match="publishing.ximalaya.album_id"):
        build_ximalaya_draft(chapter(), PublishingConfig(), audio_file)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ximalaya_publisher.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'ebook_to_audio.ximalaya_publisher'`.

- [ ] **Step 3: Add draft types and builder**

Create `src/ebook_to_audio/ximalaya_publisher.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .config import PublishingConfig
from .models import Chapter


class XimalayaDraftError(ValueError):
    """Raised when a Ximalaya draft cannot be built from local data."""


class XimalayaPublishError(RuntimeError):
    """Raised when browser automation cannot fill the Ximalaya upload form."""


@dataclass(frozen=True)
class XimalayaDraft:
    album_id: str
    upload_url: str
    audio_path: Path
    title: str
    description: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class XimalayaPublishResult:
    status: str
    message: str
    draft: XimalayaDraft


def build_ximalaya_draft(
    chapter: Chapter,
    config: PublishingConfig,
    audio_path: Path,
) -> XimalayaDraft:
    album_id = config.ximalaya_album_id.strip()
    if not album_id:
        raise XimalayaDraftError("请在 config.yaml 中配置 publishing.ximalaya.album_id。")
    title = _draft_title(chapter)
    description = _draft_description(chapter.summary, config.description_footer)
    tags = _dedupe_tags([*chapter.tags, *config.default_tags])
    return XimalayaDraft(
        album_id=album_id,
        upload_url=f"https://studio.ximalaya.com/upload?albumId={album_id}",
        audio_path=audio_path,
        title=title,
        description=description,
        tags=tuple(tags),
    )


def _draft_title(chapter: Chapter) -> str:
    for value in (chapter.translated_title, chapter.title):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"第 {chapter.chapter_index + 1} 章"


def _draft_description(summary: str | None, footer: str) -> str:
    parts = []
    if summary and summary.strip():
        parts.append(summary.strip())
    if footer and footer.strip():
        parts.append(footer.strip())
    return "\n\n".join(parts)


def _dedupe_tags(values: list[str]) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = re.sub(r"\s+", "", str(value).strip().strip("#＃"))
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            tags.append(cleaned)
    return tags
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ximalaya_publisher.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/ebook_to_audio/ximalaya_publisher.py tests/test_ximalaya_publisher.py
git commit -m "Add Ximalaya draft builder"
```

---

### Task 3: Backend Publish Endpoint with Fake Publisher

**Files:**
- Modify: `src/ebook_to_audio/web.py`
- Modify: `tests/test_web_jobs.py`

- [ ] **Step 1: Add failing endpoint tests**

Append to `tests/test_web_jobs.py`:

```python
class FakeXimalayaPublisher:
    def __init__(self):
        self.drafts = []

    def fill_draft(self, draft):
        self.drafts.append(draft)
        return {
            "status": "ready_for_review",
            "message": "喜马拉雅草稿已填写，请在浏览器中确认后手动发布。",
            "album_id": draft.album_id,
            "title": draft.title,
            "description": draft.description,
            "tags": list(draft.tags),
            "upload_url": draft.upload_url,
        }


def test_ximalaya_publish_endpoint_fills_draft_with_fake_publisher(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
active_translation_provider: deepseek
translation:
  providers:
    deepseek:
      base_url: "https://api.deepseek.com"
      api_key: "sk-test"
      model: "deepseek-chat"
tts:
  base_url: "https://token-plan-cn.xiaomimimo.com/v1"
  api_key: "mimo-key"
  model: "mimo-audio"
  default_voice: "茉莉"
publishing:
  ximalaya:
    album_id: "122326236"
    default_tags:
      - 有声书
    description_footer: "页脚"
""",
        encoding="utf-8",
    )
    app = create_app(data_dir=tmp_path, config_path=config_path, autostart_jobs=False, use_fake_clients=True)
    app.state.ximalaya_publisher = FakeXimalayaPublisher()
    client = TestClient(app)
    _, chapter_id = _book_and_chapter_id(client)
    client.post(f"/api/chapters/{chapter_id}/translate", json={"parallel_segments": 1})
    client.post(f"/api/chapters/{chapter_id}/tags", json={"api_key": "sk-tags"})
    client.post(f"/api/chapters/{chapter_id}/tts", json={"voice": "茉莉", "parallel_segments": 1, "merge": True})

    response = client.post(f"/api/chapters/{chapter_id}/publish/ximalaya/draft")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready_for_review"
    assert payload["album_id"] == "122326236"
    assert payload["title"].endswith("（中文）")
    assert "页脚" in payload["description"]
    assert "有声书" in payload["tags"]
    assert app.state.ximalaya_publisher.drafts[0].audio_path.is_file()


def test_ximalaya_publish_endpoint_requires_merged_audio(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml", autostart_jobs=False, use_fake_clients=True)
    app.state.ximalaya_publisher = FakeXimalayaPublisher()
    client = TestClient(app)
    chapter_id = _chapter_id(client)

    response = client.post(f"/api/chapters/{chapter_id}/publish/ximalaya/draft")

    assert response.status_code == 400
    assert response.json()["detail"] == "请先生成并合并章节音频。"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_web_jobs.py::test_ximalaya_publish_endpoint_fills_draft_with_fake_publisher tests/test_web_jobs.py::test_ximalaya_publish_endpoint_requires_merged_audio -v`

Expected: FAIL with 404 because the route does not exist.

- [ ] **Step 3: Add route and helper**

Modify `src/ebook_to_audio/web.py` imports:

```python
from .ximalaya_publisher import (
    PlaywrightXimalayaPublisher,
    XimalayaDraftError,
    XimalayaPublishError,
    build_ximalaya_draft,
)
```

In `create_app`, after `app.state.runner = runner`, add:

```python
    app.state.ximalaya_publisher = PlaywrightXimalayaPublisher()
```

Add this route after `chapter_audio_metadata`:

```python
    @app.post("/api/chapters/{chapter_id}/publish/ximalaya/draft")
    def publish_ximalaya_draft(chapter_id: int) -> dict[str, Any]:
        chapter = _get_chapter_or_404(repository, chapter_id)
        if chapter.audio_path is None:
            raise HTTPException(status_code=400, detail="请先生成并合并章节音频。")
        try:
            audio_path = storage.resolve_artifact(chapter.audio_path)
            if not audio_path.is_file():
                raise FileNotFoundError(chapter.audio_path)
            draft = build_ximalaya_draft(
                chapter,
                loaded_config.publishing,
                audio_path,
            )
            result = app.state.ximalaya_publisher.fill_draft(draft)
        except XimalayaDraftError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="audio not found") from exc
        except (OSError, PathSafetyError) as exc:
            raise HTTPException(status_code=404, detail="artifact not found") from exc
        except XimalayaPublishError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        if isinstance(result, dict):
            return result
        return {
            "status": result.status,
            "message": result.message,
            "album_id": result.draft.album_id,
            "title": result.draft.title,
            "description": result.draft.description,
            "tags": list(result.draft.tags),
            "upload_url": result.draft.upload_url,
        }
```

- [ ] **Step 4: Add temporary publisher class stub**

At the bottom of `src/ebook_to_audio/ximalaya_publisher.py`, add:

```python
class PlaywrightXimalayaPublisher:
    def fill_draft(self, draft: XimalayaDraft) -> XimalayaPublishResult:
        raise XimalayaPublishError("Playwright publisher is not implemented yet.")
```

- [ ] **Step 5: Run endpoint tests**

Run: `pytest tests/test_web_jobs.py::test_ximalaya_publish_endpoint_fills_draft_with_fake_publisher tests/test_web_jobs.py::test_ximalaya_publish_endpoint_requires_merged_audio -v`

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add src/ebook_to_audio/web.py src/ebook_to_audio/ximalaya_publisher.py tests/test_web_jobs.py
git commit -m "Add Ximalaya draft publish endpoint"
```

---

### Task 4: Playwright Publisher Implementation

**Files:**
- Modify: `src/ebook_to_audio/ximalaya_publisher.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Add Playwright dependency**

In `pyproject.toml`, add `"playwright",` to `[project].dependencies`.

- [ ] **Step 2: Implement supervised Playwright publisher**

Replace the `PlaywrightXimalayaPublisher` stub in `src/ebook_to_audio/ximalaya_publisher.py` with:

```python
class PlaywrightXimalayaPublisher:
    def __init__(self, user_data_dir: Path | None = None, timeout_ms: int = 120_000):
        self.user_data_dir = user_data_dir or Path.home() / ".ebook-to-audio" / "ximalaya-browser"
        self.timeout_ms = timeout_ms

    def fill_draft(self, draft: XimalayaDraft) -> XimalayaPublishResult:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise XimalayaPublishError(
                "缺少 Playwright 运行库。请运行 pip install -e \".[dev]\" 后执行 playwright install chromium。"
            ) from exc

        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        try:
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    str(self.user_data_dir),
                    headless=False,
                    accept_downloads=True,
                )
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(draft.upload_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                _set_file_input(page, draft.audio_path, self.timeout_ms)
                _fill_first_available(page, ["标题", "声音标题", "请输入标题"], draft.title, self.timeout_ms)
                if draft.description:
                    _fill_first_available(page, ["简介", "声音简介", "请输入简介"], draft.description, self.timeout_ms)
                if draft.tags:
                    _fill_tags(page, draft.tags, self.timeout_ms)
                return XimalayaPublishResult(
                    status="ready_for_review",
                    message="喜马拉雅草稿已填写，请在浏览器中确认后手动发布。",
                    draft=draft,
                )
        except PlaywrightError as exc:
            raise XimalayaPublishError(f"喜马拉雅页面自动填写失败：{exc}") from exc


def _set_file_input(page, audio_path: Path, timeout_ms: int) -> None:
    file_inputs = page.locator("input[type='file']")
    if file_inputs.count() > 0:
        file_inputs.first.set_input_files(str(audio_path), timeout=timeout_ms)
        return
    for text in ("上传", "选择文件", "上传声音"):
        button = page.get_by_text(text, exact=False)
        if button.count() > 0:
            button.first.click(timeout=timeout_ms)
            page.locator("input[type='file']").first.set_input_files(str(audio_path), timeout=timeout_ms)
            return
    raise XimalayaPublishError("未找到音频上传控件，请登录后重试或检查上传页是否改版。")


def _fill_first_available(page, labels: list[str], value: str, timeout_ms: int) -> None:
    for label in labels:
        candidates = [
            page.get_by_label(label, exact=False),
            page.get_by_placeholder(label, exact=False),
            page.locator(f"input[placeholder*='{label}']"),
            page.locator(f"textarea[placeholder*='{label}']"),
        ]
        for candidate in candidates:
            if candidate.count() > 0:
                target = candidate.first
                target.fill(value, timeout=timeout_ms)
                return
    raise XimalayaPublishError(f"未找到字段：{labels[0]}。请检查喜马拉雅上传页是否改版。")


def _fill_tags(page, tags: tuple[str, ...], timeout_ms: int) -> None:
    tag_text = " ".join(tags)
    for label in ("标签", "声音标签", "请输入标签"):
        candidates = [
            page.get_by_label(label, exact=False),
            page.get_by_placeholder(label, exact=False),
            page.locator(f"input[placeholder*='{label}']"),
        ]
        for candidate in candidates:
            if candidate.count() > 0:
                target = candidate.first
                target.fill(tag_text, timeout=timeout_ms)
                try:
                    target.press("Enter", timeout=timeout_ms)
                except Exception:
                    pass
                return
    raise XimalayaPublishError("未找到字段：标签。请检查喜马拉雅上传页是否改版。")
```

- [ ] **Step 3: Run non-browser tests**

Run: `pytest tests/test_ximalaya_publisher.py tests/test_web_jobs.py::test_ximalaya_publish_endpoint_fills_draft_with_fake_publisher -v`

Expected: PASS. These tests use fake publishing or pure draft building and do not open Ximalaya.

- [ ] **Step 4: Commit**

Run:

```bash
git add pyproject.toml src/ebook_to_audio/ximalaya_publisher.py
git commit -m "Add Playwright Ximalaya draft publisher"
```

---

### Task 5: Frontend Publish Action

**Files:**
- Modify: `src/ebook_to_audio/static/app.js`
- Modify: `tests/static_app_logic_test.js`

- [ ] **Step 1: Add failing static tests**

Append to `tests/static_app_logic_test.js`:

```javascript
const publishChapter = { id: 42, audio_path: "books/1/audio/chapter.wav", translated_title: "第一章" };
sandbox.window.EBookToAudio.renderChaptersForTest({
  book: { id: 1 },
  chapters: [publishChapter],
  audio: new Map([[42, { download_url: "/api/chapters/42/audio/download", segments: [] }]]),
});
assert(sandbox.document.querySelector("[data-publish-ximalaya='42']"));

const noAudioChapter = { id: 43, audio_path: null, translated_title: "第二章" };
sandbox.window.EBookToAudio.renderChaptersForTest({
  book: { id: 1 },
  chapters: [noAudioChapter],
  audio: new Map(),
});
assert.strictEqual(sandbox.document.querySelector("[data-publish-ximalaya='43']"), null);
```

- [ ] **Step 2: Run static test to verify it fails**

Run: `node tests/static_app_logic_test.js`

Expected: FAIL because `renderChaptersForTest` or the publish button is missing.

- [ ] **Step 3: Add publish helper and button**

In `src/ebook_to_audio/static/app.js`, add:

```javascript
  async function publishXimalayaDraft(chapterId) {
    try {
      state.expandedChapters.add(chapterId);
      renderChapters();
      setStatus(`正在打开喜马拉雅上传草稿：章节 ${chapterId}...`);
      const result = await api(`/api/chapters/${chapterId}/publish/ximalaya/draft`, {
        method: "POST",
      });
      setStatus(result.message || "喜马拉雅草稿已填写，请在浏览器中确认后手动发布。");
    } catch (error) {
      setStatus(`喜马拉雅发布草稿失败：${error.message}`, "error");
    }
  }
```

In `chapterCard`, add this button inside `.chapter-actions` after the TTS button:

```javascript
          ${chapter.audio_path ? `<button type="button" data-publish-ximalaya="${chapter.id}">发布草稿到喜马拉雅</button>` : ""}
```

In the chapter click handler, add:

```javascript
        } else if (target.dataset.publishXimalaya) {
          publishXimalayaDraft(Number(target.dataset.publishXimalaya));
```

In `window.EBookToAudio`, export:

```javascript
    publishXimalayaDraft,
    renderChaptersForTest(input) {
      state.book = input.book || null;
      state.chapters = input.chapters || [];
      state.audio = input.audio || new Map();
      renderChapters();
    },
```

- [ ] **Step 4: Run static tests**

Run: `node tests/static_app_logic_test.js`

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/ebook_to_audio/static/app.js tests/static_app_logic_test.js
git commit -m "Add Ximalaya draft publish UI action"
```

---

### Task 6: Full Verification

**Files:**
- Verify all modified source and tests.

- [ ] **Step 1: Run focused Python tests**

Run:

```bash
pytest tests/test_config.py tests/test_ximalaya_publisher.py tests/test_web_jobs.py -v
```

Expected: PASS.

- [ ] **Step 2: Run static JS tests**

Run:

```bash
node tests/static_app_logic_test.js
```

Expected: PASS.

- [ ] **Step 3: Run full test suite**

Run:

```bash
pytest
```

Expected: PASS.

- [ ] **Step 4: Manual browser verification**

Run the app:

```bash
uvicorn "ebook_to_audio.web:create_app" --factory --reload
```

Open `http://127.0.0.1:8000`, use a chapter with merged audio, click `发布草稿到喜马拉雅`, and confirm:

- The browser opens `https://studio.ximalaya.com/upload?albumId=122326236`.
- The audio file is attached.
- The title, description, and tags are filled.
- The automation does not click final publish or submit-for-review.

- [ ] **Step 5: Commit any final fixes**

If verification required fixes, commit them:

```bash
git add src tests config.example.yaml pyproject.toml
git commit -m "Finalize Ximalaya draft publishing"
```

If there were no fixes after the previous task commits, do not create an empty commit.
