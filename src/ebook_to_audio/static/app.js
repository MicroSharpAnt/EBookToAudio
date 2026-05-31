(function () {
  "use strict";

  const terminalStatuses = new Set(["completed", "completed_with_errors", "failed", "stopped"]);
  const defaultTranslationPrompt = "将文章翻译为中文";
  const defaultTtsVoice = "冰糖";
  const cleanLabels = {
    remove_watermarks: "去除文章水印",
    normalize_spacing: "去除文章多余空格等字符",
    remove_repeated_noise_lines: "删除重复广告/来源行",
    remove_decorative_characters: "删除装饰分隔线",
  };
  const statusLabels = {
    pending: "等待中",
    running: "运行中",
    paused: "已暂停",
    completed: "已完成",
    completed_with_errors: "部分完成",
    failed: "失败",
    stopped: "已停止",
  };

  const state = {
    config: null,
    book: null,
    chapters: [],
    jobs: new Map(),
    jobTimers: new Map(),
    audio: new Map(),
    editingChapterId: null,
  };

  function formatCount(value) {
    const number = Number(value || 0);
    return Number.isFinite(number) ? number.toLocaleString("en-US") : "0";
  }

  function progressPercent(job) {
    const total = Number(job && job.total_units);
    const completed = Number(job && job.completed_units);
    if (!Number.isFinite(total) || total <= 0) {
      return 0;
    }
    if (!Number.isFinite(completed) || completed <= 0) {
      return 0;
    }
    return Math.max(0, Math.min(100, Math.round((completed / total) * 100)));
  }

  function statusLabel(status) {
    return statusLabels[status] || status || "未知";
  }

  function wordCountLabel(chapter) {
    const chars = Number(chapter && chapter.char_count);
    const estimatedWords = Number.isFinite(chars) && chars > 0 ? Math.round(chars / 2) : 0;
    return `约 ${formatCount(estimatedWords)} 词`;
  }

  function settingsFromDom() {
    return {
      translationProvider: valueOf("#translationProvider"),
      translationApiKey: valueOf("#translationApiKey"),
      translationPrompt: valueOf("#translationPrompt"),
      translationContext: valueOf("#translationContext"),
      translationParallel: numberOf("#translationParallel"),
      ttsProvider: valueOf("#ttsProvider"),
      ttsApiKey: valueOf("#ttsApiKey"),
      ttsBaseUrl: valueOf("#ttsBaseUrl"),
      ttsModel: valueOf("#ttsModel"),
      ttsVoice: valueOf("#ttsVoice"),
      ttsContext: valueOf("#ttsContext"),
      ttsParallel: numberOf("#ttsParallel"),
      ttsSource: valueOf("#ttsSource"),
      mergeAudio: $("#mergeAudio") ? Boolean($("#mergeAudio").checked) : undefined,
    };
  }

  function buildTranslatePayload(settings) {
    const source = settings || settingsFromDom();
    return {
      provider: source.translationProvider || "",
      api_key: source.translationApiKey || "",
      prompt: source.translationPrompt || defaultTranslationPrompt,
      context: source.translationContext || "",
      parallel_segments: source.translationParallel || null,
    };
  }

  function buildTtsPayload(settings) {
    const source = settings || settingsFromDom();
    return {
      provider: source.ttsProvider || "",
      api_key: source.ttsApiKey || "",
      base_url: source.ttsBaseUrl || "",
      model: source.ttsModel || "",
      voice: source.ttsVoice || defaultTtsVoice,
      context: source.ttsContext || "",
      parallel_segments: source.ttsParallel || null,
      source: source.ttsSource || "chapter",
      merge: source.mergeAudio == null ? true : Boolean(source.mergeAudio),
    };
  }

  function resumePayloadForJob(job, settings) {
    const source = settings || settingsFromDom();
    if (job && job.kind === "translate" && source.translationApiKey) {
      return { api_key: source.translationApiKey };
    }
    if (job && job.kind === "tts" && source.ttsApiKey) {
      return { api_key: source.ttsApiKey };
    }
    return {};
  }

  function jobActionOptions(job, action, settings) {
    if (action !== "resume") {
      return { method: "POST" };
    }
    const payload = resumePayloadForJob(job, settings);
    if (!Object.keys(payload).length) {
      return { method: "POST" };
    }
    return { method: "POST", body: JSON.stringify(payload) };
  }

  function activeJobs(jobs) {
    return (jobs || []).filter((job) => job && !terminalStatuses.has(job.status));
  }

  function chapterHasAudio(chapter, audio) {
    return Boolean(
      (chapter && chapter.audio_path) ||
        (audio && Array.isArray(audio.segments) && audio.segments.length > 0),
    );
  }

  function audioForChapter(audioByChapter, chapterId) {
    if (!audioByChapter) {
      return null;
    }
    if (typeof audioByChapter.get === "function") {
      return audioByChapter.get(chapterId);
    }
    return audioByChapter[chapterId] || audioByChapter[String(chapterId)] || null;
  }

  function bookArtifactAvailability(chapters, audioByChapter) {
    const chapterRows = chapters || [];
    return {
      translations: chapterRows.some((chapter) => Boolean(chapter.translation_path)),
      audio: chapterRows.some((chapter) => chapterHasAudio(chapter, audioForChapter(audioByChapter, chapter.id))),
    };
  }

  function jobBelongsToCurrentBook(job, book) {
    if (!job || !book || book.id == null) {
      return true;
    }
    return Number(job.book_id) === Number(book.id);
  }

  function filterJobsForBook(jobs, book) {
    return (jobs || []).filter((job) => jobBelongsToCurrentBook(job, book));
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: options.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
      ...options,
    });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const body = await response.json();
        detail = body.detail || detail;
      } catch (_error) {
        detail = await response.text();
      }
      throw new Error(detail);
    }
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      return response.json();
    }
    return response.text();
  }

  function $(selector) {
    return document.querySelector(selector);
  }

  function setStatus(message, tone) {
    const target = $("#appStatus");
    if (!target) {
      return;
    }
    target.textContent = message;
    target.dataset.tone = tone || "";
  }

  function setText(selector, message) {
    const target = $(selector);
    if (target) {
      target.textContent = message;
    }
  }

  function link(label, href, className) {
    const anchor = document.createElement("a");
    anchor.className = `button-link ${className || ""}`.trim();
    anchor.href = href;
    anchor.textContent = label;
    return anchor;
  }

  function previewButton(label, variant) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.bookPreview = variant;
    button.textContent = label;
    return button;
  }

  function bookPreviewUrl(book, variant) {
    if (!book || book.id == null) {
      return null;
    }
    const suffixes = {
      current: "download.txt",
      full: "download/full.txt",
      cleaned: "download/cleaned.txt",
    };
    const suffix = suffixes[variant] || suffixes.current;
    return `/api/books/${book.id}/${suffix}`;
  }

  function bookPreviewLabel(variant) {
    return {
      current: "当前 TXT",
      full: "完整 TXT",
      cleaned: "清理后 TXT",
    }[variant] || "当前 TXT";
  }

  function setHref(selector, href) {
    const anchor = $(selector);
    if (!anchor) {
      return;
    }
    if (href) {
      anchor.href = href;
      anchor.removeAttribute("aria-disabled");
    } else {
      anchor.href = "#";
      anchor.setAttribute("aria-disabled", "true");
    }
  }

  async function loadInitial() {
    await loadConfig();
    await loadCurrentBook();
  }

  async function loadConfig() {
    try {
      state.config = await api("/api/config");
      const translationProvider = $("#translationProvider");
      if (translationProvider && state.config.active_translation_provider) {
        translationProvider.value = state.config.active_translation_provider;
      }
      renderTtsVoices(
        state.config.tts && state.config.tts.voices ? state.config.tts.voices : [],
        state.config.tts && state.config.tts.default_voice ? state.config.tts.default_voice : defaultTtsVoice,
      );
      const ttsParallel = $("#ttsParallel");
      if (ttsParallel && state.config.tts && state.config.tts.default_parallel_segments) {
        ttsParallel.value = state.config.tts.default_parallel_segments;
      }
      const ttsBaseUrl = $("#ttsBaseUrl");
      if (ttsBaseUrl && state.config.tts && state.config.tts.base_url) {
        ttsBaseUrl.value = state.config.tts.base_url;
      }
      const ttsModel = $("#ttsModel");
      if (ttsModel && state.config.tts && state.config.tts.model) {
        ttsModel.value = state.config.tts.model;
      }
      setStatus(state.config.loaded ? "配置已加载。" : `配置未完整加载：${state.config.error || "使用默认值"}`);
    } catch (error) {
      setStatus(`配置读取失败：${error.message}`, "error");
    }
  }

  function renderTtsVoices(voices, preferredVoice) {
    const ttsVoice = $("#ttsVoice");
    if (!ttsVoice) {
      return;
    }
    ttsVoice.replaceChildren();
    if (!voices.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "无可用音色";
      ttsVoice.append(option);
      return;
    }
    for (const voice of voices) {
      const option = document.createElement("option");
      option.value = String(voice);
      option.textContent = String(voice);
      ttsVoice.append(option);
    }
    const selected = voices.includes(preferredVoice) ? preferredVoice : voices[0];
    ttsVoice.value = selected || "";
  }

  async function loadCurrentBook() {
    try {
      const book = await api("/api/books/current");
      clearAllJobTimers();
      state.jobs.clear();
      state.book = book;
      renderBook();
      await loadChapters();
      await loadBookJobs();
    } catch (error) {
      clearAllJobTimers();
      state.book = null;
      state.chapters = [];
      state.jobs.clear();
      renderBook();
      renderChapters();
      renderJobs();
      setStatus(error.message === "no books" ? "尚未导入书籍。" : `读取书籍失败：${error.message}`, "error");
    }
  }

  async function loadChapters() {
    if (!state.book) {
      return;
    }
    try {
      state.chapters = await api(`/api/books/${state.book.id}/chapters`);
      renderChapters();
      updateBookArtifactLinks();
      await loadAudioMetadata();
    } catch (error) {
      setStatus(`读取章节失败：${error.message}`, "error");
    }
  }

  async function loadAudioMetadata() {
    await Promise.all(
      state.chapters.map(async (chapter) => {
        try {
          const metadata = await api(`/api/chapters/${chapter.id}/audio`);
          state.audio.set(chapter.id, metadata);
        } catch (_error) {
          state.audio.set(chapter.id, null);
        }
      }),
    );
    renderChapters();
    updateBookArtifactLinks();
  }

  async function loadBookJobs() {
    if (!state.book) {
      return;
    }
    try {
      const jobs = await api(`/api/books/${state.book.id}/jobs`);
      state.jobs.clear();
      filterJobsForBook(jobs, state.book).forEach((job) => state.jobs.set(job.id, job));
      renderJobs();
      activeJobs(filterJobsForBook(jobs, state.book)).forEach((job) => startJobPolling(job.id));
    } catch (error) {
      setStatus(`读取任务失败：${error.message}`, "error");
    }
  }

  function renderBook() {
    const info = $("#bookInfo");
    const downloads = $("#bookDownloads");
    if (!info || !downloads) {
      return;
    }
    downloads.replaceChildren();
    if (!state.book) {
      info.textContent = "尚未导入书籍。";
      setHref("#bookTranslationsZip", null);
      setHref("#bookAudioZip", null);
      return;
    }
    info.innerHTML = `<strong>${escapeHtml(state.book.title)}</strong><br>${escapeHtml(
      state.book.original_filename,
    )} · ${escapeHtml(state.book.source_format)}`;
    downloads.append(
      previewButton("查看当前 TXT", "current"),
      previewButton("查看完整 TXT", "full"),
      previewButton("查看清理 TXT", "cleaned"),
      link("下载当前 TXT", `/api/books/${state.book.id}/download.txt`),
      link("下载完整 TXT", `/api/books/${state.book.id}/download/full.txt`),
      link("下载清理 TXT", `/api/books/${state.book.id}/download/cleaned.txt`),
    );
    updateBookArtifactLinks();
  }

  function updateBookArtifactLinks() {
    if (!state.book) {
      setHref("#bookTranslationsZip", null);
      setHref("#bookAudioZip", null);
      return;
    }
    const available = bookArtifactAvailability(state.chapters, state.audio);
    setHref(
      "#bookTranslationsZip",
      available.translations ? `/api/books/${state.book.id}/translations/download.zip` : null,
    );
    setHref("#bookAudioZip", available.audio ? `/api/books/${state.book.id}/audio/download.zip` : null);
  }

  function renderChapters() {
    const list = $("#chapters");
    const summary = $("#chapterSummary");
    if (!list || !summary) {
      return;
    }
    list.replaceChildren();
    if (!state.book) {
      summary.textContent = "请先导入一本 EPUB 或 TXT。";
      return;
    }
    if (!state.chapters.length) {
      summary.textContent = "尚未拆分章节。点击“按章节分成多个txt”开始。";
      return;
    }
    const chars = state.chapters.reduce((sum, chapter) => sum + Number(chapter.char_count || 0), 0);
    const paragraphs = state.chapters.reduce((sum, chapter) => sum + Number(chapter.paragraph_count || 0), 0);
    summary.textContent = `${formatCount(state.chapters.length)} 章 · ${formatCount(chars)} 字符 · ${formatCount(
      paragraphs,
    )} 段落`;
    state.chapters.forEach((chapter) => list.appendChild(chapterCard(chapter)));
  }

  function chapterCard(chapter) {
    const card = document.createElement("article");
    card.className = "chapter-card";
    const audio = state.audio.get(chapter.id);
    const hasAudio = chapterHasAudio(chapter, audio);
    const ttsJob = latestJobForChapter(chapter.id, "tts");
    const translationJob = latestJobForChapter(chapter.id, "translate");
    card.innerHTML = `
      <div class="chapter-main">
        <div>
          <h3 class="chapter-title">${escapeHtml(chapter.title || `第 ${chapter.chapter_index + 1} 章`)}</h3>
          <p class="chapter-subtitle">
            ${formatCount(chapter.char_count)} 字符 · ${wordCountLabel(chapter)} · ${formatCount(chapter.paragraph_count)} 段落 ·
            译文 ${chapter.translation_path ? "已生成" : "未生成"} ·
            音频 ${hasAudio ? "已生成" : "未生成"}
          </p>
        </div>
        <div class="chapter-actions">
          <button type="button" data-view="${chapter.id}">查看</button>
          <button type="button" data-translate="${chapter.id}" class="primary">将文章翻译为中文</button>
          <button type="button" data-tts="${chapter.id}">TTS</button>
          ${ttsJob ? `<button type="button" data-merge="${ttsJob.id}">合并音频</button>` : ""}
        </div>
      </div>
      <div class="artifact-links">
        <a class="button-link" href="/api/chapters/${chapter.id}/download.txt">章节 TXT</a>
        <a class="button-link" href="/api/chapters/${chapter.id}/download.zip">章节 ZIP</a>
        ${
          chapter.translation_path
            ? `<a class="button-link" href="/api/chapters/${chapter.id}/translation/download.txt">译文 TXT</a>
               <a class="button-link" href="/api/chapters/${chapter.id}/translation/download.zip">译文 ZIP</a>`
            : ""
        }
        ${
          chapter.audio_path
            ? `<a class="button-link" href="/api/chapters/${chapter.id}/audio/download">合并 WAV</a>`
            : ""
        }
        ${hasAudio ? `<a class="button-link" href="/api/chapters/${chapter.id}/audio/download.zip">音频 ZIP</a>` : ""}
      </div>
      <div class="audio-segments">${renderSegmentLinks(chapter.id, audio)}</div>
      <div class="job-slot">
        ${translationJob ? jobMarkup(translationJob) : ""}
        ${ttsJob ? jobMarkup(ttsJob) : ""}
      </div>
    `;
    return card;
  }

  function renderSegmentLinks(chapterId, audio) {
    if (!audio || !Array.isArray(audio.segments) || !audio.segments.length) {
      return "";
    }
    return audio.segments
      .map(
        (segment) => {
          const index = Number(segment.segment_index || 0) + 1;
          const url = segment.download_url || `/api/chapters/${chapterId}/audio/segments/${segment.id}/download`;
          const safeUrl = escapeHtml(url);
          return `<div class="segment-player">
            <span class="segment-title">音频段 ${index}</span>
            <audio controls preload="none" src="${safeUrl}"></audio>
            <a class="button-link" href="${safeUrl}">下载音频段 ${index}</a>
          </div>`;
        },
      )
      .join("");
  }

  function latestJobForChapter(chapterId, kind) {
    return Array.from(state.jobs.values())
      .filter((job) => job.chapter_id === chapterId && job.kind === kind)
      .sort((a, b) => Number(b.id) - Number(a.id))[0];
  }

  function jobMarkup(job) {
    const percent = progressPercent(job);
    return `
      <div class="job-row" data-job-row="${job.id}">
        <div class="job-title">
          <span>${escapeHtml(job.kind)} #${job.id}</span>
          <span class="status-${escapeHtml(job.status)}">${statusLabel(job.status)} · ${percent}%</span>
        </div>
        <div class="progress" aria-label="进度 ${percent}%"><span style="width:${percent}%"></span></div>
        <div class="meta">${formatCount(job.completed_units)} / ${formatCount(job.total_units)} 完成${
          job.failed_units ? ` · ${formatCount(job.failed_units)} 失败` : ""
        }${job.error_message ? ` · ${escapeHtml(job.error_message)}` : ""}</div>
        <div class="job-actions">
          <button type="button" data-job-action="pause" data-job-id="${job.id}">暂停</button>
          <button type="button" data-job-action="resume" data-job-id="${job.id}">继续</button>
          <button type="button" data-job-action="stop" data-job-id="${job.id}" class="danger">停止</button>
        </div>
      </div>
    `;
  }

  function renderJobs() {
    const board = $("#jobBoard");
    const split = $("#splitJob");
    const jobsForBook = filterJobsForBook(Array.from(state.jobs.values()), state.book);
    if (board) {
      const active = jobsForBook
        .filter((job) => job.kind !== "split")
        .sort((a, b) => Number(b.id) - Number(a.id))
        .slice(0, 4);
      board.innerHTML = active.map(jobMarkup).join("");
    }
    if (split) {
      const splitJob = jobsForBook
        .filter((job) => job.kind === "split")
        .sort((a, b) => Number(b.id) - Number(a.id))[0];
      split.innerHTML = splitJob ? jobMarkup(splitJob) : "";
    }
    renderChapters();
  }

  async function uploadBook(event) {
    event.preventDefault();
    const input = $("#bookFile");
    if (!input || !input.files || !input.files[0]) {
      setStatus("请选择 .epub 或 .txt 文件。", "error");
      return;
    }
    const form = new FormData();
    form.append("file", input.files[0]);
    try {
      setStatus("正在上传并转换为 TXT...");
      const book = await api("/api/books", { method: "POST", body: form });
      clearAllJobTimers();
      state.book = book;
      state.chapters = [];
      state.jobs.clear();
      renderBook();
      renderChapters();
      setStatus("导入完成。");
    } catch (error) {
      setStatus(`导入失败：${error.message}`, "error");
    }
  }

  async function cleanBook(operations) {
    if (!state.book) {
      setStatus("请先导入书籍。", "error");
      return;
    }
    try {
      const result = await api(`/api/books/${state.book.id}/clean`, {
        method: "POST",
        body: JSON.stringify({ operations }),
      });
      const detail = (result.results || [])
        .map((item) => `${cleanLabels[item.operation] || item.operation}: ${formatCount(item.before_chars)} -> ${formatCount(item.after_chars)}`)
        .join("；");
      setText("#cleanResult", detail || "清理完成。");
      setStatus(`清理完成：${formatCount(result.char_count)} 字符。`);
      await loadCurrentBook();
      await openBookPreview("cleaned");
    } catch (error) {
      setStatus(`清理失败：${error.message}`, "error");
    }
  }

  async function splitBook() {
    if (!state.book) {
      setStatus("请先导入书籍。", "error");
      return;
    }
    try {
      const response = await api(`/api/books/${state.book.id}/split`, { method: "POST" });
      trackJob(response.job || response);
      setStatus("拆分任务已创建。");
      await loadChapters();
    } catch (error) {
      setStatus(`拆分失败：${error.message}`, "error");
    }
  }

  async function translateChapter(chapterId) {
    try {
      const response = await api(`/api/chapters/${chapterId}/translate`, {
        method: "POST",
        body: JSON.stringify(buildTranslatePayload()),
      });
      trackJob(response);
      setStatus(`章节 ${chapterId} 翻译任务已创建。`);
    } catch (error) {
      setStatus(`翻译失败：${error.message}`, "error");
    }
  }

  async function ttsChapter(chapterId) {
    try {
      const response = await api(`/api/chapters/${chapterId}/tts`, {
        method: "POST",
        body: JSON.stringify(buildTtsPayload()),
      });
      trackJob(response);
      setStatus(`章节 ${chapterId} TTS 任务已创建。`);
    } catch (error) {
      setStatus(`TTS 失败：${error.message}`, "error");
    }
  }

  async function mergeAudio(jobId) {
    try {
      const response = await api(`/api/jobs/${jobId}/audio/merge`, { method: "POST" });
      trackJob(response.job);
      await loadChapters();
      setStatus(response.merged ? "音频已合并。" : "暂无可合并音频。");
    } catch (error) {
      setStatus(`合并失败：${error.message}`, "error");
    }
  }

  async function openBookPreview(variant) {
    if (!state.book) {
      setStatus("请先导入书籍。", "error");
      return;
    }
    const url = bookPreviewUrl(state.book, variant);
    if (!url) {
      setStatus("没有可预览的文本。", "error");
      return;
    }
    const dialog = $("#bookTextDialog");
    const title = $("#bookPreviewTitle");
    const text = $("#bookPreviewText");
    const status = $("#bookPreviewStatus");
    if (!dialog || !title || !text) {
      return;
    }
    try {
      setStatus(`正在读取${bookPreviewLabel(variant)}...`);
      const content = await api(url);
      title.value = `${state.book.title || "书籍"} · ${bookPreviewLabel(variant)}`;
      text.value = content;
      if (status) {
        status.textContent = `${formatCount(content.length)} 字符`;
      }
      if (typeof dialog.showModal === "function") {
        dialog.showModal();
      } else {
        dialog.setAttribute("open", "open");
      }
      setStatus(`${bookPreviewLabel(variant)}已打开。`);
    } catch (error) {
      setStatus(`读取文本失败：${error.message}`, "error");
    }
  }

  async function jobAction(jobId, action) {
    try {
      const currentJob = state.jobs.get(jobId);
      const job = await api(`/api/jobs/${jobId}/${action}`, jobActionOptions(currentJob, action));
      trackJob(job);
      setStatus(`任务 #${jobId} ${statusLabel(job.status)}。`);
    } catch (error) {
      setStatus(`任务控制失败：${error.message}`, "error");
    }
  }

  function trackJob(job) {
    if (!job || !job.id) {
      return;
    }
    if (!jobBelongsToCurrentBook(job, state.book)) {
      clearJobTimer(job.id);
      return;
    }
    state.jobs.set(job.id, job);
    renderJobs();
    if (terminalStatuses.has(job.status)) {
      clearJobTimer(job.id);
      if (job.kind === "split" || job.kind === "translate" || job.kind === "tts") {
        loadChapters();
      }
      return;
    }
    startJobPolling(job.id);
  }

  function startJobPolling(jobId) {
    if (!state.jobTimers.has(jobId)) {
      const timer = setInterval(() => pollJob(jobId), 1200);
      state.jobTimers.set(jobId, timer);
    }
  }

  async function pollJob(jobId) {
    try {
      const job = await api(`/api/jobs/${jobId}`);
      trackJob(job);
    } catch (error) {
      clearJobTimer(jobId);
      setStatus(`任务 #${jobId} 轮询失败：${error.message}`, "error");
    }
  }

  function clearJobTimer(jobId) {
    const timer = state.jobTimers.get(jobId);
    if (timer) {
      clearInterval(timer);
      state.jobTimers.delete(jobId);
    }
  }

  function clearAllJobTimers() {
    state.jobTimers.forEach((timer) => clearInterval(timer));
    state.jobTimers.clear();
  }

  async function openEditor(chapterId) {
    const chapter = state.chapters.find((item) => item.id === chapterId);
    if (!chapter) {
      return;
    }
    state.editingChapterId = chapterId;
    const dialog = $("#chapterDialog");
    const title = $("#editorTitle");
    const text = $("#editorText");
    if (!dialog || !title || !text) {
      return;
    }
    title.value = chapter.title || "";
    text.value = "正在读取...";
    setText("#editorStatus", "");
    try {
      text.value = await api(`/api/chapters/${chapterId}/download.txt`);
      if (typeof dialog.showModal === "function") {
        dialog.showModal();
      } else {
        dialog.setAttribute("open", "open");
      }
    } catch (error) {
      setStatus(`读取章节正文失败：${error.message}`, "error");
    }
  }

  async function saveEditor() {
    if (!state.editingChapterId) {
      return;
    }
    try {
      const updated = await api(`/api/chapters/${state.editingChapterId}`, {
        method: "PUT",
        body: JSON.stringify({
          title: valueOf("#editorTitle"),
          text: valueOf("#editorText"),
        }),
      });
      state.chapters = state.chapters.map((chapter) => (chapter.id === updated.id ? updated : chapter));
      renderChapters();
      setText("#editorStatus", "已保存。");
    } catch (error) {
      setText("#editorStatus", `保存失败：${error.message}`);
    }
  }

  function valueOf(selector) {
    const element = $(selector);
    return element && typeof element.value === "string" ? element.value.trim() : "";
  }

  function numberOf(selector) {
    const value = Number(valueOf(selector));
    return Number.isFinite(value) && value > 0 ? value : null;
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function bindEvents() {
    const uploadForm = $("#uploadForm");
    if (uploadForm) {
      uploadForm.addEventListener("submit", uploadBook);
    }
    const refreshButton = $("#refreshButton");
    if (refreshButton) {
      refreshButton.addEventListener("click", loadCurrentBook);
    }
    const splitButton = $("#splitButton");
    if (splitButton) {
      splitButton.addEventListener("click", splitBook);
    }
    document.querySelectorAll("[data-clean-operation]").forEach((button) => {
      button.addEventListener("click", () => cleanBook([button.dataset.cleanOperation]));
    });
    const selectedClean = $("#applySelectedClean");
    if (selectedClean) {
      selectedClean.addEventListener("click", () => {
        const operations = Array.from(document.querySelectorAll("input[name='cleanOption']:checked")).map(
          (input) => input.value,
        );
        cleanBook(operations);
      });
    }
    const previewCleaned = $("#previewCleanedText");
    if (previewCleaned) {
      previewCleaned.addEventListener("click", () => openBookPreview("cleaned"));
    }
    const bookDownloads = $("#bookDownloads");
    if (bookDownloads) {
      bookDownloads.addEventListener("click", (event) => {
        const target = event.target.closest("button[data-book-preview]");
        if (target) {
          openBookPreview(target.dataset.bookPreview || "current");
        }
      });
    }
    const chapters = $("#chapters");
    if (chapters) {
      chapters.addEventListener("click", (event) => {
        const target = event.target.closest("button");
        if (!target) {
          return;
        }
        if (target.dataset.view) {
          openEditor(Number(target.dataset.view));
        } else if (target.dataset.translate) {
          translateChapter(Number(target.dataset.translate));
        } else if (target.dataset.tts) {
          ttsChapter(Number(target.dataset.tts));
        } else if (target.dataset.merge) {
          mergeAudio(Number(target.dataset.merge));
        }
      });
    }
    document.addEventListener("click", (event) => {
      const target = event.target.closest("[data-job-action]");
      if (target) {
        jobAction(Number(target.dataset.jobId), target.dataset.jobAction);
      }
    });
    const saveButton = $("#saveChapterButton");
    if (saveButton) {
      saveButton.addEventListener("click", saveEditor);
    }
  }

  window.EBookToAudio = {
    formatCount,
    wordCountLabel,
    progressPercent,
    statusLabel,
    buildTranslatePayload,
    buildTtsPayload,
    renderTtsVoices,
    bookPreviewUrl,
    renderSegmentLinks,
    jobActionOptions,
    activeJobs,
    chapterHasAudio,
    bookArtifactAvailability,
    jobBelongsToCurrentBook,
    filterJobsForBook,
    api,
    renderChapters,
  };

  if (typeof document !== "undefined" && document.addEventListener) {
    document.addEventListener("DOMContentLoaded", () => {
      bindEvents();
      loadInitial();
    });
  }
})();
