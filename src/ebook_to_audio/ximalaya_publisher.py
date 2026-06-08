from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from time import monotonic, sleep

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
    def __init__(
        self,
        user_data_dir: Path | None = None,
        timeout_ms: int = 120_000,
        system_chrome_path: Path | None = None,
        browser_cdp_url: str = "",
    ):
        self.user_data_dir = user_data_dir or Path.home() / ".ebook-to-audio" / "ximalaya-browser"
        self.timeout_ms = timeout_ms
        self.system_chrome_path = system_chrome_path or Path(
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        )
        self.browser_cdp_url = browser_cdp_url.strip()
        self._playwright = None
        self._browser_context = None
        self._external_browser = None

    def close(self) -> None:
        browser_context = self._browser_context
        playwright = self._playwright
        external_browser = self._external_browser
        self._browser_context = None
        self._playwright = None
        self._external_browser = None
        try:
            if browser_context is not None and external_browser is None:
                browser_context.close()
        finally:
            if playwright is not None:
                playwright.stop()

    def fill_draft(self, draft: XimalayaDraft) -> XimalayaPublishResult:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise XimalayaPublishError(
                "缺少 Playwright 运行库。请运行 pip install -e . 后执行 playwright install chromium。"
            ) from exc

        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.close()
            self._playwright = sync_playwright().start()
            self._browser_context = self._launch_persistent_context(PlaywrightError)
            page = (
                self._browser_context.pages[0]
                if self._browser_context.pages
                else self._browser_context.new_page()
            )
            _navigate_to_upload_url(page, draft.upload_url, self.timeout_ms)
            _ensure_album_upload_url(page, draft, self.timeout_ms)
            _ensure_not_login_page(page)
            _fill_draft_form(page, draft, self.timeout_ms)
            return XimalayaPublishResult(
                status="ready_for_review",
                message="喜马拉雅草稿已填写，请在浏览器中确认后手动发布。",
                draft=draft,
            )
        except XimalayaPublishError as exc:
            return XimalayaPublishResult(
                status="manual_action_required",
                message=f"{exc} 请在已打开的浏览器中完成登录、验证或手动恢复后重试。",
                draft=draft,
            )
        except PlaywrightError as exc:
            if self._browser_context is not None:
                return XimalayaPublishResult(
                    status="manual_action_required",
                    message=(
                        f"{_playwright_error_message(exc)} "
                        "请在已打开的浏览器中完成登录、验证或手动恢复后重试。"
                    ),
                    draft=draft,
                )
            self.close()
            raise XimalayaPublishError(_playwright_error_message(exc)) from exc

    def _launch_persistent_context(self, playwright_error_type):
        if self.browser_cdp_url:
            self._external_browser = self._playwright.chromium.connect_over_cdp(
                self.browser_cdp_url
            )
            if not self._external_browser.contexts:
                return self._external_browser.new_context()
            return self._external_browser.contexts[0]
        if self.system_chrome_path.exists():
            return self._playwright.chromium.launch_persistent_context(
                str(self.user_data_dir),
                headless=False,
                accept_downloads=True,
                executable_path=str(self.system_chrome_path),
            )
        try:
            return self._playwright.chromium.launch_persistent_context(
                str(self.user_data_dir),
                headless=False,
                accept_downloads=True,
            )
        except playwright_error_type as exc:
            if not _should_retry_with_system_chrome(exc) or not self.system_chrome_path.exists():
                raise
            return self._playwright.chromium.launch_persistent_context(
                str(self.user_data_dir),
                headless=False,
                accept_downloads=True,
                executable_path=str(self.system_chrome_path),
            )


def _playwright_error_message(exc: Exception) -> str:
    message = str(exc)
    if "Executable doesn't exist" in message and "playwright install" in message:
        return "缺少 Playwright Chromium 浏览器。请运行 uv run playwright install chromium 后重试。"
    return f"喜马拉雅页面自动填写失败：{message}"


def _should_retry_with_system_chrome(exc: Exception) -> bool:
    message = str(exc)
    return (
        "Target page, context or browser has been closed" in message
        and ("Received signal" in message or "process did exit" in message)
    )


def _navigate_to_upload_url(page, upload_url: str, timeout_ms: int) -> None:
    page.goto(upload_url, wait_until="domcontentloaded", timeout=timeout_ms)


def _ensure_album_upload_url(page, draft: XimalayaDraft, timeout_ms: int) -> None:
    current_url = str(getattr(page, "url", ""))
    if f"albumId={draft.album_id}" in current_url:
        return
    if "/upload" not in current_url:
        return
    _navigate_to_upload_url(page, draft.upload_url, timeout_ms)


def _ensure_not_login_page(page) -> None:
    current_url = str(getattr(page, "url", "")).lower()
    login_markers = ("passport.ximalaya.com", "/login", "sso", "auth")
    if any(marker in current_url for marker in login_markers):
        raise XimalayaPublishError("喜马拉雅需要先登录账号。")


def _fill_draft_form(page, draft: XimalayaDraft, timeout_ms: int) -> None:
    _set_file_input(page, draft.audio_path, timeout_ms)
    _fill_first_available(page, ["标题", "声音标题", "请输入标题"], draft.title, timeout_ms)
    if draft.description:
        _fill_first_available(page, ["简介", "声音简介", "请输入简介"], draft.description, timeout_ms)
    if draft.tags:
        _fill_tags(page, draft.tags, timeout_ms)


def _set_file_input(page, audio_path: Path, timeout_ms: int) -> None:
    file_inputs = page.locator("input[type='file']")
    candidates = [(file_inputs, "file")]
    candidates.extend((page.get_by_text(text, exact=False), "button") for text in ("上传", "选择文件", "上传声音"))
    match = _wait_for_first_candidate(candidates, timeout_ms)
    if match is not None:
        target, action = match
        if action == "file":
            target.set_input_files(str(audio_path), timeout=timeout_ms)
            return
        target.click(timeout=timeout_ms)
        page.locator("input[type='file']").first.set_input_files(str(audio_path), timeout=timeout_ms)
        return
    raise XimalayaPublishError("未找到音频上传控件，请登录后重试或检查上传页是否改版。")


def _fill_first_available(page, labels: list[str], value: str, timeout_ms: int) -> None:
    candidates = []
    for label in labels:
        candidates.extend(
            [
                page.get_by_label(label, exact=False),
                page.get_by_placeholder(label, exact=False),
                page.locator(f"input[placeholder*='{label}']"),
                page.locator(f"textarea[placeholder*='{label}']"),
            ]
        )
    target = _wait_for_first_locator(candidates, timeout_ms)
    if target is not None:
        target.fill(value, timeout=timeout_ms)
        return
    raise XimalayaPublishError(f"未找到字段：{labels[0]}。请检查喜马拉雅上传页是否改版。")


def _fill_tags(page, tags: tuple[str, ...], timeout_ms: int) -> None:
    tag_text = " ".join(tags)
    candidates = []
    for label in ("标签", "声音标签", "请输入标签"):
        candidates.extend(
            [
                page.get_by_label(label, exact=False),
                page.get_by_placeholder(label, exact=False),
                page.locator(f"input[placeholder*='{label}']"),
            ]
        )
    target = _wait_for_first_locator(candidates, timeout_ms)
    if target is not None:
        target.fill(tag_text, timeout=timeout_ms)
        try:
            target.press("Enter", timeout=timeout_ms)
        except Exception:
            pass
        return
    raise XimalayaPublishError("未找到字段：标签。请检查喜马拉雅上传页是否改版。")


def _wait_for_first_locator(candidates, timeout_ms: int):
    match = _wait_for_first_candidate([(candidate, None) for candidate in candidates], timeout_ms)
    return None if match is None else match[0]


def _wait_for_first_candidate(candidates, timeout_ms: int):
    deadline = monotonic() + (timeout_ms / 1000)
    while True:
        for locator, metadata in candidates:
            try:
                if locator.count() > 0:
                    return locator.first, metadata
            except Exception as exc:
                if _is_playwright_runtime_error(exc):
                    raise
                continue
        remaining = deadline - monotonic()
        if remaining <= 0:
            return None
        sleep(min(0.1, remaining))


def _is_playwright_runtime_error(exc: Exception) -> bool:
    try:
        from playwright.sync_api import Error as PlaywrightError
    except ImportError:
        return False
    return isinstance(exc, PlaywrightError)
