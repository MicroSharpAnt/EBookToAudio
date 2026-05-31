from ebook_to_audio.chapter_splitter import split_into_chapters


def test_split_into_chapters_detects_chinese_headings():
    text = "序\n开头\n第一章 初见\n正文一\n第二章 风波\n正文二"

    chapters = split_into_chapters(text, fallback_chars=100)

    assert [chapter.title for chapter in chapters] == ["序", "第一章 初见", "第二章 风波"]
    assert chapters[1].text == "正文一"


def test_split_into_chapters_falls_back_to_size_chunks():
    chapters = split_into_chapters("甲" * 12, fallback_chars=5)

    assert [chapter.title for chapter in chapters] == ["第 1 段", "第 2 段", "第 3 段"]
    assert [len(chapter.text) for chapter in chapters] == [5, 5, 2]
