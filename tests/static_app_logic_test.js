const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const appPath = path.join(__dirname, "..", "src", "ebook_to_audio", "static", "app.js");
const source = fs.readFileSync(appPath, "utf8");

const sandbox = {
  console,
  document: {
    addEventListener() {},
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
  },
  fetch: async () => {
    throw new Error("fetch should not be called by helper tests");
  },
  FormData: class FormData {},
  window: {},
};
sandbox.window = sandbox;

vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: appPath });

assert.strictEqual(sandbox.window.EBookToAudio.formatCount(12345), "12,345");
assert.strictEqual(
  sandbox.window.EBookToAudio.progressPercent({ total_units: 4, completed_units: 1 }),
  25,
);
assert.strictEqual(sandbox.window.EBookToAudio.statusLabel("completed_with_errors"), "部分完成");
assert.strictEqual(sandbox.window.EBookToAudio.wordCountLabel({ char_count: 1200 }), "约 600 词");
function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

assert.deepStrictEqual(
  plain(sandbox.window.EBookToAudio.buildTranslatePayload({
    translationProvider: "deepseek",
    translationApiKey: "sk-translation",
    translationPrompt: "请翻译为现代中文",
    translationContext: "保留人名",
    translationParallel: 2,
  })),
  {
    provider: "deepseek",
    api_key: "sk-translation",
    prompt: "请翻译为现代中文",
    context: "保留人名",
    parallel_segments: 2,
  },
);
assert.strictEqual(
  sandbox.window.EBookToAudio.buildTranslatePayload({}).prompt,
  "将文章翻译为中文",
);
const ttsPayload = sandbox.window.EBookToAudio.buildTtsPayload({
  ttsProvider: "mimo",
  ttsApiKey: "sk-tts",
  ttsBaseUrl: "https://token-plan-cn.xiaomimimo.com/v1",
  ttsModel: "mimo-v2.5-tts",
  ttsVoice: "茉莉",
  ttsContext: "旁白舒缓，人物语气克制，背景为鲁迅散文。",
  ttsParallel: 2,
  ttsSource: "translation",
  mergeAudio: false,
});
assert.deepStrictEqual(plain(ttsPayload), {
  provider: "mimo",
  api_key: "sk-tts",
  base_url: "https://token-plan-cn.xiaomimimo.com/v1",
  model: "mimo-v2.5-tts",
  voice: "茉莉",
  context: "旁白舒缓，人物语气克制，背景为鲁迅散文。",
  parallel_segments: 2,
  source: "translation",
  merge: false,
});
assert.strictEqual(Object.prototype.hasOwnProperty.call(ttsPayload, "narration_style"), false);
assert.strictEqual(Object.prototype.hasOwnProperty.call(ttsPayload, "character_tone"), false);
assert.strictEqual(Object.prototype.hasOwnProperty.call(ttsPayload, "work_background"), false);
assert.deepStrictEqual(
  plain(sandbox.window.EBookToAudio.jobActionOptions(
    { id: 10, kind: "translate" },
    "resume",
    { translationApiKey: "sk-translation", ttsApiKey: "sk-tts" },
  )),
  { method: "POST", body: JSON.stringify({ api_key: "sk-translation" }) },
);
assert.deepStrictEqual(
  plain(sandbox.window.EBookToAudio.jobActionOptions(
    { id: 11, kind: "tts" },
    "resume",
    { translationApiKey: "sk-translation", ttsApiKey: "sk-tts" },
  )),
  { method: "POST", body: JSON.stringify({ api_key: "sk-tts" }) },
);
assert.deepStrictEqual(
  plain(sandbox.window.EBookToAudio.jobActionOptions(
    { id: 11, kind: "tts" },
    "pause",
    { ttsApiKey: "sk-tts" },
  )),
  { method: "POST" },
);
assert.deepStrictEqual(
  plain(sandbox.window.EBookToAudio.activeJobs([
    { id: 1, status: "completed" },
    { id: 2, status: "paused" },
    { id: 3, status: "running" },
    { id: 4, status: "failed" },
  ])),
  [
    { id: 2, status: "paused" },
    { id: 3, status: "running" },
  ],
);
assert.deepStrictEqual(
  plain(sandbox.window.EBookToAudio.bookArtifactAvailability(
    [
      { id: 1, translation_path: "translation.txt", audio_path: null },
      { id: 2, translation_path: null, audio_path: null },
    ],
    {
      2: { segments: [{ id: 5 }] },
    },
  )),
  { translations: true, audio: true },
);
assert.strictEqual(
  sandbox.window.EBookToAudio.chapterHasAudio(
    { id: 3, audio_path: null },
    { segments: [] },
  ),
  false,
);
assert.strictEqual(
  sandbox.window.EBookToAudio.bookPreviewUrl({ id: 7 }, "current"),
  "/api/books/7/download.txt",
);
assert.strictEqual(
  sandbox.window.EBookToAudio.bookPreviewUrl({ id: 7 }, "cleaned"),
  "/api/books/7/download/cleaned.txt",
);
assert.strictEqual(
  sandbox.window.EBookToAudio.bookPreviewUrl({ id: 7 }, "full"),
  "/api/books/7/download/full.txt",
);
const segmentMarkup = sandbox.window.EBookToAudio.renderSegmentLinks(3, {
  segments: [{ id: 5, segment_index: 0, download_url: "/api/chapters/3/audio/segments/5/download" }],
});
assert(segmentMarkup.includes("<audio controls"));
assert(segmentMarkup.includes('src="/api/chapters/3/audio/segments/5/download"'));
assert(segmentMarkup.includes("下载音频段 1"));
assert.strictEqual(
  sandbox.window.EBookToAudio.buildTranslatePayload({ translationProvider: "default" }).parallel_segments,
  null,
);
assert.strictEqual(
  sandbox.window.EBookToAudio.jobBelongsToCurrentBook({ id: 7, book_id: 2 }, { id: 2 }),
  true,
);
assert.strictEqual(
  sandbox.window.EBookToAudio.jobBelongsToCurrentBook({ id: 8, book_id: 1 }, { id: 2 }),
  false,
);
assert.deepStrictEqual(
  plain(sandbox.window.EBookToAudio.filterJobsForBook(
    [
      { id: 7, book_id: 2 },
      { id: 8, book_id: 1 },
      { id: 9, book_id: 2 },
    ],
    { id: 2 },
  )),
  [
    { id: 7, book_id: 2 },
    { id: 9, book_id: 2 },
  ],
);
