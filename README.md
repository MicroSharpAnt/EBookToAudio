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

If the Ximalaya login page crashes in Playwright-managed Chrome, log in with a
regular Chrome window first:

```bash
scripts/login-ximalaya-chrome.sh
```

After logging in, quit that Chrome window. Then start the Chrome instance used
by the publisher:

```bash
scripts/start-ximalaya-chrome.sh
```

Then set `publishing.ximalaya.browser_cdp_url` in `config.yaml` to
`http://127.0.0.1:9222` and publish the draft again from the app.

## Test

```bash
pytest
```

`config.yaml` and `data/` are local runtime files and should be ignored by git.
