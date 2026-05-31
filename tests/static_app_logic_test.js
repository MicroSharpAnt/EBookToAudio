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
