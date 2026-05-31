from pathlib import Path

from fastapi.testclient import TestClient

from ebook_to_audio.models import JobStatus
from ebook_to_audio.web import create_app


def _chapter_id(client: TestClient) -> int:
    upload = client.post("/api/books", files={"file": ("book.txt", "第一章\n一二三四五六".encode("utf-8"), "text/plain")})
    book_id = upload.json()["id"]
    client.post(f"/api/books/{book_id}/split")
    return client.get(f"/api/books/{book_id}/chapters").json()[0]["id"]


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
