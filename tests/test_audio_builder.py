from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest

from ebook_to_audio.audio_builder import AudioBuilder


def test_audio_builder_without_ffmpeg_returns_none(tmp_path: Path):
    builder = AudioBuilder(ffmpeg_path=None)

    assert builder.merge_audio([tmp_path / "a.wav"], tmp_path / "out.wav") is None


def test_audio_builder_build_zip_uses_safe_relative_paths(tmp_path: Path):
    job_dir = tmp_path / "job"
    chapter = job_dir / "chapters" / "000.wav"
    segment = job_dir / "segments" / "000-000.wav"
    chapter.parent.mkdir(parents=True)
    segment.parent.mkdir(parents=True)
    chapter.write_bytes(b"RIFFxxxxWAVEchapter")
    segment.write_bytes(b"RIFFxxxxWAVEsegment")

    zip_path = AudioBuilder(ffmpeg_path=None).build_zip(job_dir, [chapter], [segment])

    with ZipFile(zip_path) as archive:
        assert sorted(archive.namelist()) == [
            "chapters/000.wav",
            "segments/000-000.wav",
        ]
        assert all(not name.startswith("/") and ".." not in Path(name).parts for name in archive.namelist())


def test_audio_builder_rejects_zip_paths_outside_job_dir(tmp_path: Path):
    job_dir = tmp_path / "job"
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"RIFFxxxxWAVEoutside")

    with pytest.raises(ValueError, match="Unsafe archive path"):
        AudioBuilder(ffmpeg_path=None).build_zip(job_dir, [outside], [])
