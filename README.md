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

## Ximalaya Publishing

If the Ximalaya login page crashes in Playwright-managed Chrome, start a regular
Chrome instance with a debugging port first:

```bash
scripts/start-ximalaya-chrome.sh
```

Then set `publishing.ximalaya.browser_cdp_url` in `config.yaml` to
`http://127.0.0.1:9222`, log in to Ximalaya in that Chrome window, and publish
the draft again from the app.

## Test

```bash
pytest
```

`config.yaml` and `data/` are local runtime files and should be ignored by git.
