from ebook_to_audio.chapter_splitter import split_into_chapters


def test_split_into_chapters_detects_chinese_headings():
    text = "序\n开头\n第一章 初见\n正文一\n第二章 风波\n正文二"

    chapters = split_into_chapters(text, fallback_chars=100)

    assert [chapter.title for chapter in chapters] == ["序", "第一章 初见", "第二章 风波"]
    assert chapters[1].text == "正文一"


def test_split_into_chapters_preserves_front_matter_before_headings():
    text = "作者说明\n献词\n第一章 初见\n正文一\n第二章 风波\n正文二"

    chapters = split_into_chapters(text, fallback_chars=100)

    assert [chapter.title for chapter in chapters] == ["前言", "第一章 初见", "第二章 风波"]
    assert chapters[0].text == "作者说明\n献词"
    assert chapters[1].text == "正文一"


def test_split_into_chapters_falls_back_to_size_chunks():
    chapters = split_into_chapters("甲" * 12, fallback_chars=5)

    assert [chapter.title for chapter in chapters] == ["第 1 段", "第 2 段", "第 3 段"]
    assert [len(chapter.text) for chapter in chapters] == [5, 5, 2]


def test_split_into_chapters_falls_back_when_only_one_heading_is_found():
    chapters = split_into_chapters("第一章 孤章\n正文内容", fallback_chars=5)

    assert [chapter.title for chapter in chapters] == ["第 1 段", "第 2 段", "第 3 段"]
    assert "".join(chapter.text for chapter in chapters) == "第一章 孤章\n正文内容"


def test_split_into_chapters_detects_supported_heading_variants():
    cases = [
        ("Chapter 1 Arrival", "Chapter 2 Trouble"),
        ("Chapter IV Arrival", "Chapter V Trouble"),
        ("I. Arrival", "II. Trouble"),
        ("卷一 风起", "卷二 云涌"),
        ("前言", "第一章 正文"),
        ("楔子", "第一章 正文"),
        ("尾声", "后记"),
    ]

    for first_heading, second_heading in cases:
        text = f"{first_heading}\n内容一\n{second_heading}\n内容二"

        chapters = split_into_chapters(text, fallback_chars=100)

        assert [chapter.title for chapter in chapters] == [first_heading, second_heading]
        assert [chapter.text for chapter in chapters] == ["内容一", "内容二"]
