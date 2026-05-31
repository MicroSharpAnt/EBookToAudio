from __future__ import annotations


def split_text(text: str, max_chars: int) -> list[str]:
    if max_chars <= 0:
        raise ValueError("max_chars must be greater than zero")

    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    if len(normalized) <= max_chars:
        return [normalized]

    segments: list[str] = []
    current = ""

    for paragraph in _paragraph_chunks(normalized):
        if len(paragraph) > max_chars:
            if current:
                segments.append(current)
                current = ""
            segments.extend(_split_long_paragraph(paragraph, max_chars))
        elif not current:
            current = paragraph
        elif len(current) + len("\n\n") + len(paragraph) <= max_chars:
            current += "\n\n" + paragraph
        else:
            segments.append(current)
            current = paragraph

    if current:
        segments.append(current)

    return segments


def _paragraph_chunks(text: str) -> list[str]:
    return [part for part in text.split("\n\n") if part.strip()] or [text]


def _split_long_paragraph(paragraph: str, max_chars: int) -> list[str]:
    line_segments = _split_by_lines(paragraph, max_chars)
    segments: list[str] = []

    for line in line_segments:
        if not line.strip():
            continue
        if len(line) <= max_chars:
            segments.append(line)
        else:
            segments.extend(segment for segment in _hard_split(line, max_chars) if segment.strip())

    return segments


def _split_by_lines(text: str, max_chars: int) -> list[str]:
    lines = text.splitlines(keepends=True)
    if len(lines) <= 1:
        return [text]

    segments: list[str] = []
    current = ""

    for line in lines:
        if len(line) > max_chars:
            if current:
                segments.append(current)
                current = ""
            segments.append(line)
        elif not current:
            current = line
        elif len(current) + len(line) <= max_chars:
            current += line
        else:
            segments.append(current)
            current = line

    if current:
        segments.append(current)

    return segments


def _hard_split(text: str, max_chars: int) -> list[str]:
    return [text[index : index + max_chars] for index in range(0, len(text), max_chars)]
