from pathlib import Path

import pytest

from ebook_to_audio.book_parser import ParseError, ParsedChapter, parse_book_bytes


def test_parse_txt_normalizes_bom_and_line_endings():
    parsed = parse_book_bytes("sample.txt", "\ufeff第一章\r\n正文\r\n\r\n第二行".encode("utf-8"))

    assert parsed.title == "sample"
    assert parsed.source_format == "txt"
    assert parsed.full_text == "第一章\n正文\n\n第二行"
    assert parsed.initial_chapters == [ParsedChapter(title="sample", text="第一章\n正文\n\n第二行")]


def test_parse_rejects_unsupported_extension():
    with pytest.raises(ParseError, match="Unsupported file type"):
        parse_book_bytes("sample.pdf", b"%PDF")
