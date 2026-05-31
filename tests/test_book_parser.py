from io import BytesIO

import pytest
from ebooklib import epub

from ebook_to_audio.book_parser import ParseError, ParsedChapter, parse_book_bytes


def _make_epub_bytes() -> bytes:
    book = epub.EpubBook()
    book.set_identifier("sample-id")
    book.set_title("Sample EPUB")
    book.set_language("zh")

    first_chapter = epub.EpubHtml(title="第二章", file_name="chapter_2.xhtml", lang="zh")
    first_chapter.content = """
        <html>
            <body>
                <h1>第二章</h1>
                <p>第二章正文</p>
                <script>ignored script</script>
            </body>
        </html>
    """

    second_chapter = epub.EpubHtml(title="第一章", file_name="chapter_1.xhtml", lang="zh")
    second_chapter.content = """
        <html>
            <body>
                <style>.ignored { color: red; }</style>
                <h1>第一章</h1>
                <p>第一章正文</p>
            </body>
        </html>
    """

    book.add_item(first_chapter)
    book.add_item(second_chapter)
    book.add_item(epub.EpubNav())
    book.add_item(epub.EpubNcx())
    book.spine = ["nav", first_chapter, second_chapter]

    output = BytesIO()
    epub.write_epub(output, book)
    return output.getvalue()


def _make_epub_with_toc_titles_bytes() -> bytes:
    book = epub.EpubBook()
    book.set_identifier("toc-title-sample")
    book.set_title("TOC Title Sample")
    book.set_language("en")

    table_of_contents = epub.EpubHtml(title="Contents", file_name="contents.xhtml", lang="en")
    table_of_contents.content = """
        <html>
            <body>
                <p>Table of Contents</p>
                <p>Story One</p>
                <p>Story Two</p>
            </body>
        </html>
    """

    first = epub.EpubHtml(title="", file_name="story-one.xhtml", lang="en")
    first.content = """
        <html>
            <body>
                <p>THE PLUTONIAN FIRE</p>
                <p>First story text.</p>
            </body>
        </html>
    """

    second = epub.EpubHtml(title="", file_name="story-two.xhtml", lang="en")
    second.content = """
        <html>
            <body>
                <p>THE GIFT OF THE MAGI</p>
                <p>Second story text.</p>
            </body>
        </html>
    """

    book.add_item(table_of_contents)
    book.add_item(first)
    book.add_item(second)
    book.toc = (
        epub.Link("story-one.xhtml", "Story One", "story-one"),
        epub.Link("story-two.xhtml", "Story Two", "story-two"),
    )
    book.add_item(epub.EpubNav())
    book.add_item(epub.EpubNcx())
    book.spine = [table_of_contents, first, second]

    output = BytesIO()
    epub.write_epub(output, book)
    return output.getvalue()


def test_parse_txt_normalizes_bom_and_line_endings():
    parsed = parse_book_bytes("sample.txt", "\ufeff第一章\r\n正文\r\n\r\n第二行".encode("utf-8"))

    assert parsed.title == "sample"
    assert parsed.source_format == "txt"
    assert parsed.full_text == "第一章\n正文\n\n第二行"
    assert parsed.initial_chapters == (ParsedChapter(title="sample", text="第一章\n正文\n\n第二行"),)


def test_parse_epub_uses_spine_order_and_skips_navigation_documents():
    parsed = parse_book_bytes("sample.epub", _make_epub_bytes())

    assert parsed.title == "sample"
    assert parsed.source_format == "epub"
    assert parsed.initial_chapters == (
        ParsedChapter(title="第二章", text="第二章\n第二章正文"),
        ParsedChapter(title="第一章", text="第一章\n第一章正文"),
    )
    assert parsed.full_text == "第二章\n第二章正文\n\n第一章\n第一章正文"


def test_parse_epub_uses_toc_titles_and_skips_toc_only_documents():
    parsed = parse_book_bytes("stories.epub", _make_epub_with_toc_titles_bytes())

    assert parsed.initial_chapters == (
        ParsedChapter(title="Story One", text="THE PLUTONIAN FIRE\nFirst story text."),
        ParsedChapter(title="Story Two", text="THE GIFT OF THE MAGI\nSecond story text."),
    )
    assert "Table of Contents" not in parsed.full_text


def test_parse_epub_rejects_invalid_bytes():
    with pytest.raises(ParseError, match="Could not parse EPUB file"):
        parse_book_bytes("bad.epub", b"not an epub")


def test_parse_rejects_unsupported_extension():
    with pytest.raises(ParseError, match="Unsupported file type"):
        parse_book_bytes("sample.pdf", b"%PDF")
