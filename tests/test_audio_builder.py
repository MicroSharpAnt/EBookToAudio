from __future__ import annotations

from pathlib import Path
import wave
from zipfile import ZipFile

import pytest

from ebook_to_audio import audio_builder
from ebook_to_audio.audio_builder import AudioBuilder


def test_audio_builder_without_ffmpeg_merges_compatible_wav_files(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(audio_builder.shutil, "which", lambda _name: None)
    first = _write_wav(tmp_path / "a.wav", frames=4)
    second = _write_wav(tmp_path / "b.wav", frames=6)
    builder = AudioBuilder(ffmpeg_path=None)

    output = builder.merge_audio([first, second], tmp_path / "out.wav")

    assert output == tmp_path / "out.wav"
    with wave.open(str(output), "rb") as merged:
        assert merged.getnframes() == 10


def test_audio_builder_with_invalid_ffmpeg_path_returns_none(tmp_path: Path):
    source = tmp_path / "a.wav"
    source.write_bytes(b"RIFFxxxxWAVEfake")
    builder = AudioBuilder(ffmpeg_path=str(tmp_path / "missing-ffmpeg"))

    assert builder.merge_audio([source], tmp_path / "out.wav") is None


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


def _write_wav(path: Path, frames: int) -> Path:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * frames)
    return path
