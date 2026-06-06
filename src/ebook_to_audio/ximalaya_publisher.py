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


class PlaywrightXimalayaPublisher:
    def fill_draft(self, draft: XimalayaDraft) -> XimalayaPublishResult:
        raise XimalayaPublishError("Playwright publisher is not implemented yet.")
