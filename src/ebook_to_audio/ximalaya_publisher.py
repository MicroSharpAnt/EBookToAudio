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
    def __init__(self, user_data_dir: Path | None = None, timeout_ms: int = 120_000):
        self.user_data_dir = user_data_dir or Path.home() / ".ebook-to-audio" / "ximalaya-browser"
        self.timeout_ms = timeout_ms
        self._playwright = None
        self._browser_context = None

    def close(self) -> None:
        browser_context = self._browser_context
        playwright = self._playwright
        self._browser_context = None
        self._playwright = None
        if browser_context is not None:
            browser_context.close()
        if playwright is not None:
            playwright.stop()

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
            self.close()
            self._playwright = sync_playwright().start()
            self._browser_context = self._playwright.chromium.launch_persistent_context(
                str(self.user_data_dir),
                headless=False,
                accept_downloads=True,
            )
            page = (
                self._browser_context.pages[0]
                if self._browser_context.pages
                else self._browser_context.new_page()
            )
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
        except XimalayaPublishError:
            self.close()
            raise
        except PlaywrightError as exc:
            self.close()
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
