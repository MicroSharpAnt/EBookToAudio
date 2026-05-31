from pathlib import Path

from fastapi.testclient import TestClient

from ebook_to_audio.web import create_app


def test_static_ui_served_with_required_labels(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "missing.yaml", autostart_jobs=False)
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    for label in [
        "去除文章水印",
        "去除文章多余空格等字符",
        "按章节分成多个txt",
        "将文章翻译为中文",
        "翻译提示词",
    ]:
        assert label in response.text


def test_static_assets_are_served(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "missing.yaml", autostart_jobs=False)
    client = TestClient(app)

    for path in ["/static/styles.css", "/static/app.js"]:
        response = client.get(path)
        assert response.status_code == 200


def test_readme_uses_fastapi_factory_start_command():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert 'uvicorn "ebook_to_audio.web:create_app" --factory --reload' in readme
    assert "ebook_to_audio.app:app" not in readme


def test_static_ui_exposes_word_count_label(tmp_path: Path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "missing.yaml", autostart_jobs=False)
    client = TestClient(app)

    response = client.get("/static/app.js")

    assert response.status_code == 200
    assert "约" in response.text
    assert "词" in response.text
