from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path
import shutil
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from .book_parser import ParseError, parse_book_bytes
from .chapter_splitter import split_into_chapters
from .config import (
    AppConfig,
    ConfigError,
    LimitsConfig,
    PromptConfig,
    ProviderConfig,
    TTSConfig,
    TranslationConfig,
    load_config,
)
from .job_runner import JobRunner
from .models import Book, Chapter, Job, JobKind
from .repository import Repository
from .storage import LocalStorage, PathSafetyError, chapter_metadata
from .text_cleaner import clean_text


DEFAULT_MAX_UPLOAD_BYTES = 1_000_000
UPLOAD_READ_CHUNK_BYTES = 64 * 1024


class CleanRequest(BaseModel):
    operations: list[str]


class ChapterUpdateRequest(BaseModel):
    title: str
    text: str


def create_app(
    data_dir: Path | str | None = None,
    config_path: Path | str | None = None,
    autostart_jobs: bool = True,
) -> FastAPI:
    config_file = Path(config_path or "config.yaml")
    loaded_config, config_error = _load_or_default_config(
        config_file,
        Path(data_dir) if data_dir is not None else None,
    )
    resolved_data_dir = Path(data_dir) if data_dir is not None else loaded_config.data_dir

    storage = LocalStorage(resolved_data_dir)
    storage.initialize()
    repository = Repository(resolved_data_dir / "app.db")
    repository.initialize()
    runner = JobRunner(repository, storage, tts_max_chars=loaded_config.tts.max_request_chars)

    if autostart_jobs:
        repository.reset_running_to_pending()

    app = FastAPI(title="EBook To Audio")
    app.state.config = loaded_config
    app.state.config_error = config_error
    app.state.config_path = config_file
    app.state.repository = repository
    app.state.storage = storage
    app.state.runner = runner

    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        return {
            "loaded": config_error is None,
            "error": config_error,
            **_config_metadata(loaded_config),
        }

    @app.post("/api/books")
    async def upload_book(file: UploadFile = File(...)) -> dict[str, Any]:
        max_upload_bytes = loaded_config.limits.max_upload_bytes
        content = await _read_upload_bytes(file, max_upload_bytes)

        filename = _sanitize_upload_filename(file.filename or "book.txt")
        try:
            parsed = parse_book_bytes(filename, content)
        except ParseError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        book = repository.create_book(
            title=parsed.title,
            source_format=parsed.source_format,
            original_filename=filename,
            source_path="pending",
            filtered_path="pending",
            cleaned_path="pending",
        )
        try:
            source_path = storage.source_path(book.id, filename)
            filtered_path = storage.filtered_path(book.id)
            cleaned_path = storage.cleaned_path(book.id)
            storage.resolve_artifact(source_path).parent.mkdir(parents=True, exist_ok=True)
            storage.resolve_artifact(source_path).write_bytes(content)
            storage.write_text(filtered_path, parsed.full_text)
            storage.write_text(cleaned_path, parsed.full_text)
            return _book_dict(repository.update_book_paths(book.id, source_path, filtered_path, cleaned_path))
        except (OSError, PathSafetyError) as exc:
            _discard_partial_book(repository, storage, book.id)
            raise HTTPException(status_code=500, detail="could not upload book") from exc

    @app.get("/api/books")
    def list_books() -> list[dict[str, Any]]:
        return [_book_dict(book) for book in repository.list_books()]

    @app.get("/api/books/current")
    def get_current_book() -> dict[str, Any]:
        books = repository.list_books()
        if not books:
            raise HTTPException(status_code=404, detail="no books")
        return _book_dict(books[0])

    @app.get("/api/books/{book_id}")
    def get_book(book_id: int) -> dict[str, Any]:
        return _book_dict(_get_book_or_404(repository, book_id))

    @app.get("/api/books/{book_id}/download.txt", response_class=PlainTextResponse)
    def download_book_text(book_id: int) -> PlainTextResponse:
        book = _get_book_or_404(repository, book_id)
        return PlainTextResponse(_read_artifact_text(storage, book.cleaned_path), media_type="text/plain; charset=utf-8")

    @app.get("/api/books/{book_id}/download/full.txt", response_class=PlainTextResponse)
    def download_full_book_text(book_id: int) -> PlainTextResponse:
        book = _get_book_or_404(repository, book_id)
        return PlainTextResponse(_read_artifact_text(storage, book.filtered_path), media_type="text/plain; charset=utf-8")

    @app.get("/api/books/{book_id}/download/cleaned.txt", response_class=PlainTextResponse)
    def download_cleaned_book_text(book_id: int) -> PlainTextResponse:
        book = _get_book_or_404(repository, book_id)
        return PlainTextResponse(_read_artifact_text(storage, book.cleaned_path), media_type="text/plain; charset=utf-8")

    @app.post("/api/books/{book_id}/clean")
    def clean_book(book_id: int, request: CleanRequest) -> dict[str, Any]:
        book = _get_book_or_404(repository, book_id)
        try:
            result = clean_text(_read_artifact_text(storage, book.cleaned_path), request.operations)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        storage.write_text(book.cleaned_path, result.text)
        metadata = chapter_metadata(result.text)
        return {
            "book": _book_dict(book),
            "results": [_clean_result_dict(clean_result) for clean_result in result.results],
            "char_count": metadata.char_count,
            "paragraph_count": metadata.paragraph_count,
        }

    @app.post("/api/books/{book_id}/split")
    def split_book(book_id: int) -> dict[str, Any]:
        book = _get_book_or_404(repository, book_id)
        chapters = split_into_chapters(_read_artifact_text(storage, book.cleaned_path))
        job = repository.create_job(book.id, None, JobKind.SPLIT, len(chapters), options={})
        try:
            chapter_rows: list[tuple[int, str, str, int, int]] = []
            for index, split_chapter in enumerate(chapters):
                text_path = _staged_chapter_path(book.id, job.id, index)
                storage.write_text(text_path, split_chapter.text)
                metadata = chapter_metadata(split_chapter.text)
                chapter_rows.append(
                    (
                        index,
                        split_chapter.title,
                        text_path,
                        metadata.char_count,
                        metadata.paragraph_count,
                    )
                )
            repository.replace_chapters_for_book(book.id, chapter_rows)
            job = repository.complete_job(job.id, completed_units=len(chapters))
        except Exception:
            repository.fail_job(job.id, "could not split book")
            job = repository.get_job(job.id)
            return JSONResponse(
                status_code=500,
                content={"detail": "could not split book", "job": _job_dict(job)},
            )
        return {"job": _job_dict(job)}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: int) -> dict[str, Any]:
        return _job_dict(_get_job_or_404(repository, job_id))

    @app.get("/api/books/{book_id}/chapters")
    def list_chapters(book_id: int) -> list[dict[str, Any]]:
        _get_book_or_404(repository, book_id)
        return [_chapter_dict(chapter) for chapter in repository.list_chapters(book_id)]

    @app.get("/api/chapters/{chapter_id}")
    def get_chapter(chapter_id: int) -> dict[str, Any]:
        return _chapter_dict(_get_chapter_or_404(repository, chapter_id))

    @app.put("/api/chapters/{chapter_id}")
    def update_chapter(chapter_id: int, request: ChapterUpdateRequest) -> dict[str, Any]:
        chapter = _get_chapter_or_404(repository, chapter_id)
        storage.write_text(chapter.text_path, request.text)
        metadata = chapter_metadata(request.text)
        updated = repository.update_chapter(
            chapter.id,
            request.title,
            chapter.text_path,
            metadata.char_count,
            metadata.paragraph_count,
        )
        return _chapter_dict(updated)

    @app.get("/api/chapters/{chapter_id}/download.txt", response_class=PlainTextResponse)
    def download_chapter_text(chapter_id: int) -> PlainTextResponse:
        chapter = _get_chapter_or_404(repository, chapter_id)
        return PlainTextResponse(_read_artifact_text(storage, chapter.text_path), media_type="text/plain; charset=utf-8")

    @app.get("/api/chapters/{chapter_id}/download.zip")
    def download_chapter_zip(chapter_id: int) -> FileResponse:
        chapter = _get_chapter_or_404(repository, chapter_id)
        try:
            zip_path = storage.create_zip(
                f"books/{chapter.book_id}/downloads/chapter-{chapter.chapter_index:04d}.zip",
                [chapter.text_path],
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="chapter artifact not found") from exc
        except (OSError, PathSafetyError) as exc:
            raise HTTPException(status_code=500, detail="could not create chapter archive") from exc
        return FileResponse(zip_path, filename=f"chapter-{chapter.chapter_index + 1}.zip", media_type="application/zip")

    return app


def _load_or_default_config(config_path: Path, data_dir: Path | None) -> tuple[AppConfig, str | None]:
    try:
        config = load_config(config_path)
        if data_dir is None:
            return config, None
        return replace(config, data_dir=data_dir), None
    except ConfigError:
        return _default_config(data_dir or Path("data"), None), "configuration invalid"


def _default_config(data_dir: Path, limits: LimitsConfig | None) -> AppConfig:
    provider = ProviderConfig(base_url="", api_key="", model="")
    translation = TranslationConfig(
        segment_limit=1200,
        request_timeout_seconds=45,
        max_retries=2,
        prompt=PromptConfig(),
        providers={"default": provider},
        active_provider_name="default",
    )
    tts = TTSConfig(
        base_url="",
        api_key="",
        model="",
        default_voice="",
        max_request_chars=900,
        default_parallel_segments=2,
    )
    return AppConfig(
        active_translation_provider="default",
        data_dir=data_dir,
        translation=translation,
        tts=tts,
        limits=limits
        or LimitsConfig(
            max_upload_bytes=DEFAULT_MAX_UPLOAD_BYTES,
            max_parallel_translation_segments=3,
            max_parallel_tts_segments=4,
        ),
    )


def _book_dict(book: Book) -> dict[str, Any]:
    return asdict(book)


def _chapter_dict(chapter: Chapter) -> dict[str, Any]:
    return asdict(chapter)


def _job_dict(job: Job) -> dict[str, Any]:
    data = asdict(job)
    data["kind"] = str(job.kind)
    data["status"] = str(job.status)
    return data


async def _read_upload_bytes(file: UploadFile, max_upload_bytes: int) -> bytes:
    content = bytearray()
    while True:
        chunk = await file.read(UPLOAD_READ_CHUNK_BYTES)
        if not chunk:
            return bytes(content)
        content.extend(chunk)
        if len(content) > max_upload_bytes:
            raise HTTPException(status_code=413, detail="uploaded file is too large")


def _config_metadata(config: AppConfig) -> dict[str, Any]:
    metadata = config.safe_metadata()
    metadata.pop("data_dir", None)
    return metadata


def _clean_result_dict(clean_result: Any) -> dict[str, Any]:
    data = asdict(clean_result)
    data.pop("text", None)
    return data


def _sanitize_upload_filename(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name
    if not name or name in {".", ".."}:
        raise HTTPException(status_code=400, detail="invalid upload filename")
    return name


def _staged_chapter_path(book_id: int, job_id: int, chapter_index: int) -> str:
    return f"books/{book_id}/chapters/split-{job_id}/{chapter_index:04d}.txt"


def _discard_partial_book(repository: Repository, storage: LocalStorage, book_id: int) -> None:
    repository.delete_book(book_id)
    try:
        shutil.rmtree(storage.book_dir(book_id), ignore_errors=True)
    except PathSafetyError:
        pass


def _get_book_or_404(repository: Repository, book_id: int) -> Book:
    try:
        return repository.get_book(book_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="book not found") from exc


def _get_chapter_or_404(repository: Repository, chapter_id: int) -> Chapter:
    try:
        return repository.get_chapter(chapter_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="chapter not found") from exc


def _get_job_or_404(repository: Repository, job_id: int) -> Job:
    try:
        return repository.get_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc


def _read_artifact_text(storage: LocalStorage, relative_path: str) -> str:
    try:
        return storage.read_text(relative_path)
    except (OSError, PathSafetyError) as exc:
        raise HTTPException(status_code=404, detail="artifact not found") from exc
