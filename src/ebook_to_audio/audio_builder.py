from __future__ import annotations

from pathlib import Path
import math
import shutil
import subprocess
import tempfile
import wave
import zipfile


DEFAULT_MAX_SILENCE_MS = 1200
DEFAULT_SILENCE_THRESHOLD = 96


class AudioBuilder:
    def __init__(self, ffmpeg_path: str | None = None):
        self._explicit_ffmpeg_path = ffmpeg_path is not None
        self.ffmpeg_path = ffmpeg_path or shutil.which("ffmpeg")

    def merge_audio(self, input_paths: list[Path], output_path: Path) -> Path | None:
        if not input_paths:
            return None
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="ebook-to-audio-", dir=output_path.parent) as temp_dir:
            prepared_paths = _collapse_inputs_for_merge(input_paths, Path(temp_dir))
            if self.ffmpeg_path and self._merge_with_ffmpeg(prepared_paths, output_path):
                return output_path
            if self._explicit_ffmpeg_path:
                return None
            return _merge_wav_files(prepared_paths, output_path)

    def _merge_with_ffmpeg(self, input_paths: list[Path], output_path: Path) -> bool:
        list_path = output_path.parent / "ffmpeg-list.txt"
        list_path.write_text(
            "\n".join(f"file '{_escape_concat_path(path)}'" for path in input_paths),
            encoding="utf-8",
        )
        try:
            subprocess.run(
                [
                    self.ffmpeg_path,
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(list_path),
                    "-c",
                    "copy",
                    str(output_path),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError):
            output_path.unlink(missing_ok=True)
            return False
        finally:
            list_path.unlink(missing_ok=True)
        return True

    def build_zip(
        self,
        job_dir: Path,
        chapter_paths: list[Path],
        segment_paths: list[Path],
    ) -> Path:
        output_dir = job_dir / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        zip_path = output_dir / "book.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in chapter_paths:
                archive.write(path, _archive_name(job_dir, path))
            for path in segment_paths:
                archive.write(path, _archive_name(job_dir, path))
        return zip_path

    def waveform(self, input_path: Path, buckets: int = 600) -> dict[str, object] | None:
        return wav_waveform(input_path, buckets=buckets)


def _archive_name(job_dir: Path, path: Path) -> str:
    try:
        relative_path = path.relative_to(job_dir)
    except ValueError as exc:
        raise ValueError(f"Unsafe archive path: {path}") from exc
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError(f"Unsafe archive path: {path}")
    archive_name = relative_path.as_posix()
    if "\\" in archive_name:
        raise ValueError(f"Unsafe archive path: {path}")

    resolved_job_dir = job_dir.resolve()
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(resolved_job_dir)
    except ValueError as exc:
        raise ValueError(f"Unsafe archive path: {path}") from exc
    return archive_name


def _escape_concat_path(path: Path) -> str:
    return path.as_posix().replace("\\", "\\\\").replace("'", "'\\''")


def _merge_wav_files(input_paths: list[Path], output_path: Path) -> Path | None:
    params = None
    try:
        with wave.open(str(output_path), "wb") as output:
            for input_path in input_paths:
                with wave.open(str(input_path), "rb") as source:
                    source_params = (
                        source.getnchannels(),
                        source.getsampwidth(),
                        source.getframerate(),
                        source.getcomptype(),
                        source.getcompname(),
                    )
                    if params is None:
                        params = source_params
                        output.setnchannels(source_params[0])
                        output.setsampwidth(source_params[1])
                        output.setframerate(source_params[2])
                        output.setcomptype(source_params[3], source_params[4])
                    elif source_params != params:
                        raise ValueError("WAV files do not share audio parameters")
                    output.writeframes(source.readframes(source.getnframes()))
    except (OSError, EOFError, wave.Error, ValueError):
        output_path.unlink(missing_ok=True)
        return None
    return output_path


def _collapse_inputs_for_merge(input_paths: list[Path], temp_dir: Path) -> list[Path]:
    prepared_paths: list[Path] = []
    for index, input_path in enumerate(input_paths):
        collapsed_path = temp_dir / f"{index:04d}.wav"
        if _collapse_long_silences(input_path, collapsed_path):
            prepared_paths.append(collapsed_path)
        else:
            prepared_paths.append(input_path)
    return prepared_paths


def _collapse_long_silences(
    input_path: Path,
    output_path: Path,
    *,
    max_silence_ms: int = DEFAULT_MAX_SILENCE_MS,
    threshold: int = DEFAULT_SILENCE_THRESHOLD,
) -> bool:
    try:
        with wave.open(str(input_path), "rb") as source:
            params = source.getparams()
            if source.getsampwidth() != 2 or source.getcomptype() != "NONE":
                return False
            channels = source.getnchannels()
            framerate = source.getframerate()
            frames = source.readframes(source.getnframes())
    except (OSError, EOFError, wave.Error):
        return False

    frame_width = channels * 2
    if frame_width <= 0 or not frames:
        return False

    max_silence_frames = max(1, int(framerate * (max_silence_ms / 1000)))
    chunks: list[bytes] = []
    silence_run: list[bytes] = []
    changed = False

    for offset in range(0, len(frames), frame_width):
        frame = frames[offset : offset + frame_width]
        if len(frame) < frame_width:
            continue
        if _is_silent_frame_16bit(frame, threshold):
            silence_run.append(frame)
            continue
        if silence_run:
            keep = min(len(silence_run), max_silence_frames)
            if keep < len(silence_run):
                changed = True
            chunks.append(b"".join(silence_run[:keep]))
            silence_run = []
        chunks.append(frame)

    if silence_run:
        keep = min(len(silence_run), max_silence_frames)
        if keep < len(silence_run):
            changed = True
        chunks.append(b"".join(silence_run[:keep]))

    if not changed:
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with wave.open(str(output_path), "wb") as output:
            output.setparams(params)
            output.writeframes(b"".join(chunks))
    except (OSError, wave.Error):
        output_path.unlink(missing_ok=True)
        return False
    return True


def _is_silent_frame_16bit(frame: bytes, threshold: int) -> bool:
    return all(
        abs(int.from_bytes(frame[offset : offset + 2], "little", signed=True)) <= threshold
        for offset in range(0, len(frame), 2)
    )


def wav_waveform(input_path: Path, buckets: int = 600) -> dict[str, object] | None:
    try:
        with wave.open(str(input_path), "rb") as source:
            if source.getsampwidth() != 2 or source.getcomptype() != "NONE":
                return None
            channels = source.getnchannels()
            framerate = source.getframerate()
            frame_count = source.getnframes()
            frames = source.readframes(frame_count)
    except (OSError, EOFError, wave.Error):
        return None

    frame_width = channels * 2
    if frame_width <= 0 or frame_count <= 0:
        return None
    bucket_count = max(1, min(buckets, frame_count))
    frames_per_bucket = max(1, math.ceil(frame_count / bucket_count))
    peaks: list[float] = []
    max_sample = 32768

    for bucket_start in range(0, frame_count, frames_per_bucket):
        bucket_end = min(frame_count, bucket_start + frames_per_bucket)
        peak = 0
        for frame_index in range(bucket_start, bucket_end):
            offset = frame_index * frame_width
            frame = frames[offset : offset + frame_width]
            for sample_offset in range(0, len(frame), 2):
                sample = int.from_bytes(frame[sample_offset : sample_offset + 2], "little", signed=True)
                peak = max(peak, abs(sample))
        peaks.append(round(min(1.0, peak / max_sample), 4))

    return {
        "sample_rate": framerate,
        "duration_seconds": round(frame_count / framerate, 3),
        "peaks": peaks,
    }
