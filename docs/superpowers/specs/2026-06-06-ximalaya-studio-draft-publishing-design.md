# Ximalaya Studio Draft Publishing Design

## Goal

Add a chapter-level publishing action that opens Ximalaya Studio, uploads the generated chapter audio, fills the upload form, and stops before the final publish or review submission step.

The first version targets the user's existing Ximalaya creator workflow for album `122326236`:

`https://studio.ximalaya.com/upload?albumId=122326236`

It does not require Ximalaya Open Platform upload API access.

## User Workflow

1. The user translates a chapter, generates tags, and creates merged chapter audio.
2. The chapter card shows a `发布草稿到喜马拉雅` action when merged audio exists.
3. Clicking the action starts a local browser automation session.
4. The automation opens the Ximalaya Studio upload page for the configured album.
5. If the user is not logged in, the browser remains open for manual login, then continues where possible.
6. The automation uploads the WAV file and fills title, description, and tags.
7. The automation stops before clicking the final publish or submit-for-review button.
8. The app reports that the draft is ready for manual review.

## Metadata Mapping

The publishing draft is built from existing chapter data:

- Title: `chapter.translated_title`, falling back to `chapter.title`, then `第 N 章`.
- Description: `chapter.summary`, optionally followed by `publishing.ximalaya.description_footer`.
- Tags: `chapter.tags` plus `publishing.ximalaya.default_tags`, trimmed and deduplicated in order.
- Album: `publishing.ximalaya.album_id`, defaulting in the user's local config to `122326236`.
- Audio file: `chapter.audio_path`, resolved through `LocalStorage`.

If summary or tags are missing, publishing is still allowed. The UI should warn that fallback or empty metadata is being used, so the user can decide whether to generate missing metadata first.

## Configuration

Extend `config.example.yaml` with:

```yaml
publishing:
  ximalaya:
    album_id: "122326236"
    default_tags:
      - 有声书
    description_footer: ""
```

The existing `AppConfig.safe_metadata()` should expose whether an album ID and footer are configured and list non-secret default tags.

## Backend API

Add:

`POST /api/chapters/{chapter_id}/publish/ximalaya/draft`

The endpoint:

- Loads the chapter and book.
- Requires `chapter.audio_path`; otherwise returns `400` with a clear message.
- Requires `publishing.ximalaya.album_id`; otherwise returns `400`.
- Resolves the audio file safely through `LocalStorage`.
- Builds a `XimalayaDraft` object with title, description, tags, album ID, upload URL, and local audio path.
- Runs the Playwright publisher.
- Returns a JSON response describing the draft and automation state.

Example response:

```json
{
  "status": "ready_for_review",
  "album_id": "122326236",
  "title": "第一章（中文）",
  "description": "本章简介...",
  "tags": ["鲁迅", "散文", "有声书"],
  "message": "喜马拉雅草稿已填写，请在浏览器中确认后手动发布。"
}
```

## Publisher Implementation

Add a small Playwright-backed module, for example `ximalaya_publisher.py`.

Responsibilities:

- Launch a persistent browser profile so the user's Ximalaya login can be reused.
- Navigate to `https://studio.ximalaya.com/upload?albumId={album_id}`.
- Upload the local audio file through the page's file input.
- Fill title, description, and tags using resilient selectors.
- Stop before any final publish, submit, or audit button.
- Return a structured result for the API.

The publisher should use cautious selector handling:

- Prefer accessible labels and placeholder text when available.
- Keep a small fallback list for likely Chinese labels such as `标题`, `简介`, `标签`.
- Fail with an actionable error if a required field cannot be found.

The automation should be designed for supervised local use. It should not try to bypass login, CAPTCHA, platform risk prompts, or publishing confirmations.

## Frontend

Add a chapter action button:

`发布草稿到喜马拉雅`

Behavior:

- Enabled only when `chapter.audio_path` exists.
- Calls the new backend endpoint.
- Shows progress text while the browser automation is running.
- Shows success text when the browser has been filled and is waiting for user review.
- Shows clear errors for missing audio, missing album ID, missing Playwright dependency, login interruption, or field detection failure.

The existing chapter metadata UI remains the source of truth for title, summary, and tags. The publish button should not create tags or summaries automatically in this first version.

## Error Handling

Expected failures should produce clear user-facing messages:

- Missing merged audio: `请先生成并合并章节音频。`
- Missing album ID: `请在 config.yaml 中配置 publishing.ximalaya.album_id。`
- Missing Playwright browser/runtime: explain how to install the dependency and browser.
- Not logged in: keep the browser open and tell the user to log in, then retry.
- Upload page changed: report which field could not be found.

The endpoint should not record a publish as successful unless the form was actually filled.

## Testing

Automated tests should avoid opening real Ximalaya pages.

Backend tests:

- Draft metadata uses translated title, summary, chapter tags, and default tags.
- Draft metadata falls back to original title when translated title is missing.
- Endpoint rejects missing audio.
- Endpoint rejects missing album ID.
- Endpoint calls an injected fake publisher and returns the structured result.

Frontend tests:

- The chapter card renders the publish action only when merged audio exists.
- The click handler calls the new endpoint.
- Success and error statuses are surfaced.

Manual verification:

- Run the app locally.
- Use a chapter with merged audio.
- Click `发布草稿到喜马拉雅`.
- Confirm the browser opens the configured album upload page.
- Confirm title, description, tags, and audio are filled.
- Confirm the automation stops before final submission.

## Non-Goals

- No fully automatic final publish.
- No bypassing login, CAPTCHA, or platform risk controls.
- No Ximalaya Open Platform API integration in this version.
- No automatic generation of missing summaries or tags as part of the publish action.
- No batch publishing in this first version.
