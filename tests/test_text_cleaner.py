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


def test_remove_watermarks_preserves_domain_mentions_in_prose():
    text = "作者提到 example.com 这个域名。"

    result = remove_watermarks(text)

    assert result.text == text
    assert result.removed_lines == 0


def test_line_removal_functions_preserve_no_match_text_exactly():
    text = "正文\n\n下一段\n"

    assert remove_watermarks(text).text == text
    assert remove_repeated_noise_lines(text).text == text
    assert remove_decorative_characters(text).text == text


def test_normalize_spacing_collapses_blank_lines_and_invisible_chars():
    result = normalize_spacing("第一章\u200b　　正文\t\t内容\n\n\n\n第二段  结尾  ")

    assert result.text == "第一章正文内容\n\n第二段结尾"
    assert result.after_chars < result.before_chars


def test_normalize_spacing_removes_artifact_spaces_inside_chinese_prose():
    text = (
        "现在是早已并屋子一起卖给朱 文公的子孙了，"
        "其中似乎确凿只有一些野草 ；但那时却是我的乐园。"
        "鸣 蝉在树叶里长吟， 蟋蟀们在这里弹琴。使用 DeepSeek 翻译。"
    )

    result = normalize_spacing(text)

    assert "朱文公" in result.text
    assert "野草；" in result.text
    assert "鸣蝉" in result.text
    assert "长吟，蟋蟀们" in result.text
    assert "DeepSeek 翻译" in result.text


def test_remove_repeated_noise_lines_only_removes_short_repeated_noise():
    text = "广告发布页\n正文一\n广告发布页\n正文二\n广告发布页\n正文三"

    result = remove_repeated_noise_lines(text, min_repeats=3)

    assert result.text == "正文一\n正文二\n正文三"
    assert result.removed_lines == 3


def test_remove_repeated_noise_lines_preserves_repeated_ordinary_prose():
    text = "是的。\n正文一\n是的。\n正文二\n是的。\n正文三"

    result = remove_repeated_noise_lines(text, min_repeats=3)

    assert result.text == text
    assert result.removed_lines == 0


def test_remove_decorative_characters_removes_separator_lines():
    result = remove_decorative_characters("正文\n**************\n----------\n下一段")

    assert result.text == "正文\n下一段"


def test_clean_text_applies_selected_operations():
    result = clean_text("水印：www.test.com\n正文\u200b  内容", ["remove_watermarks", "normalize_spacing"])

    assert result.text == "正文内容"
    assert [item.operation for item in result.results] == ["remove_watermarks", "normalize_spacing"]


def test_clean_text_rejects_unknown_operation():
    try:
        clean_text("正文", ["unknown"])
    except ValueError as error:
        assert str(error) == "Unknown text cleaning operation: unknown"
    else:
        raise AssertionError("Expected ValueError")
