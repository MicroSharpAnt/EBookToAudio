from pathlib import Path

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
