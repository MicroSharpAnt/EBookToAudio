import builtins
import sys
import types
from pathlib import Path

import pytest

from ebook_to_audio.config import PublishingConfig
from ebook_to_audio.models import Chapter
from ebook_to_audio.ximalaya_publisher import (
    PlaywrightXimalayaPublisher,
    XimalayaDraft,
    XimalayaDraftError,
    XimalayaPublishError,
    build_ximalaya_draft,
    _fill_first_available,
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
        chapter(translated_title=None, title="Original Chapter Title"),
        PublishingConfig(ximalaya_album_id="122326236"),
        audio_file,
    )

    assert draft.title == "Original Chapter Title"


def test_build_ximalaya_draft_falls_back_to_chapter_number(tmp_path: Path):
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


def test_fill_draft_keeps_browser_open_for_manual_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    class FakeLocator:
        @property
        def first(self):
            return self

        def count(self):
            return 1

        def click(self, **_kwargs):
            pass

        def fill(self, _value, **_kwargs):
            pass

        def press(self, _key, **_kwargs):
            pass

        def set_input_files(self, _path, **_kwargs):
            pass

    class FakePage:
        def goto(self, _url, **_kwargs):
            pass

        def get_by_label(self, _label, **_kwargs):
            return FakeLocator()

        def get_by_placeholder(self, _placeholder, **_kwargs):
            return FakeLocator()

        def get_by_text(self, _text, **_kwargs):
            return FakeLocator()

        def locator(self, _selector):
            return FakeLocator()

    class FakeBrowserContext:
        def __init__(self):
            self.closed = False
            self.pages = [FakePage()]

        def close(self):
            self.closed = True

        def new_page(self):
            page = FakePage()
            self.pages.append(page)
            return page

    class FakeChromium:
        def __init__(self, context):
            self.context = context

        def launch_persistent_context(self, *_args, **_kwargs):
            return self.context

    class FakePlaywright:
        def __init__(self):
            self.context = FakeBrowserContext()
            self.chromium = FakeChromium(self.context)
            self.stopped = False

        def stop(self):
            self.stopped = True

    class FakePlaywrightManager:
        def __init__(self):
            self.playwright = FakePlaywright()
            self.exited = False

        def start(self):
            return self.playwright

        def __enter__(self):
            return self.playwright

        def __exit__(self, *_args):
            self.exited = True
            self.playwright.stop()

    fake_manager = FakePlaywrightManager()
    fake_sync_api = types.ModuleType("playwright.sync_api")
    fake_sync_api.Error = RuntimeError
    fake_sync_api.sync_playwright = lambda: fake_manager
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)

    audio_file = tmp_path / "chapter.wav"
    audio_file.write_bytes(b"RIFF")
    draft = XimalayaDraft(
        album_id="122326236",
        upload_url="https://studio.ximalaya.com/upload?albumId=122326236",
        audio_path=audio_file,
        title="第一章",
        description="简介",
        tags=("有声书",),
    )

    publisher = PlaywrightXimalayaPublisher(user_data_dir=tmp_path / "browser")
    result = publisher.fill_draft(draft)

    assert result.status == "ready_for_review"
    assert fake_manager.exited is False
    assert fake_manager.playwright.stopped is False
    assert fake_manager.playwright.context.closed is False


def test_fill_draft_returns_manual_action_when_upload_control_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    class MissingLocator:
        @property
        def first(self):
            return self

        def count(self):
            return 0

    class FakePage:
        def goto(self, _url, **_kwargs):
            pass

        def get_by_text(self, _text, **_kwargs):
            return MissingLocator()

        def locator(self, _selector):
            return MissingLocator()

    class FakeBrowserContext:
        def __init__(self):
            self.closed = False
            self.pages = [FakePage()]

        def close(self):
            self.closed = True

    class FakeChromium:
        def __init__(self, context):
            self.context = context

        def launch_persistent_context(self, *_args, **_kwargs):
            return self.context

    class FakePlaywright:
        def __init__(self):
            self.context = FakeBrowserContext()
            self.chromium = FakeChromium(self.context)
            self.stopped = False

        def stop(self):
            self.stopped = True

    class FakePlaywrightManager:
        def __init__(self):
            self.playwright = FakePlaywright()

        def start(self):
            return self.playwright

    fake_manager = FakePlaywrightManager()
    fake_sync_api = types.ModuleType("playwright.sync_api")
    fake_sync_api.Error = RuntimeError
    fake_sync_api.sync_playwright = lambda: fake_manager
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)

    audio_file = tmp_path / "chapter.wav"
    audio_file.write_bytes(b"RIFF")
    draft = XimalayaDraft(
        album_id="122326236",
        upload_url="https://studio.ximalaya.com/upload?albumId=122326236",
        audio_path=audio_file,
        title="第一章",
        description="简介",
        tags=("有声书",),
    )

    publisher = PlaywrightXimalayaPublisher(user_data_dir=tmp_path / "browser", timeout_ms=1)
    result = publisher.fill_draft(draft)

    assert result.status == "manual_action_required"
    assert "上传控件" in result.message
    assert result.draft == draft
    assert fake_manager.playwright.stopped is False
    assert fake_manager.playwright.context.closed is False


def test_fill_draft_retries_album_upload_url_after_bare_upload_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    class MissingLocator:
        @property
        def first(self):
            return self

        def count(self):
            return 0

    class ReadyLocator:
        def __init__(self):
            self.files = None
            self.filled = []
            self.pressed = []

        @property
        def first(self):
            return self

        def count(self):
            return 1

        def set_input_files(self, path, **_kwargs):
            self.files = path

        def fill(self, value, **_kwargs):
            self.filled.append(value)

        def press(self, key, **_kwargs):
            self.pressed.append(key)

    class FakePage:
        def __init__(self):
            self.goto_urls = []
            self.file_input = ReadyLocator()
            self.title = ReadyLocator()
            self.description = ReadyLocator()
            self.tags = ReadyLocator()

        @property
        def url(self):
            return "https://studio.ximalaya.com/upload" if len(self.goto_urls) == 1 else self.goto_urls[-1]

        def goto(self, url, **_kwargs):
            self.goto_urls.append(url)

        def get_by_text(self, _text, **_kwargs):
            return MissingLocator()

        def get_by_label(self, label, **_kwargs):
            if len(self.goto_urls) < 2:
                return MissingLocator()
            return {"标题": self.title, "简介": self.description, "标签": self.tags}.get(
                label,
                MissingLocator(),
            )

        def get_by_placeholder(self, _placeholder, **_kwargs):
            return MissingLocator()

        def locator(self, selector):
            if len(self.goto_urls) < 2:
                return MissingLocator()
            if selector == "input[type='file']":
                return self.file_input
            return MissingLocator()

    class FakeBrowserContext:
        def __init__(self):
            self.closed = False
            self.page = FakePage()
            self.pages = [self.page]

        def close(self):
            self.closed = True

    class FakeChromium:
        def __init__(self, context):
            self.context = context

        def launch_persistent_context(self, *_args, **_kwargs):
            return self.context

    class FakePlaywright:
        def __init__(self):
            self.context = FakeBrowserContext()
            self.chromium = FakeChromium(self.context)
            self.stopped = False

        def stop(self):
            self.stopped = True

    class FakePlaywrightManager:
        def __init__(self):
            self.playwright = FakePlaywright()

        def start(self):
            return self.playwright

    fake_manager = FakePlaywrightManager()
    fake_sync_api = types.ModuleType("playwright.sync_api")
    fake_sync_api.Error = RuntimeError
    fake_sync_api.sync_playwright = lambda: fake_manager
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)

    audio_file = tmp_path / "chapter.wav"
    audio_file.write_bytes(b"RIFF")
    draft = XimalayaDraft(
        album_id="122326236",
        upload_url="https://studio.ximalaya.com/upload?albumId=122326236",
        audio_path=audio_file,
        title="第一章",
        description="简介",
        tags=("有声书",),
    )

    publisher = PlaywrightXimalayaPublisher(user_data_dir=tmp_path / "browser", timeout_ms=1)
    result = publisher.fill_draft(draft)

    page = fake_manager.playwright.context.page
    assert result.status == "ready_for_review"
    assert page.goto_urls == [draft.upload_url, draft.upload_url]
    assert page.file_input.files == str(audio_file)
    assert page.title.filled == ["第一章"]
    assert page.description.filled == ["简介"]
    assert page.tags.filled == ["有声书"]
    assert fake_manager.playwright.context.closed is False
    assert fake_manager.playwright.stopped is False


def test_fill_draft_closes_browser_and_raises_when_locator_probe_has_runtime_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    class FakePlaywrightError(Exception):
        pass

    class CrashingLocator:
        @property
        def first(self):
            return self

        def count(self):
            raise FakePlaywrightError("page closed")

    class FakePage:
        def goto(self, _url, **_kwargs):
            pass

        def get_by_text(self, _text, **_kwargs):
            return CrashingLocator()

        def locator(self, _selector):
            return CrashingLocator()

    class FakeBrowserContext:
        def __init__(self):
            self.closed = False
            self.pages = [FakePage()]

        def close(self):
            self.closed = True

    class FakeChromium:
        def __init__(self, context):
            self.context = context

        def launch_persistent_context(self, *_args, **_kwargs):
            return self.context

    class FakePlaywright:
        def __init__(self):
            self.context = FakeBrowserContext()
            self.chromium = FakeChromium(self.context)
            self.stopped = False

        def stop(self):
            self.stopped = True

    class FakePlaywrightManager:
        def __init__(self):
            self.playwright = FakePlaywright()

        def start(self):
            return self.playwright

    fake_manager = FakePlaywrightManager()
    fake_sync_api = types.ModuleType("playwright.sync_api")
    fake_sync_api.Error = FakePlaywrightError
    fake_sync_api.sync_playwright = lambda: fake_manager
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)

    audio_file = tmp_path / "chapter.wav"
    audio_file.write_bytes(b"RIFF")
    draft = XimalayaDraft(
        album_id="122326236",
        upload_url="https://studio.ximalaya.com/upload?albumId=122326236",
        audio_path=audio_file,
        title="第一章",
        description="简介",
        tags=("有声书",),
    )

    publisher = PlaywrightXimalayaPublisher(user_data_dir=tmp_path / "browser", timeout_ms=1)

    with pytest.raises(XimalayaPublishError, match="页面自动填写失败"):
        publisher.fill_draft(draft)

    assert fake_manager.playwright.stopped is True
    assert fake_manager.playwright.context.closed is True


def test_fill_first_available_waits_for_late_rendered_control():
    class FakeLocator:
        def __init__(self, counts):
            self.counts = list(counts)
            self.filled = None

        @property
        def first(self):
            return self

        def count(self):
            return self.counts.pop(0) if self.counts else 1

        def fill(self, value, **_kwargs):
            self.filled = value

    class MissingLocator:
        @property
        def first(self):
            return self

        def count(self):
            return 0

    class FakePage:
        def __init__(self):
            self.title = FakeLocator([0, 1])

        def get_by_label(self, label, **_kwargs):
            return self.title if label == "标题" else MissingLocator()

        def get_by_placeholder(self, _placeholder, **_kwargs):
            return MissingLocator()

        def locator(self, _selector):
            return MissingLocator()

    page = FakePage()

    _fill_first_available(page, ["标题"], "第一章", timeout_ms=1_000)

    assert page.title.filled == "第一章"


def test_close_stops_playwright_when_context_close_raises(tmp_path: Path):
    class ClosingContext:
        def close(self):
            raise RuntimeError("context close failed")

    class FakePlaywright:
        def __init__(self):
            self.stopped = False

        def stop(self):
            self.stopped = True

    playwright = FakePlaywright()
    publisher = PlaywrightXimalayaPublisher(user_data_dir=tmp_path / "browser")
    publisher._browser_context = ClosingContext()
    publisher._playwright = playwright

    with pytest.raises(RuntimeError, match="context close failed"):
        publisher.close()

    assert playwright.stopped is True
    assert publisher._browser_context is None
    assert publisher._playwright is None


def test_missing_playwright_message_uses_main_dependency_install_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "playwright.sync_api":
            raise ImportError("No module named playwright")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    publisher = PlaywrightXimalayaPublisher(user_data_dir=tmp_path / "browser")
    draft = XimalayaDraft(
        album_id="122326236",
        upload_url="https://studio.ximalaya.com/upload?albumId=122326236",
        audio_path=tmp_path / "chapter.wav",
        title="第一章",
        description="",
        tags=(),
    )

    with pytest.raises(XimalayaPublishError) as exc_info:
        publisher.fill_draft(draft)

    message = str(exc_info.value)
    assert "pip install -e ." in message
    assert "playwright install chromium" in message
    assert "[dev]" not in message
