const PREFERENCES_STORAGE_KEY = "paper-preferences";
const PREFERENCES_STORAGE_VERSION = 2;
const FILTER_STORAGE_KEY = "paper-filters";
const FILTER_STORAGE_VERSION = 1;
const INITIAL_RENDER_COUNT = 40;
const RENDER_BATCH_SIZE = 20;

const savedFilters = loadFilters();

const state = {
  raw: [],
  filtered: [],
  visibleCount: INITIAL_RENDER_COUNT,
  selectedConferences: new Set(savedFilters.selectedConferences),
  selectedYears: new Set(savedFilters.selectedYears),
  availableConferences: [],
  availableYears: [],
  activeView: "browse",
  preferences: loadPreferences(),
};

const conferenceFiltersEl = document.getElementById("conferenceFilters");
const yearFiltersEl = document.getElementById("yearFilters");
const keywordEl = document.getElementById("keyword");
const hasAbstractEl = document.getElementById("hasAbstract");
const resultsEl = document.getElementById("results");
const loadingEl = document.getElementById("loading");
const filtersPanelEl = document.getElementById("filtersPanel");
const viewTabs = [...document.querySelectorAll(".view-tab")];

keywordEl.addEventListener("input", render);
hasAbstractEl.addEventListener("change", render);
resultsEl.addEventListener("click", handleAbstractToggle);
resultsEl.addEventListener("click", handlePreferenceClick);
viewTabs.forEach((tab) => tab.addEventListener("click", handleViewChange));
conferenceFiltersEl.addEventListener("click", handleConferenceFilterClick);
yearFiltersEl.addEventListener("click", handleYearFilterClick);

function loadPreferences() {
  try {
    const raw = JSON.parse(localStorage.getItem(PREFERENCES_STORAGE_KEY) || "null");
    if (!raw || raw.version !== PREFERENCES_STORAGE_VERSION) {
      return createEmptyPreferences();
    }
    return {
      version: PREFERENCES_STORAGE_VERSION,
      liked: raw.liked && typeof raw.liked === "object" ? raw.liked : {},
      viewed: raw.viewed && typeof raw.viewed === "object" ? raw.viewed : {},
      disliked: raw.disliked && typeof raw.disliked === "object" && !Array.isArray(raw.disliked) ? raw.disliked : {},
    };
  } catch {
    return createEmptyPreferences();
  }
}

function savePreferences() {
  localStorage.setItem(PREFERENCES_STORAGE_KEY, JSON.stringify(state.preferences));
}

function createEmptyPreferences() {
  return {
    version: PREFERENCES_STORAGE_VERSION,
    liked: {},
    viewed: {},
    disliked: {},
  };
}

function loadFilters() {
  try {
    const raw = JSON.parse(localStorage.getItem(FILTER_STORAGE_KEY) || "null");
    if (!raw || raw.version !== FILTER_STORAGE_VERSION) {
      return createEmptyFilters();
    }
    return {
      version: FILTER_STORAGE_VERSION,
      selectedConferences: Array.isArray(raw.selectedConferences) ? raw.selectedConferences : [],
      selectedYears: Array.isArray(raw.selectedYears) ? raw.selectedYears : [],
    };
  } catch {
    return createEmptyFilters();
  }
}

function saveFilters() {
  localStorage.setItem(
    FILTER_STORAGE_KEY,
    JSON.stringify({
      version: FILTER_STORAGE_VERSION,
      selectedConferences: [...state.selectedConferences],
      selectedYears: [...state.selectedYears],
    }),
  );
}

function createEmptyFilters() {
  return {
    version: FILTER_STORAGE_VERSION,
    selectedConferences: [],
    selectedYears: [],
  };
}

function getPaperId(paper) {
  return [paper.conference, paper.year, paper.title].join("::");
}

function getPaperDownloadUrl(paper) {
  return paper.source_url || paper.doi_url || paper.dblp_url || "";
}

function getPaperConferenceKey(paper) {
  if (paper.conference) {
    return String(paper.conference);
  }
  if (!paper.conference_label) {
    return "";
  }
  const matched = state.availableConferences.find((conference) => conference.label === paper.conference_label);
  return matched ? matched.key : "";
}

function getPaperYearKey(paper) {
  if (paper.year === undefined || paper.year === null || paper.year === "") {
    return "";
  }
  return String(paper.year);
}

function isExcludedPaperType(paper) {
  return String(paper.type || "").trim().toLowerCase() === "editorship";
}

function isLiked(paper) {
  return Boolean(state.preferences.liked[getPaperId(paper)]);
}

function isDisliked(paper) {
  const paperId = getPaperId(paper);
  return Boolean(state.preferences.disliked[paperId]);
}

function getFavoritePapers() {
  return Object.values(state.preferences.liked).filter(
    (paper) => !isExcludedPaperType(paper) && !isDisliked(paper) && !isViewed(paper),
  );
}

function isViewed(paper) {
  return Boolean(state.preferences.viewed[getPaperId(paper)]);
}

function getViewedPapers() {
  return Object.values(state.preferences.viewed).filter(
    (paper) => !isExcludedPaperType(paper) && !isDisliked(paper),
  );
}

function getDislikedPapers() {
  return Object.values(state.preferences.disliked).filter(
    (paper) => Boolean(paper) && !isExcludedPaperType(paper),
  );
}

function checkIcon(filled) {
  return `
    <svg viewBox="0 0 24 24" fill="${filled ? "currentColor" : "none"}" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z"></path>
      <path d="m9 12 2 2 4-4"></path>
    </svg>
  `;
}

function heartIcon(filled) {
  return `
    <svg viewBox="0 0 24 24" fill="${filled ? "currentColor" : "none"}" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="m12 21-1.35-1.23C5.4 15.02 2 11.93 2 8.12 2 5.04 4.42 2.62 7.5 2.62c1.74 0 3.41.81 4.5 2.09 1.09-1.28 2.76-2.09 4.5-2.09 3.08 0 5.5 2.42 5.5 5.5 0 3.81-3.4 6.9-8.65 11.65Z"></path>
    </svg>
  `;
}

function thumbsDownIcon(filled) {
  return `
    <svg viewBox="0 0 24 24" fill="${filled ? "currentColor" : "none"}" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M17 14V2"></path>
      <path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.95-2.45l1.46-6A2 2 0 0 1 5.62 4H17a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.95 1.55l-.63 2.49a1 1 0 0 1-1.94-.24V19a2 2 0 0 0-.72-1.54A2 2 0 0 1 9 18.12Z"></path>
    </svg>
  `;
}

function downloadIcon() {
  return `
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M12 3v12"></path>
      <path d="m7 10 5 5 5-5"></path>
      <path d="M5 21h14"></path>
    </svg>
  `;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function highlightHtml(value, keyword) {
  return escapeHtml(value || "");
}

function renderAbstractHtml(value, keyword, lang) {
  const text = String(value || "").trim();
  if (!text) {
    return highlightHtml(lang === "zh" ? "暂无中文摘要" : "暂无英文摘要", keyword);
  }
  const paragraphs = text
    .split(/\n\s*\n/)
    .map((part) => part.trim())
    .filter(Boolean);
  return paragraphs
    .map((paragraph) => {
      const classes = ["abstract-paragraph"];
      if (lang === "zh") {
        classes.push("abstract-paragraph-zh");
      }
      return `<span class="${classes.join(" ")}">${highlightHtml(paragraph, keyword)}</span>`;
    })
    .join("");
}

function getMatchPriority(paper, keyword) {
  if (!keyword) {
    return 0;
  }
  const lowered = keyword.toLowerCase();
  if ((paper.title || "").toLowerCase().includes(lowered)) {
    return 3;
  }
  if ((paper.abstract || "").toLowerCase().includes(lowered)) {
    return 2;
  }
  const otherText = [
    ...(paper.authors || []),
    paper.abstract_zh || "",
  ].join(" ").toLowerCase();
  if (otherText.includes(lowered)) {
    return 1;
  }
  return 0;
}

async function loadData() {
  loadingEl.textContent = "正在加载数据...";
  resultsEl.innerHTML = "";
  try {
    const indexRes = await fetch("./papers-data.json");
    const indexPayload = await indexRes.json();
    if (!indexRes.ok) {
      throw new Error(indexPayload.detail || indexPayload.error || "请求失败");
    }
    const dataFiles = Array.isArray(indexPayload.data_files) ? indexPayload.data_files : [];
    const shardPayloads = await Promise.all(
      dataFiles.map(async ({ file }) => {
        const res = await fetch(`./${file}`);
        const payload = await res.json();
        if (!res.ok) {
          throw new Error(payload.detail || payload.error || `加载分片失败: ${file}`);
        }
        return payload;
      }),
    );
    state.raw = shardPayloads.flatMap((payload) => payload.papers || []);
    state.availableConferences = indexPayload.available_conferences || [];
    state.availableYears = indexPayload.available_years || [];
    const availableConferenceKeys = new Set(state.availableConferences.map((conference) => conference.key));
    const availableYearKeys = new Set(state.availableYears.map((year) => String(year)));
    state.selectedConferences = new Set(
      [...state.selectedConferences].filter((conference) => availableConferenceKeys.has(conference)),
    );
    state.selectedYears = new Set(
      [...state.selectedYears].filter((year) => availableYearKeys.has(year)),
    );
    saveFilters();
    renderConferenceFilters();
    renderYearFilters();
    render();
  } catch (error) {
    loadingEl.textContent = `加载失败：${error.message}。请先执行 python3 app.py build-cache 生成静态数据。`;
  }
}

function renderConferenceFilters() {
  conferenceFiltersEl.innerHTML = state.availableConferences.map((conference) => `
    <button
      class="filter-chip ${state.selectedConferences.has(conference.key) ? "is-active" : ""}"
      data-conference="${escapeHtml(conference.key)}"
      type="button"
    >${escapeHtml(conference.label)}</button>
  `).join("");
}

function renderYearFilters() {
  yearFiltersEl.innerHTML = state.availableYears.map((year) => `
    <button
      class="filter-chip ${state.selectedYears.has(String(year)) ? "is-active" : ""}"
      data-year="${escapeHtml(String(year))}"
      type="button"
    >${escapeHtml(String(year))}</button>
  `).join("");
}

function getBasePapersForActiveView() {
  if (state.activeView === "favorites") {
    return getFavoritePapers();
  }
  if (state.activeView === "viewed") {
    return getViewedPapers();
  }
  if (state.activeView === "disliked") {
    return getDislikedPapers();
  }
  return state.raw.filter(
    (paper) => !isExcludedPaperType(paper) && !isDisliked(paper) && !isLiked(paper) && !isViewed(paper),
  );
}

function render() {
  state.visibleCount = INITIAL_RENDER_COUNT;
  syncViewTabs();
  filtersPanelEl.hidden = false;
  const keyword = keywordEl.value.trim().toLowerCase();
  const requireAbstract = hasAbstractEl.checked;
  const papers = getBasePapersForActiveView()
    .filter((paper) => {
      const conferenceKey = getPaperConferenceKey(paper);
      const yearKey = getPaperYearKey(paper);
      if (state.selectedConferences.size && !state.selectedConferences.has(conferenceKey)) {
        return false;
      }
      if (state.selectedYears.size && !state.selectedYears.has(yearKey)) {
        return false;
      }
      if (requireAbstract && !paper.abstract) {
        return false;
      }
      if (!keyword) {
        return true;
      }
      const haystack = [
        paper.title,
        paper.abstract || "",
        ...(paper.authors || []),
        paper.abstract_zh || "",
      ].join(" ").toLowerCase();
      return haystack.includes(keyword);
    })
    .sort((a, b) => {
      const matchDelta = getMatchPriority(b, keyword) - getMatchPriority(a, keyword);
      if (matchDelta) {
        return matchDelta;
      }
      return Number(b.year) - Number(a.year) || a.title.localeCompare(b.title);
    });

  state.filtered = papers;
  updateStatus();
  renderResults();
}

function updateStatus() {
  const renderedCount = Math.min(state.visibleCount, state.filtered.length);
  const conferenceHint = state.selectedConferences.size ? `已选 ${state.selectedConferences.size} 个会议` : "全部会议";
  const yearHint = state.selectedYears.size ? `已选 ${state.selectedYears.size} 个年份` : "全部年份";
  if (state.activeView === "favorites") {
    loadingEl.textContent = `我的喜欢中共有 ${state.filtered.length} 篇论文，当前渲染 ${renderedCount} 篇，范围：${conferenceHint}，${yearHint}。`;
    return;
  }
  if (state.activeView === "viewed") {
    loadingEl.textContent = `已看完列表中共有 ${state.filtered.length} 篇论文，当前渲染 ${renderedCount} 篇，范围：${conferenceHint}，${yearHint}。`;
    return;
  }
  if (state.activeView === "disliked") {
    loadingEl.textContent = `不感兴趣列表中共有 ${state.filtered.length} 篇论文，当前渲染 ${renderedCount} 篇，范围：${conferenceHint}，${yearHint}。`;
    return;
  }
  loadingEl.textContent = `当前匹配 ${state.filtered.length} 篇论文，已渲染 ${renderedCount} 篇，范围：${conferenceHint}，${yearHint}。`;
}

function renderResults() {
  if (!state.filtered.length) {
    resultsEl.innerHTML = `<div class="empty">${
      state.activeView === "favorites"
        ? "你还没有标记喜欢的论文。"
        : state.activeView === "disliked"
          ? "你还没有标记不感兴趣的论文。"
        : state.activeView === "viewed"
          ? "你还没有标记已看完的论文。"
          : "当前筛选条件下没有结果。"
    }</div>`;
    return;
  }

  const visiblePapers = state.filtered.slice(0, state.visibleCount);
  const cardsHtml = visiblePapers.map((paper) => {
    const paperId = getPaperId(paper);
    const tags = [
      `<span class="tag">${paper.conference_label}</span>`,
      `<span class="tag">${paper.year}</span>`,
    ];
    if (paper.abstract_source) {
      tags.push(`<span class="tag">摘要: ${escapeHtml(paper.abstract_source)}</span>`);
    }

    const links = [];
    if (paper.source_url) {
      links.push(`<a href="${paper.source_url}" target="_blank" rel="noreferrer">原始页面</a>`);
    }

    return `
      <article class="paper" data-paper-id="${escapeHtml(paperId)}">
        <div class="meta">
          <div class="meta-tags">${tags.join("")}</div>
          <div class="meta-actions">
            <button class="meta-icon-button" data-action="download" title="下载论文">${downloadIcon()}</button>
            <button class="meta-icon-button ${isViewed(paper) ? "is-active" : ""}" data-action="viewed" title="已看完">${checkIcon(isViewed(paper))}</button>
            <button class="meta-icon-button ${isLiked(paper) ? "is-active" : ""}" data-action="like" title="喜欢">${heartIcon(isLiked(paper))}</button>
            <button class="meta-icon-button ${isDisliked(paper) ? "is-active" : ""}" data-action="dislike" title="不喜欢">${thumbsDownIcon(isDisliked(paper))}</button>
          </div>
        </div>
        <h2>${highlightHtml(paper.title, keywordEl.value.trim())}</h2>
        <p class="authors">${highlightHtml((paper.authors || []).join(", "), keywordEl.value.trim())}</p>
        <div class="abstract-panel">
          <div class="abstract-tabs">
            <button class="abstract-tab ${paper.abstract_zh ? "is-active" : ""}" data-lang="zh" ${paper.abstract_zh ? "" : "disabled"}>中文摘要</button>
            <button class="abstract-tab ${!paper.abstract_zh ? "is-active" : ""}" data-lang="en" ${paper.abstract ? "" : "disabled"}>英文摘要</button>
          </div>
          <div class="abstract-content">
            <div class="abstract abstract-pane ${paper.abstract_zh ? "is-active" : ""}" data-lang="zh">${renderAbstractHtml(paper.abstract_zh, keywordEl.value.trim(), "zh")}</div>
            <div class="abstract abstract-pane ${!paper.abstract_zh ? "is-active" : ""}" data-lang="en">${renderAbstractHtml(paper.abstract, keywordEl.value.trim(), "en")}</div>
          </div>
        </div>
        <div class="links">${links.join("")}</div>
      </article>
    `;
  }).join("");

  const hasMore = state.visibleCount < state.filtered.length;
  const footerHtml = hasMore
    ? `
      <div class="results-footer">
        <button class="load-more-button" type="button" data-action="load-more">
          继续加载 ${Math.min(RENDER_BATCH_SIZE, state.filtered.length - state.visibleCount)} 篇
        </button>
      </div>
    `
    : "";

  resultsEl.innerHTML = cardsHtml + footerHtml;
}

function syncViewTabs() {
  viewTabs.forEach((tab) => {
    tab.classList.toggle("is-active", tab.dataset.view === state.activeView);
  });
}

function handleViewChange(event) {
  const nextView = event.currentTarget.dataset.view;
  if (nextView === state.activeView) {
    return;
  }
  state.activeView = nextView;
  render();
}

function handleConferenceFilterClick(event) {
  const button = event.target.closest("[data-conference]");
  if (!button) {
    return;
  }
  const key = button.dataset.conference;
  if (!key) {
    return;
  }
  if (state.selectedConferences.has(key)) {
    state.selectedConferences.delete(key);
  } else {
    state.selectedConferences.add(key);
  }
  saveFilters();
  renderConferenceFilters();
  render();
}

function handleYearFilterClick(event) {
  const button = event.target.closest("[data-year]");
  if (!button) {
    return;
  }
  const year = button.dataset.year;
  if (!year) {
    return;
  }
  if (state.selectedYears.has(year)) {
    state.selectedYears.delete(year);
  } else {
    state.selectedYears.add(year);
  }
  saveFilters();
  renderYearFilters();
  render();
}

function handleAbstractToggle(event) {
  const button = event.target.closest(".abstract-tab");
  if (!button || button.disabled) {
    return;
  }
  const panel = button.closest(".abstract-panel");
  if (!panel) {
    return;
  }
  const lang = button.dataset.lang;
  panel.querySelectorAll(".abstract-tab").forEach((tab) => {
    tab.classList.toggle("is-active", tab.dataset.lang === lang);
  });
  panel.querySelectorAll(".abstract-pane").forEach((pane) => {
    pane.classList.toggle("is-active", pane.dataset.lang === lang);
  });
}

function handlePreferenceClick(event) {
  const button = event.target.closest(".meta-icon-button");
  if (!button) {
    return;
  }
  const article = button.closest(".paper");
  const paperId = article?.dataset.paperId;
  if (!paperId) {
    return;
  }

  const currentPaper = [...state.raw, ...getFavoritePapers(), ...getViewedPapers(), ...getDislikedPapers()]
    .find((paper) => getPaperId(paper) === paperId);
  if (!currentPaper) {
    return;
  }

  if (button.dataset.action === "download") {
    const url = getPaperDownloadUrl(currentPaper);
    if (!url) {
      return;
    }
    const link = document.createElement("a");
    link.href = url;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.download = "";
    document.body.appendChild(link);
    link.click();
    link.remove();
    return;
  }

  if (button.dataset.action === "like") {
    if (state.preferences.liked[paperId]) {
      delete state.preferences.liked[paperId];
    } else {
      state.preferences.liked[paperId] = currentPaper;
      delete state.preferences.disliked[paperId];
    }
  }

  if (button.dataset.action === "viewed") {
    if (state.preferences.viewed[paperId]) {
      delete state.preferences.viewed[paperId];
    } else {
      state.preferences.viewed[paperId] = currentPaper;
      delete state.preferences.disliked[paperId];
    }
  }

  if (button.dataset.action === "dislike") {
    delete state.preferences.liked[paperId];
    delete state.preferences.viewed[paperId];
    if (state.preferences.disliked[paperId]) {
      delete state.preferences.disliked[paperId];
    } else {
      state.preferences.disliked[paperId] = currentPaper;
    }
  }

  savePreferences();
  render();
}

function handleLoadMore(event) {
  const button = event.target.closest('[data-action="load-more"]');
  if (!button) {
    return;
  }
  state.visibleCount = Math.min(state.visibleCount + RENDER_BATCH_SIZE, state.filtered.length);
  updateStatus();
  renderResults();
}

resultsEl.addEventListener("click", handleLoadMore);
savePreferences();
loadData();
