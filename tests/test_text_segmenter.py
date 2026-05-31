from ebook_to_audio.text_segmenter import split_text


def test_split_text_prefers_paragraph_boundaries():
    text = "第一段。" * 10 + "\n\n" + "第二段。" * 10

    segments = split_text(text, max_chars=40)

    assert len(segments) >= 2
    assert "".join(segments) == text
    assert all(len(segment) <= 40 for segment in segments)


def test_split_text_preserves_blank_line_separator_across_segments():
    text = "aa\n\nbb"

    segments = split_text(text, max_chars=3)

    assert "".join(segments) == text
    assert all(len(segment) <= 3 for segment in segments)


def test_split_text_hard_splits_tiny_limits_without_losing_separators():
    text = "aa\n\nbb"

    segments = split_text(text, max_chars=1)

    assert segments == ["a", "a", "\n", "\n", "b", "b"]
    assert "".join(segments) == text


def test_split_text_prefers_line_boundaries():
    text = "第一行\n第二行\n第三行"

    segments = split_text(text, max_chars=6)

    assert segments == ["第一行\n", "第二行\n", "第三行"]
    assert "".join(segments) == text
    assert all(len(segment) <= 6 for segment in segments)


def test_split_text_hard_splits_long_lines():
    text = "abcdefghij"

    segments = split_text(text, max_chars=4)

    assert segments == ["abcd", "efgh", "ij"]
    assert "".join(segments) == text
    assert all(len(segment) <= 4 for segment in segments)
