from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
from typing import Callable, Iterable


@dataclass(frozen=True)
class CleanResult:
    operation: str
    text: str
    before_chars: int
    after_chars: int
    removed_lines: int


@dataclass(frozen=True)
class CombinedCleanResult:
    text: str
    results: list[CleanResult]


_INVISIBLE_CHARACTERS = str.maketrans(
    {
        "\u200b": "",
        "\u200c": "",
        "\u200d": "",
        "\ufeff": "",
        "\u2060": "",
    }
)
_SPACING_RE = re.compile(r"[ \t\f\v\u00a0\u3000]+")
_EXCESS_BLANK_LINES_RE = re.compile(r"\n{3,}")
_STANDALONE_URL_OR_DOMAIN_RE = re.compile(
    r"^[\s:：\-—_=*【】\[\]（）()《》<>|]*(?:https?://\S+|www\.\S+|"
    r"[A-Za-z0-9-]+\.(?:com|net|org|cn|cc|vip|xyz|top|info)(?:/\S*)?)"
    r"[\s:：\-—_=*【】\[\]（）()《》<>|]*$",
    re.IGNORECASE,
)
_WATERMARK_RE = re.compile(
    r"(本书来自|扫码关注|关注公众号|微信公众号|水印|更多精彩|下载地址|小说下载|仅供.*交流)",
    re.IGNORECASE,
)
_DECORATIVE_LINE_RE = re.compile(r"^[\s*\-=~_#·•…—–]{6,}$")


def remove_watermarks(text: str) -> CleanResult:
    before_chars = len(text)
    kept_lines = []
    removed_lines = 0

    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped and (_WATERMARK_RE.search(stripped) or _STANDALONE_URL_OR_DOMAIN_RE.match(stripped)):
            removed_lines += 1
            continue
        kept_lines.append(line)

    cleaned = text if removed_lines == 0 else "".join(kept_lines)
    return _result("remove_watermarks", cleaned, before_chars, removed_lines)


def normalize_spacing(text: str) -> CleanResult:
    before_chars = len(text)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").translate(_INVISIBLE_CHARACTERS)
    lines = [_SPACING_RE.sub(" ", line).strip() for line in normalized.split("\n")]
    cleaned = _EXCESS_BLANK_LINES_RE.sub("\n\n", "\n".join(lines)).strip()
    removed_lines = max(0, len(lines) - len(cleaned.split("\n"))) if cleaned else len(lines)

    return _result("normalize_spacing", cleaned, before_chars, removed_lines)


def remove_repeated_noise_lines(text: str, min_repeats: int = 3, max_line_chars: int = 20) -> CleanResult:
    before_chars = len(text)
    lines = text.splitlines(keepends=True)
    counts = Counter(line.strip() for line in lines if _is_short_noise_candidate(line, max_line_chars))
    repeated_noise = {line for line, count in counts.items() if count >= min_repeats}

    kept_lines = []
    removed_lines = 0
    for line in lines:
        if line.strip() in repeated_noise:
            removed_lines += 1
            continue
        kept_lines.append(line)

    cleaned = text if removed_lines == 0 else "".join(kept_lines)
    return _result("remove_repeated_noise_lines", cleaned, before_chars, removed_lines)


def remove_decorative_characters(text: str) -> CleanResult:
    before_chars = len(text)
    kept_lines = []
    removed_lines = 0

    for line in text.splitlines(keepends=True):
        if _DECORATIVE_LINE_RE.match(line.strip()):
            removed_lines += 1
            continue
        kept_lines.append(line)

    cleaned = text if removed_lines == 0 else "".join(kept_lines)
    return _result("remove_decorative_characters", cleaned, before_chars, removed_lines)


def clean_text(text: str, operations: Iterable[str]) -> CombinedCleanResult:
    current_text = text
    results: list[CleanResult] = []

    for operation in operations:
        cleaner = _OPERATIONS.get(operation)
        if cleaner is None:
            raise ValueError(f"Unknown text cleaning operation: {operation}")
        result = cleaner(current_text)
        results.append(result)
        current_text = result.text

    return CombinedCleanResult(text=current_text, results=results)


def _is_short_noise_candidate(line: str, max_line_chars: int) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > max_line_chars:
        return False
    return bool(
        _STANDALONE_URL_OR_DOMAIN_RE.match(stripped)
        or _WATERMARK_RE.search(stripped)
        or _DECORATIVE_LINE_RE.match(stripped)
        or re.search(r"(广告|发布页|防盗|盗版|来源|书源|推广)", stripped, re.IGNORECASE)
    )


def _result(operation: str, text: str, before_chars: int, removed_lines: int) -> CleanResult:
    return CleanResult(
        operation=operation,
        text=text,
        before_chars=before_chars,
        after_chars=len(text),
        removed_lines=removed_lines,
    )


_OPERATIONS: dict[str, Callable[[str], CleanResult]] = {
    "remove_watermarks": remove_watermarks,
    "normalize_spacing": normalize_spacing,
    "remove_repeated_noise_lines": remove_repeated_noise_lines,
    "remove_decorative_characters": remove_decorative_characters,
}
