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
assert.strictEqual(
  sandbox.window.EBookToAudio.chapterDisplayTitle({
    chapter_index: 0,
    title: "The Gift",
    translated_title: "麦琪的礼物",
  }),
  "麦琪的礼物",
);
assert.strictEqual(
  sandbox.window.EBookToAudio.chapterDisplayTitle({
    chapter_index: 0,
    title: "The Gift",
    translated_title: "",
  }),
  "The Gift",
);
const chapterBriefMarkup = sandbox.window.EBookToAudio.renderChapterBrief({
  id: 8,
  title: "The Gift",
  translated_title: "麦琪的礼物",
  summary: "本章讲述一对夫妻互赠礼物。",
});
assert(chapterBriefMarkup.includes("原章节名"));
assert(chapterBriefMarkup.includes("The Gift"));
assert(chapterBriefMarkup.includes("本章讲述一对夫妻互赠礼物。"));
assert(chapterBriefMarkup.includes("复制简介"));
assert(chapterBriefMarkup.includes('data-copy-summary="8"'));
const emptySummaryBriefMarkup = sandbox.window.EBookToAudio.renderChapterBrief({
  id: 9,
  title: "The Gift",
  translated_title: "麦琪的礼物",
});
assert(emptySummaryBriefMarkup.includes("暂无章节简介。"));
assert(!emptySummaryBriefMarkup.includes("复制简介"));
assert.strictEqual(sandbox.window.EBookToAudio.renderChapterBrief({ title: "第一章" }), "");
const chapterTagsMarkup = sandbox.window.EBookToAudio.renderChapterTags({
  tags: ["鲁迅", "散文", "有声书"],
});
assert(chapterTagsMarkup.includes("章节标签"));
assert(chapterTagsMarkup.includes("鲁迅"));
assert(chapterTagsMarkup.includes("有声书"));
assert(chapterTagsMarkup.includes('data-copy-tag="鲁迅"'));
assert(chapterTagsMarkup.includes('data-copy-tag="有声书"'));
assert.strictEqual(sandbox.window.EBookToAudio.renderChapterTags({ tags: [] }), "");
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
  plain(sandbox.window.EBookToAudio.buildChapterTagsPayload({
    translationProvider: "deepseek",
    translationApiKey: "sk-tags",
    translationContext: "发布到有声书平台",
  })),
  {
    provider: "deepseek",
    api_key: "sk-tags",
    context: "发布到有声书平台",
  },
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
assert.strictEqual(sandbox.window.EBookToAudio.buildTtsPayload({}).source, "translation");
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
  null,
);
assert.strictEqual(
  sandbox.window.EBookToAudio.bookPreviewUrl({ id: 7 }, "cleaned"),
  "/api/books/7/download/cleaned.txt",
);
assert.strictEqual(
  sandbox.window.EBookToAudio.bookPreviewUrl({ id: 7 }, "full"),
  null,
);
const segmentMarkup = sandbox.window.EBookToAudio.renderSegmentLinks(3, {
  segments: [{ id: 5, segment_index: 0, download_url: "/api/chapters/3/audio/segments/5/download" }],
});
assert(segmentMarkup.includes("<audio controls"));
assert(segmentMarkup.includes('src="/api/chapters/3/audio/segments/5/download"'));
assert(segmentMarkup.includes("下载音频段 1"));
assert.strictEqual(
  sandbox.window.EBookToAudio.shouldRefreshAudioDuringJob({
    kind: "tts",
    status: "running",
    chapter_id: 3,
  }),
  true,
);
assert.strictEqual(
  sandbox.window.EBookToAudio.shouldRefreshAudioDuringJob({
    kind: "translate",
    status: "running",
    chapter_id: 3,
  }),
  false,
);
assert.strictEqual(
  sandbox.window.EBookToAudio.chapterDetailsShouldOpen(
    { id: 3 },
    12,
    null,
    { kind: "tts", status: "running" },
    new Set(),
  ),
  true,
);
assert.strictEqual(
  sandbox.window.EBookToAudio.chapterDetailsShouldOpen({ id: 3 }, 12, null, null, new Set()),
  false,
);
assert.strictEqual(
  sandbox.window.EBookToAudio.chapterDetailsShouldOpen({ id: 3 }, 12, null, null, new Set([3])),
  true,
);
assert.strictEqual(
  sandbox.window.EBookToAudio.chapterDetailsShouldOpen({ id: 3 }, 3, null, null, new Set()),
  true,
);
const segmentOnlyAudioMarkup = sandbox.window.EBookToAudio.renderChapterAudioPanel(
  { id: 3, audio_path: null },
  {
    segments: [{ id: 5, segment_index: 0, download_url: "/api/chapters/3/audio/segments/5/download" }],
  },
);
assert(segmentOnlyAudioMarkup.includes('<details class="segment-details" open'));
const chapterAudioMarkup = sandbox.window.EBookToAudio.renderChapterAudioPanel(
  { id: 3, audio_path: "books/1/audio/chapter.wav" },
  {
    download_url: "/api/chapters/3/audio/download",
    segments: [{ id: 5, segment_index: 0, download_url: "/api/chapters/3/audio/segments/5/download" }],
  },
);
assert(chapterAudioMarkup.includes("chapter-audio-player"));
assert(chapterAudioMarkup.includes("合并音频"));
assert(chapterAudioMarkup.includes('src="/api/chapters/3/audio/download"'));
assert(chapterAudioMarkup.includes("<details"));
assert(chapterAudioMarkup.includes("查看片段音频 (1)"));
const completedJobMarkup = sandbox.window.EBookToAudio.jobMarkup({
  id: 1,
  kind: "tts",
  status: "completed",
  total_units: 3,
  completed_units: 3,
  failed_units: 0,
});
assert(!completedJobMarkup.includes("data-job-action"));
const runningJobMarkup = sandbox.window.EBookToAudio.jobMarkup({
  id: 2,
  kind: "tts",
  status: "running",
  total_units: 3,
  completed_units: 1,
  failed_units: 0,
});
assert(runningJobMarkup.includes('data-job-action="pause"'));
assert(!runningJobMarkup.includes('data-job-action="resume"'));
assert(runningJobMarkup.includes('data-job-action="stop"'));
const pausedJobMarkup = sandbox.window.EBookToAudio.jobMarkup({
  id: 3,
  kind: "tts",
  status: "paused",
  total_units: 3,
  completed_units: 1,
  failed_units: 0,
});
assert(!pausedJobMarkup.includes('data-job-action="pause"'));
assert(pausedJobMarkup.includes('data-job-action="resume"'));
assert(pausedJobMarkup.includes('data-job-action="stop"'));
assert.strictEqual(
  sandbox.window.EBookToAudio.chapterJobsMarkup([{
    id: 4,
    kind: "tts",
    status: "completed",
    total_units: 1,
    completed_units: 1,
  }]),
  "",
);
assert(
  sandbox.window.EBookToAudio.chapterJobsMarkup([{
    id: 5,
    kind: "translate",
    status: "running",
    total_units: 2,
    completed_units: 1,
  }]).includes("translate #5"),
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
