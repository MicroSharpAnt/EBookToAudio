from __future__ import annotations

from pathlib import Path
import subprocess
import zipfile


class AudioBuilder:
    def __init__(self, ffmpeg_path: str | None):
        self.ffmpeg_path = ffmpeg_path

    def merge_audio(self, input_paths: list[Path], output_path: Path) -> Path | None:
        if not self.ffmpeg_path or not input_paths:
            return None
        output_path.parent.mkdir(parents=True, exist_ok=True)
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
            return None
        return output_path

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
