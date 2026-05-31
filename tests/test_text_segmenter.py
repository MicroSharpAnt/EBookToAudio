from ebook_to_audio.text_segmenter import split_text


def test_split_text_prefers_paragraph_boundaries():
    text = "第一段。" * 10 + "\n\n" + "第二段。" * 10

    segments = split_text(text, max_chars=40)

    assert len(segments) >= 2
    assert "".join(segments).replace("\n\n", "") == text.replace("\n\n", "")
    assert all(len(segment) <= 40 for segment in segments)
