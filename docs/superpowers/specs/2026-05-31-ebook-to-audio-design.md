# EBookToAudio Design

## Goal

Build a local single-book web workstation that imports EPUB/TXT books, converts them to editable TXT, cleans watermark and anti-piracy noise, splits the book into chapter TXT files, translates chapters into Simplified Chinese with an OpenAI-compatible LLM provider, and converts chapters into audio with MiMo TTS.

## Scope

The first version supports one active book at a time in the UI. Backend records still use book, chapter, and job identifiers so a later task history view can be added without changing file layouts or API contracts.

In scope:

- Import `.epub` and `.txt` files.
- Convert the imported book to full-book UTF-8 TXT.
- Clean article watermarks, excessive whitespace, repeated ad or anti-piracy lines, and common invisible or decorative characters.
- Split the cleaned book into chapter TXT files.
- Display split progress and a chapter list after splitting completes.
- View, edit, save, and download each chapter TXT.
- Show chapter metadata: character count, paragraph count, segment count where relevant, and current task states.
- Translate the full chapter list or a single selected chapter to Simplified Chinese.
- Download translated chapter TXT files and bulk exported translation artifacts.
- Convert a single chapter to audio with MiMo TTS.
- Configure global TTS voice, TTS parallel segment count, translation parallel count, and shared context prompt.
- Use the context prompt for narration style, character tone, and work background.
- Show progress bars for splitting, translation, and TTS.
- Pause, resume, or stop individual translation and TTS jobs.
- Merge all TTS segments for a chapter when `ffmpeg` is available.
- Download audio segments, merged chapter audio, and zip bundles.
- Store default provider settings in `config.yaml`, with optional temporary API key overrides from the page.

Out of scope for the first version:

- Multi-book library management in the UI.
- User accounts, cloud storage, or remote deployment.
- Real-time interruption of an HTTP request already in flight. Stop requests prevent new segments from starting and persist the stopped state after the active request returns.
- Full audiobook merge across all chapters. The first version focuses on per-chapter merge, because that matches the chapter-level workflow.

## Recommended Approach

Create a new independent FastAPI application package named `ebook_to_audio`.

Reuse proven pieces from the reference projects:

- From `/Users/yj/Documents/mimo_tts_read`: EPUB/TXT parsing patterns, MiMo TTS client behavior, audio segment generation, progress persistence, and `ffmpeg` concat merging.
- From `/Users/yj/Documents/trans_en`: OpenAI-compatible LLM translation client, YAML provider config, SQLite repository style, translation chunking, and export helpers.

Do not splice the two applications together directly. The new project should share one data model, one storage layout, one API, and one single-page UI.

## Architecture

The application has four layers:

- `web.py`: FastAPI app, request validation, static files, upload and download endpoints.
- Domain services: parsing, text cleaning, chapter splitting, translation scheduling, TTS scheduling, and audio merging.
- `repository.py`: SQLite persistence for books, chapters, translation tasks, TTS tasks, segment progress, pause and stop flags, and artifact paths.
- `static/`: single-page HTML/CSS/JS workstation.

Runtime files live under `data/`:

```text
data/
  ebook-to-audio.db
  books/{book_id}/
    source.epub
    source.txt
    cleaned.txt
    chapters/{chapter_index:04d}.txt
    translations/{chapter_index:04d}.zh.txt
    audio/{chapter_index:04d}/segments/{segment_index:04d}.wav
    audio/{chapter_index:04d}/chapter.wav
    exports/
```

SQLite stores metadata and task progress; text and audio payloads stay as files to keep downloads simple and avoid large BLOB rows.

## Data Model

Books:

- id
- title
- source_format
- original_filename
- source_path
- full_text_path
- cleaned_text_path
- created_at
- updated_at

Chapters:

- id
- book_id
- chapter_index
- title
- text_path
- translation_path
- char_count
- paragraph_count
- created_at
- updated_at

Jobs:

- id
- book_id
- chapter_id nullable for book-wide jobs
- kind: `split`, `translate`, or `tts`
- status: `pending`, `running`, `paused`, `completed`, `completed_with_errors`, `failed`, or `stopped`
- total_units
- completed_units
- failed_units
- stop_requested
- pause_requested
- error_message
- options_json
- created_at
- updated_at

Segments:

- id
- job_id
- chapter_id
- segment_index
- source_text
- output_path nullable
- status: `pending`, `running`, `completed`, `failed`, or `stopped`
- error_message

The same generic job and segment tables support translation and TTS. Split jobs use units for chapter candidates instead of segment rows unless detailed per-chapter diagnostics are needed.

## Text Import And Conversion

TXT import reads UTF-8 with BOM handling and normalizes line endings to `\n`.

EPUB import follows spine order, skips navigation documents, extracts readable text with BeautifulSoup, and writes both a full-book TXT and initial chapter candidates when chapter boundaries are already obvious from the EPUB structure.

After upload, the UI can immediately download the converted full-book TXT.

## Cleaning Features

The text cleaner is deterministic and local. It should expose separate operations so the UI can run them independently and tests can verify each behavior:

- `remove_watermarks`: remove lines matching configurable watermark and anti-piracy patterns, including common site suffixes, scan/source markers, repeated copyright notices, and bracketed ad blocks.
- `normalize_spacing`: normalize line endings, trim trailing spaces, collapse repeated blank lines, collapse excessive spaces or tabs, normalize full-width spaces, and remove zero-width characters.
- `remove_repeated_noise_lines`: remove repeated short lines that appear above a configurable threshold and look like ads or source markers.
- `remove_decorative_characters`: remove long runs of punctuation, separators, invisible control characters, and decorative boundary lines.

The page should offer at least these buttons:

- `去除文章水印`
- `去除文章多余空格等字符`
- `删除重复广告/防盗行`
- `删除装饰符/不可见字符`

Each operation updates the cleaned text and reports before/after character and line counts.

## Chapter Splitting

The splitter should identify chapter headings with Chinese and English patterns:

- `第...章`
- `第...节`
- `第...回`
- `Chapter 1`
- `CHAPTER I`
- `卷一`
- Prologue/序/前言/楔子/尾声/后记

If headings are insufficient, the splitter falls back to size-based chunks with stable titles such as `第 1 段`.

Clicking `按章节分成多个txt` starts a split job. The frontend polls job status and displays a progress bar. When the job completes, the chapter list renders all generated chapter TXT files.

Each chapter row includes:

- title
- character count
- paragraph count
- current translation status
- current TTS status
- buttons for view/edit, download TXT, translate, download translation, TTS, merge audio, download audio, pause/resume/stop active task

The view/edit panel loads the chapter TXT, lets the user edit it, and saves the file back through the API. Saving recalculates chapter metadata.

## Translation

Translation uses OpenAI-compatible chat completions. Providers are configured in `config.yaml`, with DeepSeek and MiMo examples in `config.example.yaml`.

Global translation options:

- provider
- model from provider config
- translation parallel count
- segment character limit
- optional temporary API key override

Each chapter translation job:

1. Reads the current chapter TXT.
2. Splits it into segments that fit the configured LLM segment limit.
3. Runs up to the configured parallel segment count.
4. Writes translated segment outputs in segment order.
5. Saves the final chapter translation as `translations/{chapter}.zh.txt`.

Stop behavior:

- The pause endpoint sets `pause_requested`.
- Running requests finish normally.
- The scheduler stops submitting new segments when the pause flag is seen.
- Pending segments remain `pending`.
- The job status becomes `paused`.
- The resume endpoint clears `pause_requested`, moves the job back to `running`, and schedules remaining pending segments.
- The stop endpoint sets `stop_requested`.
- Running requests finish normally.
- The scheduler stops submitting new segments.
- Pending segments become `stopped`.
- The job status becomes `stopped` unless all segments completed first.

The UI supports translating one chapter from its row. It may also provide a book-level translate-all action that queues one chapter after another, but per-chapter translation is required.

## TTS

TTS uses the MiMo OpenAI-compatible audio chat completion behavior from the reference TTS project.

Global TTS options:

- voice
- TTS parallel segment count
- context prompt
- optional temporary MiMo API key override
- merge chapter segments after completion

The context prompt is sent before the source text and can describe narration style, character tone, and work background.

Each chapter TTS job:

1. Reads the current chapter TXT or, when the user chooses a translated source later, the translated TXT. The first version defaults to original chapter TXT.
2. Splits it into TTS-safe segments.
3. Generates `.wav` segment files with the chosen voice and context prompt.
4. Updates completed, active, failed, and total segment counts.
5. Optionally merges segment WAV files into `chapter.wav` with `ffmpeg`.

Pause, resume, and stop behavior mirrors translation. Pausing keeps pending TTS segments available for later resume; stopping marks pending TTS segments as stopped and finishes the job without submitting more work.

## Downloads

Supported downloads:

- converted full-book TXT
- cleaned full-book TXT
- chapter TXT
- translated chapter TXT
- chapter audio segment WAV
- merged chapter WAV
- zip of all chapter TXT files
- zip of all translated TXT files
- zip of one chapter's audio files

Download endpoints must validate artifact paths against the book data directory before returning files.

## API Sketch

Core endpoints:

- `GET /api/config`
- `POST /api/books`
- `GET /api/books/current`
- `GET /api/books/{book_id}`
- `GET /api/books/{book_id}/text/full`
- `GET /api/books/{book_id}/text/cleaned`
- `POST /api/books/{book_id}/clean`
- `POST /api/books/{book_id}/split`
- `GET /api/jobs/{job_id}`
- `POST /api/jobs/{job_id}/pause`
- `POST /api/jobs/{job_id}/resume`
- `POST /api/jobs/{job_id}/stop`
- `GET /api/books/{book_id}/chapters`
- `GET /api/chapters/{chapter_id}`
- `PUT /api/chapters/{chapter_id}`
- `GET /api/chapters/{chapter_id}/download.txt`
- `POST /api/chapters/{chapter_id}/translate`
- `GET /api/chapters/{chapter_id}/translation`
- `GET /api/chapters/{chapter_id}/translation/download.txt`
- `POST /api/chapters/{chapter_id}/tts`
- `GET /api/chapters/{chapter_id}/audio`
- `POST /api/chapters/{chapter_id}/audio/merge`
- `GET /api/chapters/{chapter_id}/audio/download`
- `GET /api/books/{book_id}/download/chapters.zip`
- `GET /api/books/{book_id}/download/translations.zip`

The exact implementation may combine some download endpoints if the route stays clear and tested.

## Frontend

Use one static `index.html`, `styles.css`, and `app.js`.

The first viewport is the actual workstation, not a landing page. The layout:

- Top bar: app name, current book title, config health, upload button.
- Global settings band: voice, TTS parallel count, translation parallel count, provider metadata, context prompt, temporary API key fields.
- Text tools band: download converted TXT, clean watermarks, normalize spaces, remove repeated noise, remove decorative characters, split into chapters.
- Progress band: active split/book-level job status.
- Chapter table/list: one row per chapter with metadata, task progress bars, and action buttons.
- Editor modal or side panel: chapter text and translated text editing/viewing.

Controls should be dense and utilitarian. Avoid a marketing-style landing page or decorative card-heavy layout.

## Error Handling

- Unsupported file types return HTTP 400 with a clear message.
- Empty or unreadable books return HTTP 400.
- Missing provider configuration returns HTTP 400 with the config validation error.
- Failed segment requests are recorded per segment and reflected in job progress.
- If `ffmpeg` is missing, merging returns a clear error while keeping segment downloads available.
- Download endpoints return HTTP 404 when artifacts are not ready.
- Pause, resume, and stop controls are idempotent. Repeating a control request returns the current job state when the requested transition no longer changes anything.

## Testing

Automated tests should cover:

- TXT parsing and full-text conversion.
- EPUB parsing with spine order and navigation skipping.
- Cleaning operations for watermark patterns, whitespace normalization, repeated noise lines, and decorative characters.
- Chapter heading detection and size fallback.
- Repository create/read/update behavior for books, chapters, jobs, pause/stop flags, and segments.
- Chapter edit persistence and metadata recalculation.
- Translation scheduler progress, output ordering, failure recording, pause/resume behavior, and stop behavior with fake LLM client.
- TTS scheduler progress, output paths, merge behavior with fake TTS client and fake audio builder, pause/resume behavior, and stop behavior.
- Download path safety.
- FastAPI endpoints for upload, clean, split, edit, translate, TTS, pause, resume, stop, and download.
- Frontend JavaScript pure helpers for formatting counts, progress labels, and status mapping.

Tests must not call real DeepSeek or MiMo APIs.

## Git And Delivery

Initialize the current directory as a Git repository, keep runtime files out of Git, and push the completed project to:

```text
git@github.com:MicroSharpAnt/EBookToAudio.git
```

The final implementation should include:

- `README.md`
- `pyproject.toml`
- `config.example.yaml`
- source package under `src/ebook_to_audio/`
- tests under `tests/`
- a local run command such as `uvicorn "ebook_to_audio.web:create_app" --factory --host 127.0.0.1 --port 8000`
