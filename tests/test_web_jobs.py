from pathlib import Path
from urllib.parse import unquote
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


def test_translate_endpoint_updates_chapter_title_and_summary(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml", autostart_jobs=False, use_fake_clients=True)
    client = TestClient(app)
    book_id, chapter_id = _book_and_chapter_id(client)
    original_title = client.get(f"/api/books/{book_id}/chapters").json()[0]["title"]

    response = client.post(f"/api/chapters/{chapter_id}/translate", json={"parallel_segments": 1})

    assert response.status_code == 200
    assert response.json()["status"] == JobStatus.COMPLETED
    chapters = client.get(f"/api/books/{book_id}/chapters").json()
    translated = next(chapter for chapter in chapters if chapter["id"] == chapter_id)
    assert translated["translated_title"] == f"{original_title}（中文）"
    assert "更完整的篇幅" in translated["summary"]
    assert "进入正文或译文细读" in translated["summary"]


def test_chapter_tags_endpoint_generates_and_persists_tags(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml", autostart_jobs=False, use_fake_clients=True)
    client = TestClient(app)
    book_id, chapter_id = _book_and_chapter_id(client)

    response = client.post(
        f"/api/chapters/{chapter_id}/tags",
        json={"provider": "default", "api_key": "sk-tags", "context": "发布到有声书平台"},
    )

    assert response.status_code == 200
    assert response.json()["tags"] == ["鲁迅", "童年回忆", "散文", "有声书", "中文文学"]
    chapters = client.get(f"/api/books/{book_id}/chapters").json()
    tagged = next(chapter for chapter in chapters if chapter["id"] == chapter_id)
    assert tagged["tags"] == ["鲁迅", "童年回忆", "散文", "有声书", "中文文学"]


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
        json={"voice": "茉莉", "context": "温柔旁白", "parallel_segments": 1, "merge": True},
    )

    assert response.status_code == 200
    job_id = response.json()["id"]
    assert client.post(f"/api/jobs/{job_id}/pause").status_code == 200
    assert client.post(f"/api/jobs/{job_id}/resume").status_code == 200
    assert client.post(f"/api/jobs/{job_id}/stop").status_code == 200
    audio = client.get(f"/api/chapters/{chapter_id}/audio/download")
    assert audio.status_code == 200


def test_merged_audio_download_uses_book_and_chapter_filename(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml", autostart_jobs=False, use_fake_clients=True)
    client = TestClient(app)
    upload = client.post(
        "/api/books",
        files={
            "file": (
                "My Book.txt",
                "Chapter 1 Start\nhello\nChapter 2 End\nbye".encode("utf-8"),
                "text/plain",
            )
        },
    )
    book_id = upload.json()["id"]
    client.post(f"/api/books/{book_id}/split")
    chapter = client.get(f"/api/books/{book_id}/chapters").json()[0]

    response = client.post(
        f"/api/chapters/{chapter['id']}/tts",
        json={"provider": "mimo", "voice": "茉莉", "parallel_segments": 1, "merge": True},
    )
    assert response.status_code == 200

    audio = client.get(f"/api/chapters/{chapter['id']}/audio/download")

    assert audio.status_code == 200
    assert "My Book - Chapter 1 Start.wav" in unquote(audio.headers["content-disposition"])


def test_tts_endpoint_defaults_to_translation_when_available(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml", autostart_jobs=False, use_fake_clients=True)
    client = TestClient(app)
    chapter_id = _chapter_id(client)
    chapter = app.state.repository.get_chapter(chapter_id)
    translation_path = f"books/{chapter.book_id}/translations/manual.txt"
    app.state.storage.write_text(translation_path, "译文内容")
    app.state.repository.update_chapter_translation_path(chapter_id, translation_path)

    response = client.post(
        f"/api/chapters/{chapter_id}/tts",
        json={"provider": "mimo", "voice": "茉莉", "parallel_segments": 1, "merge": False},
    )

    assert response.status_code == 200
    assert response.json()["options"]["source"] == "translation"
    segments = app.state.repository.list_segments(response.json()["id"])
    assert [segment.source_text for segment in segments] == ["译文内容"]


def test_tts_endpoint_falls_back_to_chapter_when_translation_is_missing(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml", autostart_jobs=False, use_fake_clients=True)
    client = TestClient(app)
    chapter_id = _chapter_id(client)

    response = client.post(
        f"/api/chapters/{chapter_id}/tts",
        json={"provider": "mimo", "voice": "茉莉", "source": "translation", "parallel_segments": 1, "merge": False},
    )

    assert response.status_code == 200
    assert response.json()["options"]["source"] == "chapter"
    segments = app.state.repository.list_segments(response.json()["id"])
    assert [segment.source_text for segment in segments] == ["第一章\n一二三四五六"]


def test_tts_endpoint_uses_request_bound_client_without_mutating_runner(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml", autostart_jobs=False, use_fake_clients=True)
    client = TestClient(app)
    chapter_id = _chapter_id(client)
    sentinel_client = object()
    app.state.runner.tts_client = sentinel_client

    response = client.post(
        f"/api/chapters/{chapter_id}/tts",
        json={"provider": "mimo", "voice": "茉莉", "parallel_segments": 1, "merge": True},
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
            "voice": "茉莉",
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
        json={"provider": "unknown", "voice": "茉莉", "parallel_segments": 1},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "unsupported TTS provider: unknown"
    assert client.get(f"/api/chapters/{chapter_id}/audio").json()["segments"] == []


def test_tts_endpoint_rejects_unsupported_mimo_voice_before_creating_job(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml", autostart_jobs=False, use_fake_clients=True)
    client = TestClient(app)
    chapter_id = _chapter_id(client)

    response = client.post(
        f"/api/chapters/{chapter_id}/tts",
        json={"provider": "mimo", "voice": "Cherry", "parallel_segments": 1},
    )

    assert response.status_code == 400
    assert response.json()["detail"].startswith("unsupported TTS voice: Cherry")
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
            "voice": "茉莉",
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
        json={"provider": "mimo", "voice": "茉莉", "parallel_segments": 1, "merge": False},
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
        json={"provider": "mimo", "voice": "茉莉", "parallel_segments": 1, "merge": True},
    )
    second = client.post(
        f"/api/chapters/{chapter_id}/tts",
        json={"provider": "mimo", "voice": "茉莉", "parallel_segments": 1, "merge": False},
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
        json={"provider": "mimo", "voice": "茉莉", "parallel_segments": 1, "merge": True},
    )
    second = client.post(
        f"/api/chapters/{chapter_id}/tts",
        json={"provider": "mimo", "voice": "茉莉", "parallel_segments": 1, "merge": True},
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
        json={"provider": "mimo", "voice": "茉莉", "parallel_segments": 1, "merge": False},
    )

    assert response.status_code == 200
    assert app.state.repository.get_chapter(chapter_id).audio_path is None


def test_legacy_revision_zero_tts_segments_remain_visible(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml", autostart_jobs=False, use_fake_clients=True)
    client = TestClient(app)
    chapter_id = _chapter_id(client)
    chapter = app.state.repository.get_chapter(chapter_id)
    job = app.state.repository.create_job(chapter.book_id, chapter.id, JobKind.TTS, total_units=1, options={})
    segment = app.state.repository.create_segments(job.id, chapter.id, ["一二三四"])[0]
    app.state.repository.complete_segment(
        segment.id,
        output_path="books/1/audio/0000/jobs/1/0000.wav",
    )

    metadata = client.get(f"/api/chapters/{chapter_id}/audio")

    assert metadata.status_code == 200
    assert metadata.json()["segments"][0]["job_id"] == job.id


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


class FakeXimalayaPublisher:
    def __init__(self):
        self.drafts = []

    def fill_draft(self, draft):
        self.drafts.append(draft)
        return {
            "status": "ready_for_review",
            "message": "喜马拉雅草稿已填写，请在浏览器中确认后手动发布。",
            "album_id": draft.album_id,
            "title": draft.title,
            "description": draft.description,
            "tags": list(draft.tags),
            "upload_url": draft.upload_url,
        }


def test_ximalaya_publish_endpoint_fills_draft_with_fake_publisher(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
active_translation_provider: deepseek
translation:
  providers:
    deepseek:
      base_url: "https://api.deepseek.com"
      api_key: "sk-test"
      model: "deepseek-chat"
tts:
  base_url: "https://token-plan-cn.xiaomimimo.com/v1"
  api_key: "mimo-key"
  model: "mimo-audio"
  default_voice: "茉莉"
publishing:
  ximalaya:
    album_id: "122326236"
    default_tags:
      - 有声书
    description_footer: "页脚"
""",
        encoding="utf-8",
    )
    app = create_app(data_dir=tmp_path, config_path=config_path, autostart_jobs=False, use_fake_clients=True)
    app.state.ximalaya_publisher = FakeXimalayaPublisher()
    client = TestClient(app)
    _, chapter_id = _book_and_chapter_id(client)
    client.post(f"/api/chapters/{chapter_id}/translate", json={"parallel_segments": 1})
    client.post(f"/api/chapters/{chapter_id}/tags", json={"api_key": "sk-tags"})
    client.post(f"/api/chapters/{chapter_id}/tts", json={"voice": "茉莉", "parallel_segments": 1, "merge": True})

    response = client.post(f"/api/chapters/{chapter_id}/publish/ximalaya/draft")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready_for_review"
    assert payload["album_id"] == "122326236"
    assert payload["title"].endswith("（中文）")
    assert "页脚" in payload["description"]
    assert "有声书" in payload["tags"]
    assert app.state.ximalaya_publisher.drafts[0].audio_path.is_file()


def test_ximalaya_publish_endpoint_requires_merged_audio(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml", autostart_jobs=False, use_fake_clients=True)
    app.state.ximalaya_publisher = FakeXimalayaPublisher()
    client = TestClient(app)
    chapter_id = _chapter_id(client)

    response = client.post(f"/api/chapters/{chapter_id}/publish/ximalaya/draft")

    assert response.status_code == 400
    assert response.json()["detail"] == "请先生成并合并章节音频。"
