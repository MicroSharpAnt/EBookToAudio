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
