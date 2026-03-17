const state = {
  raw: [],
  filtered: [],
  conference: "osdi",
  activeView: "browse",
  preferences: loadPreferences(),
};

const conferenceEl = document.getElementById("conference");
const yearEl = document.getElementById("year");
const keywordEl = document.getElementById("keyword");
const hasAbstractEl = document.getElementById("hasAbstract");
const sortEl = document.getElementById("sort");
const summaryEl = document.getElementById("summary");
const resultsEl = document.getElementById("results");
const loadingEl = document.getElementById("loading");
const filtersPanelEl = document.getElementById("filtersPanel");
const viewTabs = [...document.querySelectorAll(".view-tab")];

conferenceEl.addEventListener("change", () => {
  state.conference = conferenceEl.value;
  loadData();
});
yearEl.addEventListener("change", render);
keywordEl.addEventListener("input", render);
hasAbstractEl.addEventListener("change", render);
sortEl.addEventListener("change", render);
resultsEl.addEventListener("click", handleAbstractToggle);
resultsEl.addEventListener("click", handlePreferenceClick);
viewTabs.forEach((tab) => tab.addEventListener("click", handleViewChange));

function loadPreferences() {
  try {
    const raw = JSON.parse(localStorage.getItem("paper-preferences") || "{}");
    return {
      liked: raw.liked && typeof raw.liked === "object" ? raw.liked : {},
      viewed: raw.viewed && typeof raw.viewed === "object" ? raw.viewed : {},
      disliked: Array.isArray(raw.disliked) ? raw.disliked : [],
    };
  } catch {
    return { liked: {}, viewed: {}, disliked: [] };
  }
}

function savePreferences() {
  localStorage.setItem("paper-preferences", JSON.stringify(state.preferences));
}

function getPaperId(paper) {
  return [paper.conference, paper.year, paper.title].join("::");
}

function isLiked(paper) {
  return Boolean(state.preferences.liked[getPaperId(paper)]);
}

function isDisliked(paper) {
  return state.preferences.disliked.includes(getPaperId(paper));
}

function getFavoritePapers() {
  return Object.values(state.preferences.liked).filter((paper) => !isDisliked(paper) && !isViewed(paper));
}

function isViewed(paper) {
  return Boolean(state.preferences.viewed[getPaperId(paper)]);
}

function getViewedPapers() {
  return Object.values(state.preferences.viewed).filter((paper) => !isDisliked(paper));
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
  const escaped = escapeHtml(value || "");
  if (!keyword) {
    return escaped;
  }
  const pattern = new RegExp(`(${escapeRegExp(keyword)})`, "ig");
  return escaped.replace(pattern, '<mark class="search-hit">$1</mark>');
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
  summaryEl.innerHTML = "";
  try {
    const res = await fetch(`/api/papers?conference=${encodeURIComponent(state.conference)}`);
    const payload = await res.json();
    if (!res.ok) {
      throw new Error(payload.detail || payload.error || "请求失败");
    }
    state.raw = payload.papers || [];
    fillYears(payload.available_years || []);
    render();
    loadingEl.textContent = state.activeView === "browse"
      ? ""
      : state.activeView === "viewed"
        ? "已看完标记保存在当前浏览器中。"
        : "我的喜欢保存在当前浏览器中。";
  } catch (error) {
    loadingEl.textContent = `加载失败：${error.message}`;
  }
}

function fillYears(years) {
  const current = yearEl.value;
  yearEl.innerHTML = '<option value="">全部年份</option>';
  years.forEach((year) => {
    const option = document.createElement("option");
    option.value = String(year);
    option.textContent = String(year);
    yearEl.appendChild(option);
  });
  if ([...yearEl.options].some((item) => item.value === current)) {
    yearEl.value = current;
  }
}

function render() {
  syncViewTabs();
  if (state.activeView === "favorites") {
    filtersPanelEl.hidden = true;
    loadingEl.textContent = "我的喜欢保存在当前浏览器中。";
    state.filtered = getFavoritePapers().sort((a, b) => Number(b.year) - Number(a.year) || a.title.localeCompare(b.title));
    renderSummary();
    renderResults();
    return;
  }

  if (state.activeView === "viewed") {
    filtersPanelEl.hidden = true;
    loadingEl.textContent = "已看完标记保存在当前浏览器中。";
    state.filtered = getViewedPapers().sort((a, b) => Number(b.year) - Number(a.year) || a.title.localeCompare(b.title));
    renderSummary();
    renderResults();
    return;
  }

  filtersPanelEl.hidden = false;
  loadingEl.textContent = "";
  const year = yearEl.value;
  const keyword = keywordEl.value.trim().toLowerCase();
  const requireAbstract = hasAbstractEl.checked;
  const sort = sortEl.value;

  const papers = state.raw
    .filter((paper) => {
      if (isDisliked(paper)) {
        return false;
      }
      if (isViewed(paper)) {
        return false;
      }
      if (year && String(paper.year) !== year) {
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
      if (sort === "title_asc") {
        return a.title.localeCompare(b.title);
      }
      return Number(b.year) - Number(a.year) || a.title.localeCompare(b.title);
    });

  state.filtered = papers;
  renderSummary();
  renderResults();
}

function renderSummary() {
  const total = state.filtered.length;
  const abstractCount = state.filtered.filter((item) => item.abstract).length;
  const yearCount = new Set(state.filtered.map((item) => item.year)).size;
  const labels = state.activeView === "favorites"
    ? ["喜欢条目", "带摘要收藏", "收藏年份"]
    : state.activeView === "viewed"
      ? ["已看完条目", "带摘要已看完", "已看完年份"]
      : ["当前结果", "带摘要论文", "覆盖年份"];
  summaryEl.innerHTML = `
    <article class="stat">
      <div class="stat-value">${total}</div>
      <div class="stat-label">${labels[0]}</div>
    </article>
    <article class="stat">
      <div class="stat-value">${abstractCount}</div>
      <div class="stat-label">${labels[1]}</div>
    </article>
    <article class="stat">
      <div class="stat-value">${yearCount}</div>
      <div class="stat-label">${labels[2]}</div>
    </article>
  `;
}

function renderResults() {
  if (!state.filtered.length) {
    resultsEl.innerHTML = `<div class="empty">${
      state.activeView === "favorites"
        ? "你还没有标记喜欢的论文。"
        : state.activeView === "viewed"
          ? "你还没有标记已看完的论文。"
          : "当前筛选条件下没有结果。"
    }</div>`;
    return;
  }

  resultsEl.innerHTML = state.filtered.map((paper) => {
    const paperId = getPaperId(paper);
    const tags = [
      `<span class="tag">${paper.conference_label}</span>`,
      `<span class="tag">${paper.year}</span>`,
    ];
    if (paper.pages) {
      tags.push(`<span class="tag">pp. ${escapeHtml(paper.pages)}</span>`);
    }
    if (paper.abstract_source) {
      tags.push(`<span class="tag">摘要: ${escapeHtml(paper.abstract_source)}</span>`);
    }

    const links = [];
    if (paper.source_url) {
      links.push(`<a href="${paper.source_url}" target="_blank" rel="noreferrer">原始页面</a>`);
    }
    if (paper.dblp_url) {
      links.push(`<a href="${paper.dblp_url}" target="_blank" rel="noreferrer">DBLP</a>`);
    }
    if (paper.openalex_id) {
      links.push(`<a href="${paper.openalex_id}" target="_blank" rel="noreferrer">OpenAlex</a>`);
    }
    if (paper.doi_url) {
      links.push(`<a href="${paper.doi_url}" target="_blank" rel="noreferrer">DOI</a>`);
    }

    return `
      <article class="paper" data-paper-id="${escapeHtml(paperId)}">
        <div class="meta">
          <div class="meta-tags">${tags.join("")}</div>
          <div class="meta-actions">
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
            <p class="abstract abstract-pane ${paper.abstract_zh ? "is-active" : ""}" data-lang="zh">${highlightHtml(paper.abstract_zh || "暂无中文摘要", keywordEl.value.trim())}</p>
            <p class="abstract abstract-pane ${!paper.abstract_zh ? "is-active" : ""}" data-lang="en">${highlightHtml(paper.abstract || "暂无英文摘要", keywordEl.value.trim())}</p>
          </div>
        </div>
        <div class="links">${links.join("")}</div>
      </article>
    `;
  }).join("");
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

  const currentPaper = [...state.raw, ...getFavoritePapers(), ...getViewedPapers()].find((paper) => getPaperId(paper) === paperId);
  if (!currentPaper) {
    return;
  }

  if (button.dataset.action === "like") {
    if (state.preferences.liked[paperId]) {
      delete state.preferences.liked[paperId];
    } else {
      state.preferences.liked[paperId] = currentPaper;
      state.preferences.disliked = state.preferences.disliked.filter((id) => id !== paperId);
    }
  }

  if (button.dataset.action === "viewed") {
    if (state.preferences.viewed[paperId]) {
      delete state.preferences.viewed[paperId];
    } else {
      state.preferences.viewed[paperId] = currentPaper;
      state.preferences.disliked = state.preferences.disliked.filter((id) => id !== paperId);
    }
  }

  if (button.dataset.action === "dislike") {
    delete state.preferences.liked[paperId];
    delete state.preferences.viewed[paperId];
    if (state.preferences.disliked.includes(paperId)) {
      state.preferences.disliked = state.preferences.disliked.filter((id) => id !== paperId);
    } else {
      state.preferences.disliked = [...state.preferences.disliked, paperId];
    }
  }

  savePreferences();
  render();
}

loadData();
