from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class SplitChapter:
    title: str
    text: str


_CHINESE_NUMBER = r"[零〇一二三四五六七八九十百千万两\d]+"
_ROMAN_NUMBER = r"[IVXLCDM]+"
_HEADING_RE = re.compile(
    rf"^\s*(?:"
    rf"第{_CHINESE_NUMBER}\s*[章节回卷部篇].*|"
    rf"卷\s*{_CHINESE_NUMBER}.*|"
    rf"(?:Chapter|Section|Part|Book)\s+(?:\d+|{_ROMAN_NUMBER})\b.*|"
    rf"(?:\d+|{_ROMAN_NUMBER})[.)、]\s+\S.*|"
    rf"(?:序|序章|序言|前言|楔子|尾声|后记)"
    rf")\s*$",
    re.IGNORECASE,
)
_MAX_HEADING_CHARS = 80


def split_into_chapters(text: str, fallback_chars: int = 6000) -> list[SplitChapter]:
    if fallback_chars <= 0:
        raise ValueError("fallback_chars must be greater than zero")

    normalized = _normalize_line_endings(text).strip()
    if not normalized:
        return []

    chapters = _split_by_headings(normalized)
    if len(chapters) >= 2:
        return chapters

    return _split_by_size(normalized, fallback_chars)


def _split_by_headings(text: str) -> list[SplitChapter]:
    chapters: list[SplitChapter] = []
    current_title: str | None = None
    current_lines: list[str] = []
    front_matter_lines: list[str] = []

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if _is_heading(line):
            if current_title is not None:
                chapters.append(SplitChapter(title=current_title, text="\n".join(current_lines).strip()))
            current_title = line
            current_lines = []
        elif current_title is not None:
            current_lines.append(raw_line)
        else:
            front_matter_lines.append(raw_line)

    if current_title is not None:
        chapters.append(SplitChapter(title=current_title, text="\n".join(current_lines).strip()))

    front_matter = "\n".join(front_matter_lines).strip()
    if front_matter and len(chapters) >= 2:
        chapters.insert(0, SplitChapter(title="前言", text=front_matter))

    return chapters


def _is_heading(line: str) -> bool:
    return bool(line and len(line) <= _MAX_HEADING_CHARS and _HEADING_RE.match(line))


def _split_by_size(text: str, fallback_chars: int) -> list[SplitChapter]:
    return [
        SplitChapter(title=f"第 {index + 1} 段", text=text[start : start + fallback_chars])
        for index, start in enumerate(range(0, len(text), fallback_chars))
        if text[start : start + fallback_chars]
    ]


def _normalize_line_endings(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")
