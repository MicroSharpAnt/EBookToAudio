from __future__ import annotations

from dataclasses import asdict, replace
import io
from pathlib import Path
import shutil
from typing import Any, Literal
import wave

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from .audio_builder import AudioBuilder
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
from .job_runner import JobRunner, _chapter_segments
from .mimo_client import MimoTTSClient, MissingMimoApiKey
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


class TranslateRequest(BaseModel):
    api_key: str | None = None
    provider: str | None = None
    parallel_segments: int | None = None


class TTSRequest(BaseModel):
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    voice: str | None = None
    context: str = ""
    parallel_segments: int | None = None
    merge: bool = True
    source: Literal["chapter", "translation"] = "chapter"


def create_app(
    data_dir: Path | str | None = None,
    config_path: Path | str | None = None,
    autostart_jobs: bool = True,
    use_fake_clients: bool = False,
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
    runner = JobRunner(
        repository,
        storage,
        llm_client=_FakeLLMClient() if use_fake_clients else None,
        tts_client=_FakeTTSClient() if use_fake_clients else _tts_client_from_config(loaded_config),
        audio_builder=_FakeAudioBuilder() if use_fake_clients else None,
        tts_max_chars=loaded_config.tts.max_request_chars,
    )

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

    @app.post("/api/jobs/{job_id}/pause")
    def pause_job(job_id: int) -> dict[str, Any]:
        _get_job_or_404(repository, job_id)
        repository.request_pause(job_id)
        return _job_dict(repository.get_job(job_id))

    @app.post("/api/jobs/{job_id}/resume")
    def resume_job(job_id: int) -> dict[str, Any]:
        _get_job_or_404(repository, job_id)
        repository.resume_job(job_id)
        return _job_dict(repository.get_job(job_id))

    @app.post("/api/jobs/{job_id}/stop")
    def stop_job(job_id: int) -> dict[str, Any]:
        _get_job_or_404(repository, job_id)
        repository.request_stop(job_id)
        return _job_dict(repository.get_job(job_id))

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

    @app.post("/api/chapters/{chapter_id}/translate")
    async def translate_chapter(
        chapter_id: int,
        request: TranslateRequest,
        background_tasks: BackgroundTasks,
    ) -> dict[str, Any]:
        _get_chapter_or_404(repository, chapter_id)
        translation_config = _translation_config_for_request(loaded_config.translation, request)
        parallel_segments = _bounded_parallel(
            request.parallel_segments,
            loaded_config.limits.max_parallel_translation_segments,
        )
        if autostart_jobs:
            job = _create_translation_job(
                repository,
                storage,
                chapter_id,
                translation_config,
                parallel_segments,
            )
            background_tasks.add_task(
                runner.run_translation_job,
                job.id,
                translation_config,
                parallel_segments,
            )
            return _job_dict(job)
        job = await runner.start_translation(
            chapter_id,
            translation_config,
            parallel_segments,
            api_key_override=request.api_key,
        )
        return _job_dict(job)

    @app.post("/api/chapters/{chapter_id}/tts")
    async def tts_chapter(
        chapter_id: int,
        request: TTSRequest,
        background_tasks: BackgroundTasks,
    ) -> dict[str, Any]:
        chapter = _get_chapter_or_404(repository, chapter_id)
        if request.source == "translation" and chapter.translation_path is None:
            raise HTTPException(status_code=404, detail="translation not found")
        tts_client = _tts_client_for_request(loaded_config, request, use_fake_clients)
        if tts_client is None:
            raise HTTPException(status_code=400, detail="TTS API key is required")
        runner.tts_client = tts_client

        voice = request.voice or loaded_config.tts.default_voice or "Cherry"
        parallel_segments = _bounded_parallel(
            request.parallel_segments or loaded_config.tts.default_parallel_segments,
            loaded_config.limits.max_parallel_tts_segments,
        )
        if autostart_jobs:
            job = _create_tts_job(
                repository,
                storage,
                chapter,
                request.source,
                runner.tts_max_chars,
                voice,
                request.context,
                parallel_segments,
                request.merge,
            )
            background_tasks.add_task(
                runner.run_tts_job,
                job.id,
                voice,
                request.context,
                parallel_segments,
                request.merge,
            )
            return _job_dict(job)
        if request.source == "translation":
            job = _create_tts_job(
                repository,
                storage,
                chapter,
                request.source,
                runner.tts_max_chars,
                voice,
                request.context,
                parallel_segments,
                request.merge,
            )
            return _job_dict(
                await runner.run_tts_job(
                    job.id,
                    voice=voice,
                    context=request.context,
                    parallel_segments=parallel_segments,
                    merge=request.merge,
                )
            )
        job = await runner.start_tts(
            chapter_id,
            voice=voice,
            context=request.context,
            parallel_segments=parallel_segments,
            merge=request.merge,
        )
        return _job_dict(job)

    @app.get("/api/chapters/{chapter_id}/audio")
    def chapter_audio_metadata(chapter_id: int) -> dict[str, Any]:
        chapter = _get_chapter_or_404(repository, chapter_id)
        audio_path = chapter.audio_path
        return {
            "chapter_id": chapter.id,
            "audio_path": audio_path,
            "download_url": f"/api/chapters/{chapter.id}/audio/download" if audio_path else None,
        }

    @app.post("/api/jobs/{job_id}/audio/merge")
    def merge_job_audio(job_id: int) -> dict[str, Any]:
        job = _get_job_or_404(repository, job_id)
        if job.kind != JobKind.TTS:
            raise HTTPException(status_code=400, detail="job is not a TTS job")
        merged = runner.merge_chapter_audio(job_id)
        if merged is None:
            return {"merged": False, "job": _job_dict(repository.get_job(job_id))}
        refreshed_job = repository.get_job(job_id)
        audio_path = repository.get_chapter(refreshed_job.chapter_id).audio_path if refreshed_job.chapter_id else None
        return {"merged": True, "audio_path": audio_path, "job": _job_dict(refreshed_job)}

    @app.get("/api/chapters/{chapter_id}/translation/download.txt", response_class=PlainTextResponse)
    def download_translation_text(chapter_id: int) -> PlainTextResponse:
        chapter = _get_chapter_or_404(repository, chapter_id)
        if chapter.translation_path is None:
            raise HTTPException(status_code=404, detail="translation not found")
        return PlainTextResponse(
            _read_artifact_text(storage, chapter.translation_path),
            media_type="text/plain; charset=utf-8",
        )

    @app.get("/api/chapters/{chapter_id}/audio/download")
    def download_audio(chapter_id: int) -> FileResponse:
        chapter = _get_chapter_or_404(repository, chapter_id)
        if chapter.audio_path is None:
            raise HTTPException(status_code=404, detail="audio not found")
        return _file_response(
            storage,
            chapter.audio_path,
            filename=f"chapter-{chapter.chapter_index + 1}.wav",
            media_type="audio/wav",
        )

    @app.get("/api/chapters/{chapter_id}/translation/download.zip")
    def download_translation_zip(chapter_id: int) -> FileResponse:
        chapter = _get_chapter_or_404(repository, chapter_id)
        if chapter.translation_path is None:
            raise HTTPException(status_code=404, detail="translation not found")
        return _zip_response(
            storage,
            f"books/{chapter.book_id}/downloads/translation-{chapter.chapter_index:04d}.zip",
            [chapter.translation_path],
            f"translation-{chapter.chapter_index + 1}.zip",
        )

    @app.get("/api/chapters/{chapter_id}/audio/download.zip")
    def download_audio_zip(chapter_id: int) -> FileResponse:
        chapter = _get_chapter_or_404(repository, chapter_id)
        if chapter.audio_path is None:
            raise HTTPException(status_code=404, detail="audio not found")
        return _zip_response(
            storage,
            f"books/{chapter.book_id}/downloads/audio-{chapter.chapter_index:04d}.zip",
            [chapter.audio_path],
            f"audio-{chapter.chapter_index + 1}.zip",
        )

    return app


def _translation_config_for_request(config: TranslationConfig, request: TranslateRequest) -> TranslationConfig:
    provider_name = request.provider or config.active_provider_name
    if provider_name not in config.providers:
        raise HTTPException(status_code=400, detail="translation provider not found")
    if not request.api_key:
        return replace(config, active_provider_name=provider_name)
    providers = dict(config.providers)
    providers[provider_name] = replace(providers[provider_name], api_key=request.api_key)
    return replace(config, providers=providers, active_provider_name=provider_name)


def _create_translation_job(
    repository: Repository,
    storage: LocalStorage,
    chapter_id: int,
    config: TranslationConfig,
    parallel_segments: int,
) -> Job:
    chapter = repository.get_chapter(chapter_id)
    source_segments = _chapter_segments(storage.read_text(chapter.text_path), config.segment_limit)
    job = repository.create_job(
        chapter.book_id,
        chapter.id,
        JobKind.TRANSLATE,
        len(source_segments),
        {"parallel_segments": parallel_segments},
    )
    if not source_segments:
        repository.fail_job(job.id, "chapter has no translatable text")
        return repository.get_job(job.id)
    repository.create_segments(job.id, chapter.id, source_segments)
    return job


def _create_tts_job(
    repository: Repository,
    storage: LocalStorage,
    chapter: Chapter,
    source: Literal["chapter", "translation"],
    segment_limit: int,
    voice: str,
    context: str,
    parallel_segments: int,
    merge: bool,
) -> Job:
    source_path = chapter.translation_path if source == "translation" else chapter.text_path
    if source_path is None:
        raise HTTPException(status_code=404, detail="translation not found")
    source_segments = _chapter_segments(storage.read_text(source_path), segment_limit)
    job = repository.create_job(
        chapter.book_id,
        chapter.id,
        JobKind.TTS,
        len(source_segments),
        {
            "voice": voice,
            "context": context,
            "parallel_segments": parallel_segments,
            "merge": merge,
            "source": source,
        },
    )
    if not source_segments:
        repository.fail_job(job.id, "chapter has no text to synthesize")
        return repository.get_job(job.id)
    repository.create_segments(job.id, chapter.id, source_segments)
    return job


def _bounded_parallel(value: int | None, maximum: int) -> int:
    if value is None:
        return max(1, maximum)
    if value < 1:
        raise HTTPException(status_code=400, detail="parallel_segments must be positive")
    return min(value, max(1, maximum))


def _tts_client_from_config(config: AppConfig) -> MimoTTSClient | None:
    try:
        return MimoTTSClient(
            api_key=config.tts.api_key,
            base_url=config.tts.base_url,
            model=config.tts.model,
        )
    except MissingMimoApiKey:
        return None


def _tts_client_for_request(
    config: AppConfig,
    request: TTSRequest,
    use_fake_clients: bool,
) -> Any | None:
    if use_fake_clients:
        return _FakeTTSClient()
    if not any([request.api_key, request.base_url, request.model]):
        return _tts_client_from_config(config)
    try:
        return MimoTTSClient(
            api_key=request.api_key or config.tts.api_key,
            base_url=request.base_url or config.tts.base_url,
            model=request.model or config.tts.model,
        )
    except MissingMimoApiKey:
        return None


def _file_response(
    storage: LocalStorage,
    relative_path: str,
    filename: str,
    media_type: str,
) -> FileResponse:
    try:
        path = storage.resolve_artifact(relative_path)
        if not path.is_file():
            raise FileNotFoundError(relative_path)
    except (OSError, PathSafetyError) as exc:
        raise HTTPException(status_code=404, detail="artifact not found") from exc
    return FileResponse(path, filename=filename, media_type=media_type)


def _zip_response(
    storage: LocalStorage,
    output_relative_path: str,
    artifact_paths: list[str],
    filename: str,
) -> FileResponse:
    try:
        zip_path = storage.create_zip(output_relative_path, artifact_paths)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="artifact not found") from exc
    except (OSError, PathSafetyError) as exc:
        raise HTTPException(status_code=500, detail="could not create archive") from exc
    return FileResponse(zip_path, filename=filename, media_type="application/zip")


class _FakeLLMClient:
    async def translate(
        self,
        provider: ProviderConfig,
        system_prompt: str,
        user_prompt: str,
        timeout_seconds: int,
        max_retries: int,
    ) -> str:
        return f"译文：{user_prompt}"


class _FakeTTSClient:
    def synthesize(self, text: str, voice: str, context: str, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(_minimal_wav_bytes())
        return output_path


class _FakeAudioBuilder(AudioBuilder):
    def __init__(self) -> None:
        super().__init__(ffmpeg_path=None)

    def merge_audio(self, input_paths: list[Path], output_path: Path) -> Path | None:
        if not input_paths:
            return None
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(input_paths[0].read_bytes())
        return output_path


def _minimal_wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 8)
    return buffer.getvalue()


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
