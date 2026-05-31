(function () {
  "use strict";

  const terminalStatuses = new Set(["completed", "completed_with_errors", "failed", "stopped"]);
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
      const translationParallel = $("#translationParallel");
      if (translationParallel && state.config.limits && state.config.limits.max_parallel_translation_segments) {
        translationParallel.value = Math.min(1, state.config.limits.max_parallel_translation_segments);
      }
      const ttsVoice = $("#ttsVoice");
      if (ttsVoice && state.config.tts && state.config.tts.default_voice) {
        ttsVoice.value = state.config.tts.default_voice;
      }
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

  async function loadCurrentBook() {
    try {
      state.book = await api("/api/books/current");
      renderBook();
      await loadChapters();
    } catch (error) {
      state.book = null;
      state.chapters = [];
      renderBook();
      renderChapters();
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
      link("下载当前 TXT", `/api/books/${state.book.id}/download.txt`),
      link("下载完整 TXT", `/api/books/${state.book.id}/download/full.txt`),
      link("下载清理 TXT", `/api/books/${state.book.id}/download/cleaned.txt`),
    );
    setHref("#bookTranslationsZip", `/api/books/${state.book.id}/translations/download.zip`);
    setHref("#bookAudioZip", `/api/books/${state.book.id}/audio/download.zip`);
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
    const ttsJob = latestJobForChapter(chapter.id, "tts");
    const translationJob = latestJobForChapter(chapter.id, "translate");
    card.innerHTML = `
      <div class="chapter-main">
        <div>
          <h3 class="chapter-title">${escapeHtml(chapter.title || `第 ${chapter.chapter_index + 1} 章`)}</h3>
          <p class="chapter-subtitle">
            ${formatCount(chapter.char_count)} 字符 · ${formatCount(chapter.paragraph_count)} 段落 ·
            译文 ${chapter.translation_path ? "已生成" : "未生成"} ·
            音频 ${chapter.audio_path || (audio && audio.segments && audio.segments.length) ? "已生成" : "未生成"}
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
        <a class="button-link" href="/api/chapters/${chapter.id}/audio/download.zip">音频 ZIP</a>
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
        (segment) =>
          `<a class="button-link" href="${segment.download_url || `/api/chapters/${chapterId}/audio/segments/${segment.id}/download`}">音频段 ${
            Number(segment.segment_index || 0) + 1
          }</a>`,
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
    if (board) {
      const active = Array.from(state.jobs.values())
        .filter((job) => job.kind !== "split")
        .sort((a, b) => Number(b.id) - Number(a.id))
        .slice(0, 4);
      board.innerHTML = active.map(jobMarkup).join("");
    }
    if (split) {
      const splitJob = Array.from(state.jobs.values())
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
      state.book = await api("/api/books", { method: "POST", body: form });
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
        body: JSON.stringify({
          provider: valueOf("#translationProvider"),
          api_key: valueOf("#translationApiKey"),
          prompt: "将文章翻译为中文",
          context: valueOf("#translationContext"),
          parallel_segments: numberOf("#translationParallel"),
        }),
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
        body: JSON.stringify({
          provider: valueOf("#ttsProvider"),
          api_key: valueOf("#ttsApiKey"),
          base_url: valueOf("#ttsBaseUrl"),
          model: valueOf("#ttsModel"),
          voice: valueOf("#ttsVoice") || "Cherry",
          context: valueOf("#ttsContext"),
          narration_style: valueOf("#narrationStyle"),
          character_tone: valueOf("#characterTone"),
          work_background: valueOf("#workBackground"),
          parallel_segments: numberOf("#ttsParallel"),
          source: valueOf("#ttsSource") || "chapter",
          merge: Boolean($("#mergeAudio") && $("#mergeAudio").checked),
        }),
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

  async function jobAction(jobId, action) {
    try {
      const job = await api(`/api/jobs/${jobId}/${action}`, { method: "POST" });
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
    state.jobs.set(job.id, job);
    renderJobs();
    if (terminalStatuses.has(job.status)) {
      clearJobTimer(job.id);
      if (job.kind === "split" || job.kind === "translate" || job.kind === "tts") {
        loadChapters();
      }
      return;
    }
    if (!state.jobTimers.has(job.id)) {
      const timer = setInterval(() => pollJob(job.id), 1200);
      state.jobTimers.set(job.id, timer);
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
    progressPercent,
    statusLabel,
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
