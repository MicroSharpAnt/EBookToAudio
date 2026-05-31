# ebook-to-audio

Local FastAPI single-page application for translating ebooks and generating audio.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Configuration

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml` with your provider API keys and model settings.
For existing installs, keep `limits.max_upload_bytes` at `20971520` or higher to allow normal EPUB/TXT imports.

## Run

```bash
uvicorn "ebook_to_audio.web:create_app" --factory --reload
```

## Test

```bash
pytest
```

`config.yaml` and `data/` are local runtime files and should be ignored by git.
