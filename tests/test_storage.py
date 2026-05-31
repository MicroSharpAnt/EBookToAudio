from pathlib import Path

import pytest

from ebook_to_audio.storage import LocalStorage, PathSafetyError, chapter_metadata


def test_storage_rejects_paths_outside_data_dir(tmp_path: Path):
    storage = LocalStorage(tmp_path)

    with pytest.raises(PathSafetyError):
        storage.resolve_artifact("../outside.txt")


def test_chapter_metadata_counts_chars_and_paragraphs():
    metadata = chapter_metadata("第一段\n\n第二段\n第三行")

    assert metadata.char_count == len("第一段第二段第三行")
    assert metadata.paragraph_count == 2
