from ebook_to_audio.text_cleaner import (
    clean_text,
    normalize_spacing,
    remove_decorative_characters,
    remove_repeated_noise_lines,
    remove_watermarks,
)


def test_remove_watermarks_removes_common_source_lines():
    text = "第一章\n本书来自 www.example.com\n正文\n扫码关注公众号\n下一段"

    result = remove_watermarks(text)

    assert result.text == "第一章\n正文\n下一段"
    assert result.removed_lines == 2


def test_normalize_spacing_collapses_blank_lines_and_invisible_chars():
    result = normalize_spacing("第一章\u200b　　正文\t\t内容\n\n\n\n第二段  结尾  ")

    assert result.text == "第一章 正文 内容\n\n第二段 结尾"
    assert result.after_chars < result.before_chars


def test_remove_repeated_noise_lines_only_removes_short_repeated_noise():
    text = "广告发布页\n正文一\n广告发布页\n正文二\n广告发布页\n正文三"

    result = remove_repeated_noise_lines(text, min_repeats=3)

    assert result.text == "正文一\n正文二\n正文三"
    assert result.removed_lines == 3


def test_remove_decorative_characters_removes_separator_lines():
    result = remove_decorative_characters("正文\n**************\n----------\n下一段")

    assert result.text == "正文\n下一段"


def test_clean_text_applies_selected_operations():
    result = clean_text("水印：www.test.com\n正文\u200b  内容", ["remove_watermarks", "normalize_spacing"])

    assert result.text == "正文 内容"
    assert [item.operation for item in result.results] == ["remove_watermarks", "normalize_spacing"]
