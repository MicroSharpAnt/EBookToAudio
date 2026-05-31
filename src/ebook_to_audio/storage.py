from __future__ import annotations

from pathlib import Path
import re
from zipfile import ZIP_DEFLATED, ZipFile

from .models import TextMetadata


class PathSafetyError(ValueError):
    pass


class LocalStorage:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    def initialize(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def resolve_artifact(self, relative_path: str) -> Path:
        path = _safe_relative_path(relative_path)
        root = self.data_dir.resolve()
        candidate = (root / path).resolve()
        if not candidate.is_relative_to(root):
            raise PathSafetyError("artifact path escapes data directory")
        return candidate

    def book_dir(self, book_id: int) -> Path:
        return self.resolve_artifact(self.book_dir_path(book_id))

    def book_dir_path(self, book_id: int) -> str:
        return f"books/{_safe_id(book_id)}"

    def source_path(self, book_id: int, filename: str = "source.txt") -> str:
        return f"{self.book_dir_path(book_id)}/{_safe_filename(filename)}"

    def filtered_path(self, book_id: int) -> str:
        return f"{self.book_dir_path(book_id)}/filtered.txt"

    def cleaned_path(self, book_id: int) -> str:
        return f"{self.book_dir_path(book_id)}/cleaned.txt"

    def chapter_path(self, book_id: int, chapter_index: int) -> str:
        _validate_non_negative(chapter_index, "chapter_index")
        return f"{self.book_dir_path(book_id)}/chapters/{chapter_index:04d}.txt"

    def translation_path(self, book_id: int, chapter_index: int, segment_index: int) -> str:
        _validate_non_negative(chapter_index, "chapter_index")
        _validate_non_negative(segment_index, "segment_index")
        return f"{self.book_dir_path(book_id)}/translations/{chapter_index:04d}-{segment_index:04d}.txt"

    def audio_path(
        self,
        book_id: int,
        chapter_index: int,
        segment_index: int,
        extension: str = "mp3",
        job_id: int | None = None,
    ) -> str:
        _validate_non_negative(chapter_index, "chapter_index")
        _validate_non_negative(segment_index, "segment_index")
        if job_id is not None:
            return (
                f"{self.book_dir_path(book_id)}/audio/{chapter_index:04d}/"
                f"jobs/{_safe_id(job_id)}/{segment_index:04d}.{_safe_extension(extension)}"
            )
        return f"{self.book_dir_path(book_id)}/audio/{chapter_index:04d}-{segment_index:04d}.{_safe_extension(extension)}"

    def write_text(self, relative_path: str, text: str) -> Path:
        path = self.resolve_artifact(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def read_text(self, relative_path: str) -> str:
        return self.resolve_artifact(relative_path).read_text(encoding="utf-8")

    def create_zip(self, output_relative_path: str, artifact_paths: list[str]) -> Path:
        output_path = self.resolve_artifact(output_relative_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(output_path, "w", compression=ZIP_DEFLATED) as archive:
            for artifact_path in artifact_paths:
                source_path = self.resolve_artifact(artifact_path)
                archive.write(source_path, arcname=artifact_path)
        return output_path


def chapter_metadata(text: str) -> TextMetadata:
    paragraphs = [paragraph for paragraph in re.split(r"\n\s*\n", text.strip()) if paragraph.strip()]
    char_count = len("".join(text.split()))
    return TextMetadata(char_count=char_count, paragraph_count=len(paragraphs))


def _safe_relative_path(value: str) -> Path:
    if not value or "\\" in value:
        raise PathSafetyError("unsafe artifact path")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise PathSafetyError("unsafe artifact path")
    return path


def _safe_id(value: int) -> str:
    if value < 0:
        raise PathSafetyError("unsafe storage id")
    return str(value)


def _validate_non_negative(value: int, name: str) -> None:
    if value < 0:
        raise PathSafetyError(f"unsafe {name}")


def _safe_filename(value: str) -> str:
    if not value or "/" in value or "\\" in value or value in {".", ".."}:
        raise PathSafetyError("unsafe filename")
    return value


def _safe_extension(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9]+", value):
        raise PathSafetyError("unsafe extension")
    return value
