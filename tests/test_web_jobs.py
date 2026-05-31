from pathlib import Path
from zipfile import ZipFile

from fastapi.testclient import TestClient

from ebook_to_audio.models import JobKind, JobStatus
from ebook_to_audio.web import create_app


def _book_and_chapter_id(client: TestClient) -> tuple[int, int]:
    upload = client.post("/api/books", files={"file": ("book.txt", "第一章\n一二三四五六".encode("utf-8"), "text/plain")})
    book_id = upload.json()["id"]
    client.post(f"/api/books/{book_id}/split")
    chapter_id = client.get(f"/api/books/{book_id}/chapters").json()[0]["id"]
    return book_id, chapter_id


def _chapter_id(client: TestClient) -> int:
    return _book_and_chapter_id(client)[1]


def test_translate_endpoint_creates_translation_and_download(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml", autostart_jobs=False, use_fake_clients=True)
    client = TestClient(app)
    chapter_id = _chapter_id(client)

    response = client.post(f"/api/chapters/{chapter_id}/translate", json={"parallel_segments": 1})

    assert response.status_code == 200
    assert response.json()["status"] == JobStatus.COMPLETED
    download = client.get(f"/api/chapters/{chapter_id}/translation/download.txt")
    assert download.status_code == 200
    assert "译文" in download.text


def test_book_jobs_endpoint_lists_existing_jobs(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml", autostart_jobs=False, use_fake_clients=True)
    client = TestClient(app)
    book_id, chapter_id = _book_and_chapter_id(client)

    response = client.post(f"/api/chapters/{chapter_id}/translate", json={"parallel_segments": 1})
    job_id = response.json()["id"]

    jobs = client.get(f"/api/books/{book_id}/jobs")

    assert jobs.status_code == 200
    assert [job["id"] for job in jobs.json()] == [job_id, 1]
    assert jobs.json()[0]["kind"] == JobKind.TRANSLATE


def test_translate_endpoint_accepts_prompt_context_and_book_zip(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml", autostart_jobs=False, use_fake_clients=True)
    client = TestClient(app)
    book_id, chapter_id = _book_and_chapter_id(client)

    response = client.post(
        f"/api/chapters/{chapter_id}/translate",
        json={"parallel_segments": 1, "provider": "default", "prompt": "翻译成英文", "context": "保留专有名词"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == JobStatus.COMPLETED
    download = client.get(f"/api/books/{book_id}/translations/download.zip")
    assert download.status_code == 200
    zip_path = tmp_path / "translations.zip"
    zip_path.write_bytes(download.content)
    with ZipFile(zip_path) as archive:
        names = archive.namelist()
        assert any(name.endswith(".txt") for name in names)
        text = archive.read(names[0]).decode("utf-8")
    assert "翻译成英文" in text
    assert "保留专有名词" in text


def test_tts_endpoint_creates_audio_and_controls_are_idempotent(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml", autostart_jobs=False, use_fake_clients=True)
    client = TestClient(app)
    chapter_id = _chapter_id(client)

    response = client.post(
        f"/api/chapters/{chapter_id}/tts",
        json={"voice": "Cherry", "context": "温柔旁白", "parallel_segments": 1, "merge": True},
    )

    assert response.status_code == 200
    job_id = response.json()["id"]
    assert client.post(f"/api/jobs/{job_id}/pause").status_code == 200
    assert client.post(f"/api/jobs/{job_id}/resume").status_code == 200
    assert client.post(f"/api/jobs/{job_id}/stop").status_code == 200
    audio = client.get(f"/api/chapters/{chapter_id}/audio/download")
    assert audio.status_code == 200


def test_tts_endpoint_uses_request_bound_client_without_mutating_runner(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml", autostart_jobs=False, use_fake_clients=True)
    client = TestClient(app)
    chapter_id = _chapter_id(client)
    sentinel_client = object()
    app.state.runner.tts_client = sentinel_client

    response = client.post(
        f"/api/chapters/{chapter_id}/tts",
        json={"provider": "mimo", "voice": "Cherry", "parallel_segments": 1, "merge": True},
    )

    assert response.status_code == 200
    assert app.state.runner.tts_client is sentinel_client
    assert client.get(f"/api/chapters/{chapter_id}/audio/download").status_code == 200


def test_tts_endpoint_exposes_segment_audio_and_book_zip(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml", autostart_jobs=False, use_fake_clients=True)
    client = TestClient(app)
    book_id, chapter_id = _book_and_chapter_id(client)

    response = client.post(
        f"/api/chapters/{chapter_id}/tts",
        json={
            "provider": "mimo",
            "voice": "Cherry",
            "context": "温柔旁白",
            "narration_style": "舒缓",
            "character_tone": "坚定",
            "work_background": "古风修仙",
            "parallel_segments": 1,
            "merge": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["options"]["provider"] == "mimo"
    assert "舒缓" in response.json()["options"]["context"]
    metadata = client.get(f"/api/chapters/{chapter_id}/audio")
    assert metadata.status_code == 200
    segments = metadata.json()["segments"]
    assert len(segments) == 1
    listing = client.get(f"/api/chapters/{chapter_id}/audio/segments")
    assert listing.status_code == 200
    assert listing.json() == segments
    segment_download = client.get(segments[0]["download_url"])
    assert segment_download.status_code == 200
    assert segment_download.content.startswith(b"RIFF")

    book_audio = client.get(f"/api/books/{book_id}/audio/download.zip")
    assert book_audio.status_code == 200
    zip_path = tmp_path / "audio.zip"
    zip_path.write_bytes(book_audio.content)
    with ZipFile(zip_path) as archive:
        names = archive.namelist()
        assert any(name.endswith(".wav") for name in names)


def test_tts_endpoint_rejects_unsupported_provider_before_creating_job(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml", autostart_jobs=False, use_fake_clients=True)
    client = TestClient(app)
    chapter_id = _chapter_id(client)

    response = client.post(
        f"/api/chapters/{chapter_id}/tts",
        json={"provider": "unknown", "voice": "Cherry", "parallel_segments": 1},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "unsupported TTS provider: unknown"
    assert client.get(f"/api/chapters/{chapter_id}/audio").json()["segments"] == []


def test_resume_endpoint_runs_pending_translation_job(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml", autostart_jobs=False, use_fake_clients=True)
    client = TestClient(app)
    chapter_id = _chapter_id(client)
    chapter = app.state.repository.get_chapter(chapter_id)
    job = app.state.repository.create_job(
        chapter.book_id,
        chapter.id,
        JobKind.TRANSLATE,
        1,
        {
            "provider": "default",
            "parallel_segments": 1,
            "prompt": "翻译",
            "context": "简洁",
            "chapter_revision": chapter.content_revision,
        },
    )
    app.state.repository.create_segments(job.id, chapter.id, ["一二三四五六"])
    app.state.repository.request_pause(job.id)

    response = client.post(f"/api/jobs/{job.id}/resume")

    assert response.status_code == 200
    assert response.json()["status"] == JobStatus.COMPLETED
    translation = client.get(f"/api/chapters/{chapter_id}/translation/download.txt")
    assert translation.status_code == 200
    assert "译文" in translation.text


def test_resume_tts_without_api_key_keeps_job_paused(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml", autostart_jobs=False)
    client = TestClient(app)
    chapter_id = _chapter_id(client)
    chapter = app.state.repository.get_chapter(chapter_id)
    job = app.state.repository.create_job(
        chapter.book_id,
        chapter.id,
        JobKind.TTS,
        1,
        {
            "provider": "mimo",
            "voice": "Cherry",
            "context": "",
            "parallel_segments": 1,
            "merge": False,
            "source": "chapter",
            "chapter_revision": chapter.content_revision,
        },
    )
    app.state.repository.create_segments(job.id, chapter.id, ["一二三四五六"])
    app.state.repository.request_pause(job.id)

    response = client.post(f"/api/jobs/{job.id}/resume")

    assert response.status_code == 400
    assert response.json()["detail"] == "TTS API key is required to resume job"
    unchanged = app.state.repository.get_job(job.id)
    assert unchanged.status == JobStatus.PAUSED
    assert unchanged.pause_requested is True


def test_resume_non_paused_translation_job_does_not_run(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml", autostart_jobs=False, use_fake_clients=True)
    client = TestClient(app)
    chapter_id = _chapter_id(client)
    chapter = app.state.repository.get_chapter(chapter_id)
    job = app.state.repository.create_job(
        chapter.book_id,
        chapter.id,
        JobKind.TRANSLATE,
        1,
        {
            "provider": "default",
            "parallel_segments": 1,
            "chapter_revision": chapter.content_revision,
        },
    )
    app.state.repository.create_segments(job.id, chapter.id, ["一二三四五六"])

    response = client.post(f"/api/jobs/{job.id}/resume")

    assert response.status_code == 200
    assert response.json()["status"] == JobStatus.PENDING
    assert client.get(f"/api/chapters/{chapter_id}/translation/download.txt").status_code == 404


def test_chapter_audio_zip_includes_segments_when_tts_merge_false(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml", autostart_jobs=False, use_fake_clients=True)
    client = TestClient(app)
    chapter_id = _chapter_id(client)

    response = client.post(
        f"/api/chapters/{chapter_id}/tts",
        json={"provider": "mimo", "voice": "Cherry", "parallel_segments": 1, "merge": False},
    )

    assert response.status_code == 200
    download = client.get(f"/api/chapters/{chapter_id}/audio/download.zip")
    assert download.status_code == 200
    zip_path = tmp_path / "chapter-segment-audio.zip"
    zip_path.write_bytes(download.content)
    with ZipFile(zip_path) as archive:
        names = archive.namelist()
        assert any("/jobs/" in name and name.endswith("/0000.wav") for name in names)


def test_repeated_tts_exposes_only_latest_job_audio(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml", autostart_jobs=False, use_fake_clients=True)
    client = TestClient(app)
    book_id, chapter_id = _book_and_chapter_id(client)

    first = client.post(
        f"/api/chapters/{chapter_id}/tts",
        json={"provider": "mimo", "voice": "Cherry", "parallel_segments": 1, "merge": True},
    )
    second = client.post(
        f"/api/chapters/{chapter_id}/tts",
        json={"provider": "mimo", "voice": "Cherry", "parallel_segments": 1, "merge": False},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    first_job_id = first.json()["id"]
    second_job_id = second.json()["id"]
    first_segments = app.state.repository.list_segments(first_job_id)
    second_segments = app.state.repository.list_segments(second_job_id)
    assert first_segments[0].output_path != second_segments[0].output_path
    assert f"/jobs/{first_job_id}/" in first_segments[0].output_path
    assert f"/jobs/{second_job_id}/" in second_segments[0].output_path

    metadata = client.get(f"/api/chapters/{chapter_id}/audio")
    assert metadata.status_code == 200
    assert metadata.json()["audio_path"] is None
    assert metadata.json()["download_url"] is None
    assert [segment["job_id"] for segment in metadata.json()["segments"]] == [second_job_id]
    assert client.get(f"/api/chapters/{chapter_id}/audio/download").status_code == 404

    chapter_zip = client.get(f"/api/chapters/{chapter_id}/audio/download.zip")
    assert chapter_zip.status_code == 200
    chapter_zip_path = tmp_path / "latest-chapter-audio.zip"
    chapter_zip_path.write_bytes(chapter_zip.content)
    with ZipFile(chapter_zip_path) as archive:
        names = archive.namelist()
    assert any(f"/jobs/{second_job_id}/" in name for name in names)
    assert not any(f"/jobs/{first_job_id}/" in name for name in names)

    book_zip = client.get(f"/api/books/{book_id}/audio/download.zip")
    assert book_zip.status_code == 200
    book_zip_path = tmp_path / "latest-book-audio.zip"
    book_zip_path.write_bytes(book_zip.content)
    with ZipFile(book_zip_path) as archive:
        names = archive.namelist()
    assert any(f"/jobs/{second_job_id}/" in name for name in names)
    assert not any(f"/jobs/{first_job_id}/" in name for name in names)


def test_manual_merge_of_older_tts_job_does_not_replace_current_audio(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml", autostart_jobs=False, use_fake_clients=True)
    client = TestClient(app)
    chapter_id = _chapter_id(client)

    first = client.post(
        f"/api/chapters/{chapter_id}/tts",
        json={"provider": "mimo", "voice": "Cherry", "parallel_segments": 1, "merge": True},
    )
    second = client.post(
        f"/api/chapters/{chapter_id}/tts",
        json={"provider": "mimo", "voice": "Cherry", "parallel_segments": 1, "merge": True},
    )
    first_job_id = first.json()["id"]
    second_job_id = second.json()["id"]
    first_audio_path = app.state.repository.get_chapter(chapter_id).audio_path

    manual_merge = client.post(f"/api/jobs/{first_job_id}/audio/merge")

    assert manual_merge.status_code == 200
    assert f"/jobs/{first_job_id}/chapter.wav" in manual_merge.json()["merged_audio_path"]
    assert app.state.repository.get_chapter(chapter_id).audio_path == first_audio_path
    metadata = client.get(f"/api/chapters/{chapter_id}/audio").json()
    assert f"/jobs/{second_job_id}/chapter.wav" in metadata["audio_path"]
    assert all(segment["job_id"] == second_job_id for segment in metadata["segments"])
    chapter_zip = client.get(f"/api/chapters/{chapter_id}/audio/download.zip")
    zip_path = tmp_path / "current-merged-audio.zip"
    zip_path.write_bytes(chapter_zip.content)
    with ZipFile(zip_path) as archive:
        names = archive.namelist()
    assert any(f"/jobs/{second_job_id}/chapter.wav" in name for name in names)
    assert not any(f"/jobs/{first_job_id}/chapter.wav" in name for name in names)


def test_tts_endpoint_clears_older_promotion_after_new_job_row_exists(tmp_path: Path, monkeypatch):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml", autostart_jobs=False, use_fake_clients=True)
    client = TestClient(app)
    chapter_id = _chapter_id(client)
    chapter = app.state.repository.get_chapter(chapter_id)
    older = app.state.repository.create_job(
        chapter.book_id,
        chapter.id,
        JobKind.TTS,
        total_units=1,
        options={"chapter_revision": chapter.content_revision},
    )
    original_create_job = app.state.repository.create_job

    def create_job_after_older_promotion(*args, **kwargs):
        app.state.repository.promote_chapter_audio_path_if_latest_tts_job(older.id, "old.wav")
        return original_create_job(*args, **kwargs)

    monkeypatch.setattr(app.state.repository, "create_job", create_job_after_older_promotion)

    response = client.post(
        f"/api/chapters/{chapter_id}/tts",
        json={"provider": "mimo", "voice": "Cherry", "parallel_segments": 1, "merge": False},
    )

    assert response.status_code == 200
    assert app.state.repository.get_chapter(chapter_id).audio_path is None


def test_resume_paused_unsupported_job_kind_returns_cleanly(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml", autostart_jobs=False, use_fake_clients=True)
    client = TestClient(app)
    book_id, _chapter_id_value = _book_and_chapter_id(client)
    job = app.state.repository.create_job(book_id, None, JobKind.SPLIT, 1, {})
    app.state.repository.request_pause(job.id)

    response = client.post(f"/api/jobs/{job.id}/resume")

    assert response.status_code == 200
    assert response.json()["kind"] == JobKind.SPLIT
    assert response.json()["status"] == JobStatus.RUNNING
