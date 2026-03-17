const state = {
  raw: [],
  filtered: [],
  conference: "osdi",
};

const conferenceEl = document.getElementById("conference");
const yearEl = document.getElementById("year");
const keywordEl = document.getElementById("keyword");
const hasAbstractEl = document.getElementById("hasAbstract");
const sortEl = document.getElementById("sort");
const summaryEl = document.getElementById("summary");
const resultsEl = document.getElementById("results");
const loadingEl = document.getElementById("loading");

conferenceEl.addEventListener("change", () => {
  state.conference = conferenceEl.value;
  loadData();
});
yearEl.addEventListener("change", render);
keywordEl.addEventListener("input", render);
hasAbstractEl.addEventListener("change", render);
sortEl.addEventListener("change", render);
resultsEl.addEventListener("click", handleAbstractToggle);

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
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
    loadingEl.textContent = "";
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
  const year = yearEl.value;
  const keyword = keywordEl.value.trim().toLowerCase();
  const requireAbstract = hasAbstractEl.checked;
  const sort = sortEl.value;

  const papers = state.raw
    .filter((paper) => {
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
        ...(paper.authors || []),
        paper.abstract || "",
      ].join(" ").toLowerCase();
      return haystack.includes(keyword);
    })
    .sort((a, b) => {
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
  summaryEl.innerHTML = `
    <article class="stat">
      <div class="stat-value">${total}</div>
      <div class="stat-label">当前结果</div>
    </article>
    <article class="stat">
      <div class="stat-value">${abstractCount}</div>
      <div class="stat-label">带摘要论文</div>
    </article>
    <article class="stat">
      <div class="stat-value">${yearCount}</div>
      <div class="stat-label">覆盖年份</div>
    </article>
  `;
}

function renderResults() {
  if (!state.filtered.length) {
    resultsEl.innerHTML = '<div class="empty">当前筛选条件下没有结果。</div>';
    return;
  }

  resultsEl.innerHTML = state.filtered.map((paper) => {
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
    if (paper.doi) {
      links.push(`<a href="${paper.doi}" target="_blank" rel="noreferrer">DOI</a>`);
    }

    return `
      <article class="paper">
        <div class="meta">${tags.join("")}</div>
        <h2>${escapeHtml(paper.title)}</h2>
        <p class="authors">${escapeHtml((paper.authors || []).join(", "))}</p>
        <div class="abstract-panel">
          <div class="abstract-tabs">
            <button class="abstract-tab ${paper.abstract_zh ? "is-active" : ""}" data-lang="zh" ${paper.abstract_zh ? "" : "disabled"}>中文摘要</button>
            <button class="abstract-tab ${!paper.abstract_zh ? "is-active" : ""}" data-lang="en" ${paper.abstract ? "" : "disabled"}>英文摘要</button>
          </div>
          <div class="abstract-content">
            <p class="abstract abstract-pane ${paper.abstract_zh ? "is-active" : ""}" data-lang="zh">${escapeHtml(paper.abstract_zh || "暂无中文摘要")}</p>
            <p class="abstract abstract-pane ${!paper.abstract_zh ? "is-active" : ""}" data-lang="en">${escapeHtml(paper.abstract || "暂无英文摘要")}</p>
          </div>
        </div>
        <div class="links">${links.join("")}</div>
      </article>
    `;
  }).join("");
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

loadData();
