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
