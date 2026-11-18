const state = {
  inputMode: "url",
  jobs: [],
  presets: [],
  ownerFilter: "ALL",
  selectedJobId: null,
  selectedJob: null,
  pollTimer: null,
  splitEditorOpen: true,
  frameW: 1920,
  frameH: 1080,
  videoEncoder: "h264_nvenc",
};

const ACTIVE_JOB_STATUSES = new Set(["queued", "downloading", "transcribing", "analyzing", "rendering"]);
const ACTIVE_CLIP_STATUSES = new Set(["queued", "rendering"]);
const PLATFORMS = [
  { id: "tiktok", label: "TikTok" },
  { id: "ig", label: "IG" },
  { id: "yt", label: "YT" },
  { id: "fb", label: "FB" },
];

const memberInput = document.getElementById("member-name-input");
memberInput.value = localStorage.getItem("clipper_member_name") || "Angger";
memberInput.addEventListener("input", () => {
  localStorage.setItem("clipper_member_name", memberInput.value.trim() || "Tim");
});

function getActiveMemberName() {
  return memberInput.value.trim() || "Tim";
}

function showToast(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.classList.remove("hidden");
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => toast.classList.add("hidden"), 3200);
}

async function apiFetch(url, options = {}) {
  const res = await fetch(url, options);
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const data = await res.json();
      detail = data.detail || detail;
    } catch (_) {}
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

function setInputMode(mode) {
  state.inputMode = mode;
  document.getElementById("tab-url").classList.toggle("active", mode === "url");
  document.getElementById("tab-upload").classList.toggle("active", mode === "upload");
  document.getElementById("url-input-group").classList.toggle("hidden", mode !== "url");
  document.getElementById("upload-input-group").classList.toggle("hidden", mode !== "upload");
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function populateOwnerFilter() {
  const select = document.getElementById("filter-owner");
  const current = state.ownerFilter || "ALL";
  const owners = Array.from(new Set(state.jobs.map((j) => j.owner || "Tim"))).sort();
  select.innerHTML = `<option value="ALL">Semua Tim</option>` + owners
    .map((o) => `<option value="${escapeHtml(o)}">${escapeHtml(o)}</option>`)
    .join("");
  select.value = owners.includes(current) || current === "ALL" ? current : "ALL";
  state.ownerFilter = select.value;
}

function renderJobsList() {
  const container = document.getElementById("jobs-list");
  const filtered = state.ownerFilter === "ALL"
    ? state.jobs
    : state.jobs.filter((j) => (j.owner || "Tim") === state.ownerFilter);
  if (!filtered.length) {
    container.innerHTML = `<div class="empty-muted">Belum ada job.</div>`;
    return;
  }
  container.innerHTML = filtered
    .map((job) => {
      const active = job.id === state.selectedJobId ? "active" : "";
      const clipsLabel = `${job.done_count || 0}/${job.clip_count || 0} klip`;
      return `
        <div class="job-item ${active}" data-job-id="${escapeHtml(job.id)}">
          <div class="job-item-title">${escapeHtml(job.title || job.id)}</div>
          <div class="job-item-meta">
            <span class="badge badge-${escapeHtml(job.status)}">${escapeHtml(job.status)}</span>
            <span>👤 ${escapeHtml(job.owner || "Tim")} • ${clipsLabel}</span>
          </div>
        </div>
      `;
    })
    .join("");

  container.querySelectorAll(".job-item").forEach((el) => {
    el.addEventListener("click", () => selectJob(el.dataset.jobId));
  });
}

function updateSplitOverlays() {
  const fw = state.frameW || 1920;
  const fh = state.frameH || 1080;
  const getVal = (id) => Math.max(0, Number(document.getElementById(id).value) || 0);

  const cam = {
    x: getVal("cam_x"),
    y: getVal("cam_y"),
    w: Math.max(16, getVal("cam_w")),
    h: Math.max(16, getVal("cam_h")),
  };
  const game = {
    x: getVal("game_x"),
    y: getVal("game_y"),
    w: Math.max(16, getVal("game_w")),
    h: Math.max(16, getVal("game_h")),
  };

  const applyBox = (el, box) => {
    el.style.left = `${(box.x / fw) * 100}%`;
    el.style.top = `${(box.y / fh) * 100}%`;
    el.style.width = `${Math.min(100, (box.w / fw) * 100)}%`;
    el.style.height = `${Math.min(100, (box.h / fh) * 100)}%`;
  };

  applyBox(document.getElementById("overlay-cam"), cam);
  applyBox(document.getElementById("overlay-game"), game);
}

function loadSplitInputs(splitConfig) {
  const keys = ["cam_x", "cam_y", "cam_w", "cam_h", "game_x", "game_y", "game_w", "game_h"];
  keys.forEach((k) => {
    const input = document.getElementById(k);
    if (input) input.value = splitConfig[k] ?? 0;
  });
  updateSplitOverlays();
}

function createClipCard(clip, job) {
  const card = document.createElement("div");
  card.className = "clip-card";
  card.dataset.clipId = clip.id;

  const words = Array.isArray(clip.words) ? clip.words.map((w) => ({ ...w })) : [];
  const duration = (Number(clip.end_time) - Number(clip.start_time)).toFixed(1);

  card.innerHTML = `
    <div class="clip-editor">
      <div class="clip-top-row">
        <input type="text" class="clip-title-input" value="${escapeHtml(clip.title || "")}" />
        <span class="meta-pill">Skor ${Number(clip.score || 0).toFixed(2)} • ${duration}s</span>
        <span class="badge badge-${escapeHtml(clip.status)}">${escapeHtml(clip.status)}</span>
      </div>

      <div class="clip-controls-row">
        <div class="clip-field">
          <label>Start (detik)</label>
          <input type="number" step="0.1" min="0" class="clip-start" value="${Number(clip.start_time).toFixed(2)}" />
        </div>
        <div class="clip-field">
          <label>End (detik)</label>
          <input type="number" step="0.1" min="1" class="clip-end" value="${Number(clip.end_time).toFixed(2)}" />
        </div>
        <div class="clip-field">
          <label>Layout Klip</label>
          <select class="clip-layout">
            <option value="fullscreen" ${clip.layout_mode === "fullscreen" ? "selected" : ""}>Fullscreen 9:16</option>
            <option value="splitscreen" ${clip.layout_mode === "splitscreen" ? "selected" : ""}>Split-Screen</option>
          </select>
        </div>
        <button type="button" class="btn btn-secondary btn-small reslice-btn">
          Ambil Ulang Transkrip
        </button>
      </div>

      <div class="clip-field" style="margin-bottom: 10px;">
        <label>Teks Hook Atas (Opsional)</label>
        <input type="text" class="clip-hook-input" placeholder="Mis. GTA VI 100% AKU SIAP!" value="${escapeHtml(clip.hook_text || "")}" />
      </div>

      <div class="find-replace-bar">
        <input type="text" class="find-input" placeholder="Cari kata typo (mis. Lu Siat Aminos)" />
        <input type="text" class="replace-input" placeholder="Ganti jadi (mis. Lucia Caminos)" />
        <button type="button" class="btn btn-secondary btn-small replace-btn">Ganti Semua</button>
      </div>

      <div class="words-editor"></div>

      <div class="clip-actions">
        <button type="button" class="btn btn-secondary save-clip-btn">Simpan Subtitle & Waktu</button>
        <button type="button" class="btn btn-primary render-clip-btn" ${!job.raw_exists ? 'disabled title="File video mentah sudah dihapus"' : ""}>
          ${state.videoEncoder === "h264_nvenc" ? "Render Klip (GPU NVENC)" : "Render Klip (CPU x264)"}
        </button>
        ${
          clip.output_exists
            ? `<a class="btn btn-success" href="/api/clips/${encodeURIComponent(clip.id)}/download">Download MP4</a>`
            : ""
        }
      </div>
      <div class="publish-checklist">
        <span class="publish-label">Status Upload:</span>
        ${PLATFORMS.map((p) => {
          const isPub = (clip.published_platforms || []).includes(p.id);
          return `<button type="button" class="platform-pill ${isPub ? "published" : ""}" data-platform="${p.id}">${isPub ? "✓ " : ""}${p.label}</button>`;
        }).join("")}
      </div>
      ${
        clip.error_message
          ? `<div class="error-banner">${escapeHtml(clip.error_message)}</div>`
          : ""
      }
    </div>

    <div class="preview-box">
      ${
        clip.output_exists
          ? `<video controls playsinline preload="metadata" src="/api/clips/${encodeURIComponent(clip.id)}/video?t=${Date.now()}"></video>`
          : `<div class="empty-muted">Belum di-render.<br/>Edit subtitle lalu klik <b>Render Klip</b>.</div>`
      }
    </div>
  `;

  const wordsContainer = card.querySelector(".words-editor");
  const renderWordChips = () => {
    if (!words.length) {
      wordsContainer.innerHTML = `<div class="empty-muted">Tidak ada kata subtitle pada rentang waktu ini.</div>`;
      return;
    }
    wordsContainer.innerHTML = "";
    words.forEach((token, idx) => {
      const chip = document.createElement("div");
      chip.className = "word-chip";

      const timeSpan = document.createElement("span");
      timeSpan.className = "word-time";
      timeSpan.textContent = `${Number(token.start).toFixed(1)}s`;

      const wordInput = document.createElement("input");
      wordInput.type = "text";
      wordInput.className = "word-input";
      wordInput.value = token.word;
      wordInput.style.width = `${Math.max(46, Math.min(160, token.word.length * 8 + 16))}px`;
      wordInput.addEventListener("input", (e) => {
        words[idx].word = e.target.value;
      });

      const delBtn = document.createElement("button");
      delBtn.type = "button";
      delBtn.className = "word-del";
      delBtn.title = "Hapus kata";
      delBtn.textContent = "×";
      delBtn.addEventListener("click", () => {
        words.splice(idx, 1);
        renderWordChips();
      });

      chip.append(timeSpan, wordInput, delBtn);
      wordsContainer.appendChild(chip);
    });
  };
  renderWordChips();

  card.querySelector(".replace-btn").addEventListener("click", () => {
    const findTxt = card.querySelector(".find-input").value.trim();
    const replaceTxt = card.querySelector(".replace-input").value.trim();
    if (!findTxt) return;

    const normalizeToken = (s) => String(s).replace(/^[^a-zA-Z0-9]+|[^a-zA-Z0-9]+$/g, "").toLowerCase();
    const findParts = findTxt.split(/\s+/).map(normalizeToken).filter(Boolean);
    const replaceParts = replaceTxt ? replaceTxt.split(/\s+/) : [];
    if (!findParts.length) return;
    let replacedCount = 0;

    if (findParts.length === 1) {
      const targetNorm = findParts[0];
      const rawEsc = findTxt.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      words.forEach((w) => {
        if (w.word.toLowerCase().includes(findTxt.toLowerCase())) {
          w.word = w.word.replace(new RegExp(rawEsc, "gi"), replaceTxt);
          replacedCount += 1;
        } else if (normalizeToken(w.word) === targetNorm) {
          const lead = (w.word.match(/^[^a-zA-Z0-9]+/) || [""])[0];
          const trail = (w.word.match(/[^a-zA-Z0-9]+$/) || [""])[0];
          w.word = `${lead}${replaceTxt}${trail}`;
          replacedCount += 1;
        }
      });
    } else {
      for (let i = 0; i <= words.length - findParts.length; i++) {
        const sliceNorm = words.slice(i, i + findParts.length).map((w) => normalizeToken(w.word));
        const matches = sliceNorm.every((tok, idx) => tok === findParts[idx]);
        if (matches) {
          const spanStart = words[i].start;
          const spanEnd = words[i + findParts.length - 1].end;
          const trailPunct = (words[i + findParts.length - 1].word.match(/[^a-zA-Z0-9]+$/) || [""])[0];
          const step = (spanEnd - spanStart) / Math.max(1, replaceParts.length);
          const newTokens = replaceParts.map((part, k) => {
            const isLast = k === replaceParts.length - 1;
            const text = isLast && trailPunct && /[a-zA-Z0-9]$/.test(part) ? `${part}${trailPunct}` : part;
            return {
              word: text,
              start: Number((spanStart + k * step).toFixed(3)),
              end: Number((spanStart + (k + 1) * step).toFixed(3)),
            };
          });
          words.splice(i, findParts.length, ...newTokens);
          replacedCount += 1;
          i += Math.max(0, newTokens.length - 1);
        }
      }
    }
    renderWordChips();
    showToast(replacedCount ? `Diganti ${replacedCount} kali. Klik Simpan untuk menyimpan.` : "Teks tidak ditemukan.");
  });

  const savePayload = async (reslice = false) => {
    const body = {
      title: card.querySelector(".clip-title-input").value.trim(),
      start_time: Number(card.querySelector(".clip-start").value),
      end_time: Number(card.querySelector(".clip-end").value),
      layout_mode: card.querySelector(".clip-layout").value,
      hook_text: card.querySelector(".clip-hook-input").value.trim(),
      words: reslice ? null : words.filter((w) => w.word.trim()),
      reslice_from_transcript: reslice,
    };
    await apiFetch(`/api/clips/${encodeURIComponent(clip.id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    showToast(reslice ? "Transkrip diambil ulang sesuai detik!" : "Perubahan klip disimpan!");
    await selectJob(job.id);
  };

  card.querySelector(".save-clip-btn").addEventListener("click", () => {
    savePayload(false).catch((err) => showToast(`Gagal simpan: ${err.message}`));
  });

  card.querySelector(".reslice-btn").addEventListener("click", () => {
    savePayload(true).catch((err) => showToast(`Gagal ambil transkrip: ${err.message}`));
  });

  card.querySelector(".render-clip-btn").addEventListener("click", async () => {
    try {
      await savePayload(false);
      await apiFetch(`/api/clips/${encodeURIComponent(clip.id)}/render`, { method: "POST" });
      showToast("Klip masuk antrean render NVENC!");
      await refreshAll();
    } catch (err) {
      showToast(`Gagal render: ${err.message}`);
    }
  });

  card.querySelectorAll(".platform-pill").forEach((pillBtn) => {
    pillBtn.addEventListener("click", async () => {
      const platform = pillBtn.dataset.platform;
      const current = new Set(clip.published_platforms || []);
      if (current.has(platform)) {
        current.delete(platform);
      } else {
        current.add(platform);
      }
      const nextPlatforms = Array.from(current);
      try {
        const updated = await apiFetch(`/api/clips/${encodeURIComponent(clip.id)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ published_platforms: nextPlatforms }),
        });
        clip.published_platforms = updated.published_platforms || nextPlatforms;
        card.querySelectorAll(".platform-pill").forEach((btn) => {
          const pid = btn.dataset.platform;
          const pInfo = PLATFORMS.find((x) => x.id === pid);
          const active = clip.published_platforms.includes(pid);
          btn.classList.toggle("published", active);
          btn.textContent = `${active ? "✓ " : ""}${pInfo ? pInfo.label : pid}`;
        });
      } catch (err) {
        showToast(`Gagal update checklist: ${err.message}`);
      }
    });
  });

  return card;
}

function renderJobDetail() {
  const job = state.selectedJob;
  const emptyEl = document.getElementById("empty-state");
  const detailEl = document.getElementById("job-detail");
  if (!job) {
    emptyEl.classList.remove("hidden");
    detailEl.classList.add("hidden");
    return;
  }

  emptyEl.classList.add("hidden");
  detailEl.classList.remove("hidden");

  const statusBadge = document.getElementById("detail-status-badge");
  statusBadge.className = `badge badge-${job.status}`;
  statusBadge.textContent = job.status;

  document.getElementById("detail-owner-tag").textContent = `👤 ${job.owner || "Tim"}`;
  document.getElementById("detail-source-tag").textContent = job.source_type === "url" ? "URL" : "LOKAL";
  document.getElementById("detail-lang-tag").textContent = `${job.language.toUpperCase()} • ${job.whisper_model}`;
  document.getElementById("detail-title").textContent = job.title || job.id;
  document.getElementById("detail-source-value").textContent = job.source_value;

  const progress = job.progress || 0;
  document.getElementById("detail-progress-fill").style.width = `${progress}%`;
  document.getElementById("detail-progress-text").textContent = `${progress}%`;

  const errEl = document.getElementById("detail-error");
  if (job.error_message) {
    errEl.textContent = job.error_message;
    errEl.classList.remove("hidden");
  } else {
    errEl.classList.add("hidden");
  }

  const renderAllBtn = document.getElementById("render-all-btn");
  const deleteRawBtn = document.getElementById("delete-raw-btn");
  renderAllBtn.disabled = !job.raw_exists || !(job.clips && job.clips.length);
  deleteRawBtn.disabled = !job.raw_exists;
  renderAllBtn.title = job.raw_exists ? "" : "File video mentah sudah dihapus";
  deleteRawBtn.title = job.raw_exists ? "" : "File video mentah sudah dihapus";
  document.getElementById("raw-missing-notice").classList.toggle("hidden", !!job.raw_exists);

  const zipBtn = document.getElementById("download-zip-btn");
  const hasRendered = (job.clips || []).some((c) => c.output_exists);
  zipBtn.classList.toggle("hidden", !hasRendered);
  zipBtn.href = `/api/jobs/${encodeURIComponent(job.id)}/download-zip`;

  const img = document.getElementById("preview-frame-img");
  const stage = document.getElementById("frame-stage");
  const noFrame = document.getElementById("no-frame-msg");
  if (job.has_preview) {
    stage.classList.remove("hidden");
    noFrame.classList.add("hidden");
    const targetSrc = `/api/jobs/${encodeURIComponent(job.id)}/frame`;
    if (!img.src.endsWith(targetSrc)) {
      img.onload = () => {
        state.frameW = img.naturalWidth || 1920;
        state.frameH = img.naturalHeight || 1080;
        updateSplitOverlays();
      };
      img.src = targetSrc;
    }
  } else {
    stage.classList.add("hidden");
    noFrame.classList.remove("hidden");
  }

  loadSplitInputs(job.split_config || {});

  const clipsContainer = document.getElementById("clips-container");
  const clips = job.clips || [];
  document.getElementById("clips-count-label").textContent = `${clips.length} klip`;
  clipsContainer.innerHTML = "";
  if (!clips.length) {
    clipsContainer.innerHTML = `<div class="panel-card empty-muted">Kandidat klip belum tersedia (masih diproses atau belum selesai).</div>`;
  } else {
    clips.forEach((clip) => clipsContainer.appendChild(createClipCard(clip, job)));
  }
}

function schedulePollingIfNeeded() {
  clearTimeout(state.pollTimer);
  const anyJobBusy = state.jobs.some((j) => ACTIVE_JOB_STATUSES.has(j.status));
  const anyClipBusy =
    state.selectedJob &&
    (state.selectedJob.clips || []).some((c) => ACTIVE_CLIP_STATUSES.has(c.status));

  if (anyJobBusy || anyClipBusy) {
    state.pollTimer = setTimeout(refreshAll, 3000);
  }
}

async function selectJob(jobId) {
  state.selectedJobId = jobId;
  renderJobsList();
  try {
    state.selectedJob = await apiFetch(`/api/jobs/${encodeURIComponent(jobId)}`);
    renderJobDetail();
    schedulePollingIfNeeded();
  } catch (err) {
    showToast(`Gagal memuat detail job: ${err.message}`);
  }
}

async function refreshStorage() {
  try {
    const stats = await apiFetch("/api/storage");
    document.getElementById("stat-raw-mb").textContent = `${stats.raw_mb} MB`;
    document.getElementById("stat-clips-mb").textContent = `${stats.clips_mb} MB`;
    document.getElementById("stat-free-gb").textContent = `${stats.free_disk_gb} GB`;
  } catch (_) {}
}

async function refreshHardwareBadge() {
  try {
    const info = await apiFetch("/api/health");
    state.videoEncoder = info.video_encoder || "libx264";

    const badge = document.getElementById("hw-badge");
    const modeBanner = document.getElementById("hw-mode-badge");
    modeBanner.classList.remove("hw-gpu-active", "hw-gpu-partial", "hw-cpu-only");

    if (info.mode === "GPU (CUDA + NVENC)") {
      badge.textContent = "CUDA + NVENC Studio";
      modeBanner.textContent = "GPU AKTIF (CUDA + NVENC)";
      modeBanner.classList.add("hw-gpu-active");
    } else if (info.mode === "GPU Partial") {
      badge.textContent = `Hybrid (${info.whisper_device.toUpperCase()} + ${info.video_encoder})`;
      modeBanner.textContent = "HYBRID GPU / CPU";
      modeBanner.classList.add("hw-gpu-partial");
    } else {
      badge.textContent = "CPU-Only Mode";
      modeBanner.textContent = "CPU ONLY MODE (Fallback)";
      modeBanner.classList.add("hw-cpu-only");
    }

    const vramStr = info.gpu_vram_mb ? ` (${(info.gpu_vram_mb / 1024).toFixed(1)} GB VRAM)` : "";
    document.getElementById("hw-gpu").textContent = `${info.gpu_name || "-"}${vramStr}`;
    const shortCpu = (info.cpu_name || "CPU").replace(/\(R\)|\(TM\)|CPU @.*$/gi, "").trim();
    document.getElementById("hw-cpu-ram").textContent = `${shortCpu} • ${info.cpu_cores || "?"} Core • ${info.ram_total_gb || "?"} GB RAM`;
    document.getElementById("hw-whisper").textContent = `${(info.whisper_device || "cpu").toUpperCase()} (${info.whisper_compute_type || "int8"})`;
    document.getElementById("hw-encoder").textContent = info.video_encoder === "h264_nvenc" ? "NVIDIA h264_nvenc" : "CPU libx264";
    document.getElementById("hw-detector").textContent = info.llm_enabled
      ? "Hybrid AI (Gemini + 4-Sinyal)"
      : "4-Sinyal Lokal (Audio Spike + Reaksi)";
  } catch (_) {}
}

async function refreshPresets() {
  try {
    state.presets = await apiFetch("/api/presets");
    const select = document.getElementById("preset-select");
    const current = select.value;
    select.innerHTML = `<option value="">-- Pilih Preset Streamer --</option>` + state.presets
      .map((p) => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.name)}</option>`)
      .join("");
    if (state.presets.some((p) => p.id === current)) {
      select.value = current;
    }
  } catch (_) {}
}

async function refreshMetrics() {
  try {
    const m = await apiFetch("/api/metrics");
    document.getElementById("metric-clips-summary").textContent = `${m.rendered_clips || 0} render • ${m.published_clips || 0} upload`;
    const pc = m.platform_counts || {};
    document.getElementById("metric-platforms").textContent = `TT:${pc.tiktok || 0} • IG:${pc.ig || 0} • YT:${pc.yt || 0} • FB:${pc.fb || 0}`;
    const members = (m.member_stats || [])
      .map((item) => `${item.owner} (${item.clips_rendered})`)
      .join(", ");
    document.getElementById("metric-members").textContent = members || "-";
    document.getElementById("metric-ytdlp").textContent = m.ytdlp_version || "-";
  } catch (_) {}
}

async function refreshAll() {
  try {
    state.jobs = await apiFetch("/api/jobs");
    populateOwnerFilter();
    if (!state.selectedJobId && state.jobs.length > 0) {
      state.selectedJobId = state.jobs[0].id;
    }
    renderJobsList();
    if (state.selectedJobId) {
      state.selectedJob = await apiFetch(`/api/jobs/${encodeURIComponent(state.selectedJobId)}`);
      renderJobDetail();
    } else {
      renderJobDetail();
    }
    refreshStorage();
    refreshMetrics();
    schedulePollingIfNeeded();
  } catch (err) {
    showToast(`Gagal memuat jobs: ${err.message}`);
  }
}

document.getElementById("tab-url").addEventListener("click", () => setInputMode("url"));
document.getElementById("tab-upload").addEventListener("click", () => setInputMode("upload"));
document.getElementById("refresh-jobs-btn").addEventListener("click", refreshAll);
document.getElementById("filter-owner").addEventListener("change", (e) => {
  state.ownerFilter = e.target.value;
  renderJobsList();
});

document.getElementById("new-job-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = document.getElementById("submit-job-btn");
  btn.disabled = true;
  try {
    const owner = getActiveMemberName();
    const language = document.getElementById("job-language").value;
    const whisper_model = document.getElementById("job-model").value;
    const layout_mode = document.getElementById("job-layout").value;
    const target_duration = Number(document.getElementById("job-duration").value) || 35;
    const num_clips = Number(document.getElementById("job-num-clips").value) || 3;

    let created;
    if (state.inputMode === "url") {
      const url = document.getElementById("job-url").value.trim();
      if (!url) throw new Error("Masukkan URL video terlebih dahulu.");
      const payload = { owner, url, language, whisper_model, layout_mode, target_duration, num_clips };
      try {
        created = await apiFetch("/api/jobs/url", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      } catch (err) {
        if (err.status === 409 && confirm(`${err.message}\n\nTetap proses ulang link ini?`)) {
          created = await apiFetch("/api/jobs/url", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ...payload, force_duplicate: true }),
          });
        } else {
          throw err;
        }
      }
      document.getElementById("job-url").value = "";
    } else {
      const fileInput = document.getElementById("job-file");
      if (!fileInput.files || !fileInput.files.length) {
        throw new Error("Pilih file video lokal terlebih dahulu.");
      }
      const formData = new FormData();
      formData.append("file", fileInput.files[0]);
      formData.append("owner", owner);
      formData.append("language", language);
      formData.append("whisper_model", whisper_model);
      formData.append("layout_mode", layout_mode);
      formData.append("target_duration", String(target_duration));
      formData.append("num_clips", String(num_clips));
      created = await apiFetch("/api/jobs/upload", { method: "POST", body: formData });
      fileInput.value = "";
    }

    showToast("Job baru ditambahkan ke antrean!");
    state.selectedJobId = created.id;
    await refreshAll();
  } catch (err) {
    showToast(`Gagal membuat job: ${err.message}`);
  } finally {
    btn.disabled = false;
  }
});

["cam_x", "cam_y", "cam_w", "cam_h", "game_x", "game_y", "game_w", "game_h"].forEach((id) => {
  document.getElementById(id).addEventListener("input", updateSplitOverlays);
});

document.querySelectorAll(".preset-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    const fw = state.frameW || 1920;
    const fh = state.frameH || 1080;
    const presets = {
      "top-left": { cam_x: 40, cam_y: 40, cam_w: 480, cam_h: 284, game_x: 504, game_y: 0, game_w: 911, game_h: fh },
      "top-right": { cam_x: Math.max(0, fw - 520), cam_y: 40, cam_w: 480, cam_h: 284, game_x: 504, game_y: 0, game_w: 911, game_h: fh },
      "bottom-left": { cam_x: 40, cam_y: Math.max(0, fh - 324), cam_w: 480, cam_h: 284, game_x: 504, game_y: 0, game_w: 911, game_h: fh },
    };
    loadSplitInputs(presets[btn.dataset.preset]);
  });
});

document.getElementById("save-split-btn").addEventListener("click", async () => {
  if (!state.selectedJobId) return;
  const split_config = {};
  ["cam_x", "cam_y", "cam_w", "cam_h", "game_x", "game_y", "game_w", "game_h"].forEach((k) => {
    split_config[k] = Number(document.getElementById(k).value) || 0;
  });
  try {
    await apiFetch(`/api/jobs/${encodeURIComponent(state.selectedJobId)}/layout`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ split_config }),
    });
    showToast("Koordinat Split-Screen disimpan!");
  } catch (err) {
    showToast(`Gagal simpan layout: ${err.message}`);
  }
});

document.getElementById("toggle-split-btn").addEventListener("click", () => {
  state.splitEditorOpen = !state.splitEditorOpen;
  document.getElementById("split-editor-body").classList.toggle("hidden", !state.splitEditorOpen);
  document.getElementById("toggle-split-btn").textContent = state.splitEditorOpen ? "Sembunyikan" : "Tampilkan";
});

document.getElementById("reanalyze-btn").addEventListener("click", async () => {
  if (!state.selectedJobId) return;
  const btn = document.getElementById("reanalyze-btn");
  btn.disabled = true;
  try {
    await apiFetch(`/api/jobs/${encodeURIComponent(state.selectedJobId)}/reanalyze`, { method: "POST" });
    showToast("Momen highlight dianalisis ulang dengan detektor terbaru!");
    await refreshAll();
  } catch (err) {
    showToast(`Gagal analisis ulang: ${err.message}`);
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("render-all-btn").addEventListener("click", async () => {
  if (!state.selectedJobId) return;
  try {
    await apiFetch(`/api/jobs/${encodeURIComponent(state.selectedJobId)}/render-all`, { method: "POST" });
    showToast("Semua klip masuk antrean render NVENC!");
    await refreshAll();
  } catch (err) {
    showToast(`Gagal render semua: ${err.message}`);
  }
});

document.getElementById("delete-raw-btn").addEventListener("click", async () => {
  if (!state.selectedJobId) return;
  if (!confirm("Hapus file video mentah (raw.mp4 & audio_16k.wav) untuk menghemat SSD? Klip yang sudah dirender tetap aman.")) {
    return;
  }
  try {
    const res = await apiFetch(`/api/jobs/${encodeURIComponent(state.selectedJobId)}/raw`, { method: "DELETE" });
    showToast(`File mentah dihapus (${res.freed_mb} MB dibebaskan).`);
    await refreshAll();
  } catch (err) {
    showToast(`Gagal menghapus file mentah: ${err.message}`);
  }
});

document.getElementById("apply-preset-btn").addEventListener("click", () => {
  const id = document.getElementById("preset-select").value;
  const preset = state.presets.find((p) => p.id === id);
  if (!preset) {
    showToast("Pilih preset streamer terlebih dahulu.");
    return;
  }
  loadSplitInputs(preset.split_config || {});
  document.getElementById("job-layout").value = preset.layout_mode;
  document.getElementById("job-language").value = preset.language;
  document.getElementById("job-duration").value = preset.target_duration;
  showToast(`Preset "${preset.name}" diterapkan!`);
});

document.getElementById("save-preset-btn").addEventListener("click", async () => {
  const name = document.getElementById("preset-name-input").value.trim();
  if (!name) {
    showToast("Masukkan nama preset streamer dulu.");
    return;
  }
  const split_config = {};
  ["cam_x", "cam_y", "cam_w", "cam_h", "game_x", "game_y", "game_w", "game_h"].forEach((k) => {
    split_config[k] = Number(document.getElementById(k).value) || 0;
  });
  try {
    const created = await apiFetch("/api/presets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        layout_mode: document.getElementById("job-layout").value,
        language: document.getElementById("job-language").value,
        target_duration: Number(document.getElementById("job-duration").value) || 35,
        split_config,
      }),
    });
    document.getElementById("preset-name-input").value = "";
    await refreshPresets();
    document.getElementById("preset-select").value = created.id;
    showToast(`Preset "${created.name}" disimpan!`);
  } catch (err) {
    showToast(`Gagal simpan preset: ${err.message}`);
  }
});

document.getElementById("delete-preset-btn").addEventListener("click", async () => {
  const id = document.getElementById("preset-select").value;
  if (!id) return;
  try {
    await apiFetch(`/api/presets/${encodeURIComponent(id)}`, { method: "DELETE" });
    await refreshPresets();
    showToast("Preset dihapus.");
  } catch (err) {
    showToast(`Gagal hapus preset: ${err.message}`);
  }
});

document.getElementById("cleanup-storage-btn").addEventListener("click", async () => {
  if (!confirm("Hapus raw video & audio untuk semua job yang sudah selesai? Klip MP4 hasil render tetap aman.")) {
    return;
  }
  try {
    const res = await apiFetch("/api/storage/cleanup", { method: "POST" });
    showToast(`${res.jobs_cleaned} job dibersihkan (${res.freed_mb} MB dibebaskan).`);
    await refreshAll();
  } catch (err) {
    showToast(`Gagal membersihkan storage: ${err.message}`);
  }
});

document.getElementById("update-ytdlp-btn").addEventListener("click", async () => {
  const btn = document.getElementById("update-ytdlp-btn");
  btn.disabled = true;
  btn.textContent = "Updating...";
  try {
    const res = await apiFetch("/api/maintenance/update-ytdlp", { method: "POST" });
    showToast(`yt-dlp diperbarui ke versi ${res.ytdlp_version}`);
    await refreshMetrics();
  } catch (err) {
    showToast(`Gagal update yt-dlp: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Update yt-dlp";
  }
});

refreshPresets();
refreshHardwareBadge().finally(() => refreshAll());
