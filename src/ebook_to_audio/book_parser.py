from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

import ebooklib
from bs4 import BeautifulSoup
from ebooklib import epub


class ParseError(Exception):
    """Raised when uploaded book bytes cannot be parsed."""


@dataclass(frozen=True)
class ParsedChapter:
    title: str
    text: str


@dataclass(frozen=True)
class ParsedBook:
    title: str
    source_format: str
    full_text: str
    initial_chapters: tuple[ParsedChapter, ...]


def parse_book_bytes(filename: str, content: bytes) -> ParsedBook:
    source_path = Path(filename)
    source_format = source_path.suffix.lower().lstrip(".")
    title = source_path.stem

    if source_format == "txt":
        return parse_txt(title, content)
    if source_format == "epub":
        return parse_epub(title, content)

    raise ParseError(f"Unsupported file type: {source_path.suffix or filename}")


def parse_txt(title: str, content: bytes) -> ParsedBook:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ParseError("TXT file must be UTF-8 encoded") from exc

    text = _normalize_line_endings(text).strip()
    if not text:
        raise ParseError("TXT file is empty")

    return ParsedBook(
        title=title,
        source_format="txt",
        full_text=text,
        initial_chapters=(ParsedChapter(title=title, text=text),),
    )


def parse_epub(title: str, content: bytes) -> ParsedBook:
    if not content:
        raise ParseError("EPUB file is empty")

    with tempfile.TemporaryDirectory() as temp_dir:
        epub_path = Path(temp_dir) / "book.epub"
        epub_path.write_bytes(content)

        try:
            book = epub.read_epub(str(epub_path))
        except Exception as exc:
            raise ParseError("Could not parse EPUB file") from exc

    chapters = _extract_epub_chapters(book, fallback_title=title)
    if not chapters:
        raise ParseError("EPUB file does not contain readable text")

    full_text = "\n\n".join(chapter.text for chapter in chapters)
    return ParsedBook(
        title=title,
        source_format="epub",
        full_text=full_text,
        initial_chapters=tuple(chapters),
    )


def _extract_epub_chapters(book: epub.EpubBook, fallback_title: str) -> list[ParsedChapter]:
    chapters: list[ParsedChapter] = []

    for spine_item in book.spine:
        item_id = spine_item[0] if isinstance(spine_item, tuple) else spine_item
        item = book.get_item_with_id(item_id)
        if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT or _is_navigation_item(item):
            continue

        text = _extract_document_text(item)
        if not text:
            continue

        chapter_title = _extract_document_title(item) or Path(item.get_name()).stem or fallback_title
        chapters.append(ParsedChapter(title=chapter_title, text=text))

    return chapters


def _is_navigation_item(item: ebooklib.epub.EpubItem) -> bool:
    properties = getattr(item, "properties", [])
    if "nav" in properties:
        return True

    name = item.get_name().lower()
    return name.endswith("nav.xhtml") or name.endswith("toc.xhtml") or name.endswith("toc.ncx")


def _extract_document_text(item: ebooklib.epub.EpubItem) -> str:
    soup = BeautifulSoup(item.get_content(), "html.parser")
    for ignored_tag in soup(["script", "style"]):
        ignored_tag.decompose()

    lines = _normalize_line_endings(soup.get_text(separator="\n")).splitlines()
    return "\n".join(line.strip() for line in lines if line.strip())


def _extract_document_title(item: ebooklib.epub.EpubItem) -> str | None:
    soup = BeautifulSoup(item.get_content(), "html.parser")
    title_tag = soup.find(["h1", "title"])
    if title_tag is None:
        return None

    title = title_tag.get_text(separator=" ", strip=True)
    return title or None


def _normalize_line_endings(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")
