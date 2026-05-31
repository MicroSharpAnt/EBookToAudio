from pathlib import Path
from zipfile import ZipFile

from fastapi.testclient import TestClient

from ebook_to_audio.web import create_app


def test_upload_clean_split_edit_and_download_chapter(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "missing.yaml", autostart_jobs=False)
    client = TestClient(app)

    upload = client.post("/api/books", files={"file": ("book.txt", "第一章\n正文  内容".encode("utf-8"), "text/plain")})
    assert upload.status_code == 200
    book_id = upload.json()["id"]

    clean = client.post(f"/api/books/{book_id}/clean", json={"operations": ["normalize_spacing"]})
    assert clean.status_code == 200
    assert clean.json()["results"][0]["operation"] == "normalize_spacing"

    split = client.post(f"/api/books/{book_id}/split")
    assert split.status_code == 200
    job_id = split.json()["job"]["id"]
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "completed"

    chapters = client.get(f"/api/books/{book_id}/chapters").json()
    chapter_id = chapters[0]["id"]
    update = client.put(f"/api/chapters/{chapter_id}", json={"title": "第一章 修改", "text": "新正文"})
    assert update.status_code == 200
    assert update.json()["char_count"] == 3

    download = client.get(f"/api/chapters/{chapter_id}/download.txt")
    assert download.status_code == 200
    assert "新正文" in download.text


def test_failed_upload_does_not_leave_visible_book(tmp_path: Path, monkeypatch):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "missing.yaml", autostart_jobs=False)
    client = TestClient(app, raise_server_exceptions=False)

    def fail_write_text(relative_path: str, text: str):
        raise OSError(f"cannot write {tmp_path / relative_path}")

    monkeypatch.setattr(app.state.storage, "write_text", fail_write_text)

    upload = client.post("/api/books", files={"file": ("book.txt", b"hello", "text/plain")})

    assert upload.status_code == 500
    assert upload.json()["detail"] == "could not upload book"
    assert client.get("/api/books").json() == []


def test_upload_windows_path_filename_is_sanitized_before_book_create(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "missing.yaml", autostart_jobs=False)
    client = TestClient(app, raise_server_exceptions=False)

    upload = client.post("/api/books", files={"file": ("bad\\name.txt", b"hello", "text/plain")})

    assert upload.status_code == 200
    body = upload.json()
    assert body["original_filename"] == "name.txt"
    assert body["title"] == "name"
    assert body["source_format"] == "txt"
    assert body["source_path"].endswith("/name.txt")
    assert "\\" not in body["source_path"]
    assert [book["original_filename"] for book in client.get("/api/books").json()] == ["name.txt"]


def test_upload_posix_path_filename_is_sanitized_in_metadata(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "missing.yaml", autostart_jobs=False)
    client = TestClient(app)

    upload = client.post("/api/books", files={"file": ("../book.txt", b"hello", "text/plain")})

    assert upload.status_code == 200
    body = upload.json()
    assert body["original_filename"] == "book.txt"
    assert body["title"] == "book"
    assert body["source_path"].endswith("/book.txt")


def test_repeated_split_failure_preserves_existing_chapters(tmp_path: Path, monkeypatch):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "missing.yaml", autostart_jobs=False)
    client = TestClient(app, raise_server_exceptions=False)

    upload = client.post(
        "/api/books",
        files={"file": ("book.txt", "第一章\n旧正文".encode("utf-8"), "text/plain")},
    )
    book_id = upload.json()["id"]
    assert client.post(f"/api/books/{book_id}/split").status_code == 200
    original_chapters = client.get(f"/api/books/{book_id}/chapters").json()
    assert [chapter["title"] for chapter in original_chapters] == ["第 1 段"]

    original_write_text = app.state.storage.write_text

    def fail_chapter_write(relative_path: str, text: str):
        if "/split-" in relative_path:
            raise OSError(f"cannot write {tmp_path / relative_path}")
        return original_write_text(relative_path, text)

    monkeypatch.setattr(app.state.storage, "write_text", fail_chapter_write)
    failed = client.post(f"/api/books/{book_id}/split")

    assert failed.status_code == 500
    assert failed.json()["detail"] == "could not split book"
    chapters_after_failure = client.get(f"/api/books/{book_id}/chapters").json()
    assert chapters_after_failure == original_chapters


def test_failed_split_job_error_is_api_safe(tmp_path: Path, monkeypatch):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "missing.yaml", autostart_jobs=False)
    client = TestClient(app, raise_server_exceptions=False)

    upload = client.post("/api/books", files={"file": ("book.txt", b"hello", "text/plain")})
    book_id = upload.json()["id"]

    def fail_write_text(relative_path: str, text: str):
        raise OSError(f"cannot write {tmp_path / relative_path}")

    monkeypatch.setattr(app.state.storage, "write_text", fail_write_text)
    split = client.post(f"/api/books/{book_id}/split")
    job_id = split.json()["job"]["id"]

    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "failed"
    assert job["error_message"] == "could not split book"
    assert str(tmp_path) not in str(job)


def test_oversize_upload_returns_413(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "missing.yaml", autostart_jobs=False)
    client = TestClient(app)

    upload = client.post(
        "/api/books",
        files={"file": ("book.txt", b"x" * 1_000_001, "text/plain")},
    )

    assert upload.status_code == 413


def test_missing_chapter_file_zip_download_returns_safe_error(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "missing.yaml", autostart_jobs=False)
    client = TestClient(app, raise_server_exceptions=False)

    upload = client.post("/api/books", files={"file": ("book.txt", b"hello", "text/plain")})
    book_id = upload.json()["id"]
    client.post(f"/api/books/{book_id}/split")
    chapter = client.get(f"/api/books/{book_id}/chapters").json()[0]

    app.state.storage.resolve_artifact(chapter["text_path"]).unlink()
    download = client.get(f"/api/chapters/{chapter['id']}/download.zip")

    assert download.status_code == 404
    assert download.json()["detail"] == "chapter artifact not found"
    assert str(tmp_path) not in download.text


def test_invalid_file_type_bad_clean_and_missing_resources_return_api_errors(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "missing.yaml", autostart_jobs=False)
    client = TestClient(app)

    config = client.get("/api/config")
    assert config.json()["error"] == "configuration invalid"
    assert str(tmp_path) not in str(config.json())

    invalid = client.post("/api/books", files={"file": ("book.pdf", b"%PDF", "application/pdf")})
    assert invalid.status_code == 400

    upload = client.post("/api/books", files={"file": ("book.txt", b"hello", "text/plain")})
    book_id = upload.json()["id"]
    bad_clean = client.post(f"/api/books/{book_id}/clean", json={"operations": ["missing"]})
    assert bad_clean.status_code == 400

    assert client.get("/api/books/999").status_code == 404
    assert client.get("/api/jobs/999").status_code == 404
    assert client.get("/api/chapters/999").status_code == 404


def test_chapter_zip_download_contains_chapter_text(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "missing.yaml", autostart_jobs=False)
    client = TestClient(app)

    upload = client.post("/api/books", files={"file": ("book.txt", "hello".encode("utf-8"), "text/plain")})
    book_id = upload.json()["id"]
    client.post(f"/api/books/{book_id}/split")
    chapter = client.get(f"/api/books/{book_id}/chapters").json()[0]

    download = client.get(f"/api/chapters/{chapter['id']}/download.zip")
    zip_path = tmp_path / "chapter.zip"
    zip_path.write_bytes(download.content)

    with ZipFile(zip_path) as archive:
        names = archive.namelist()
        assert any(name.endswith(".txt") for name in names)
        assert "hello" in archive.read(names[0]).decode("utf-8")
