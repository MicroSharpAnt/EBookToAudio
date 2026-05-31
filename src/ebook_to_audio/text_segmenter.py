from __future__ import annotations


def split_text(text: str, max_chars: int) -> list[str]:
    if max_chars <= 0:
        raise ValueError("max_chars must be greater than zero")

    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    if len(normalized) <= max_chars:
        return [normalized]

    return _split_chunk(normalized, max_chars)


def _split_chunk(text: str, max_chars: int) -> list[str]:
    segments: list[str] = []
    remaining = text

    while len(remaining) > max_chars:
        split_index = _preferred_split_index(remaining, max_chars) or max_chars
        segments.append(remaining[:split_index])
        remaining = remaining[split_index:]

    if remaining:
        segments.append(remaining)

    return segments


def _preferred_split_index(text: str, max_chars: int) -> int | None:
    blank_line_index = text.rfind("\n\n", 0, max_chars + 1)
    if blank_line_index != -1:
        after_separator = blank_line_index + 2
        if after_separator <= max_chars:
            return after_separator
        if blank_line_index == max_chars:
            return blank_line_index

    line_index = text.rfind("\n", 0, max_chars)
    if line_index != -1:
        return line_index + 1

    return None
