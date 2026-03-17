import difflib
import json
import re
import socket
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup


BASE_DIR = Path(__file__).resolve().parent
CACHE_PATH = BASE_DIR / "data" / "papers_cache.json"
USER_AGENT = "paper-abstract-local-service/1.0"
REQUEST_TIMEOUT = 20
CACHE_TTL_SECONDS = 60 * 60 * 24
DEFAULT_LOOKBACK_YEARS = 3
MAX_OPENALEX_CANDIDATES = 10
ABSTRACT_WORKERS = 8
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 12315

CONFERENCES = {
    "osdi": {"label": "OSDI", "dblp_slug": "osdi"},
    "nsdi": {"label": "NSDI", "dblp_slug": "nsdi"},
    "sosp": {"label": "SOSP", "dblp_slug": "sosp"},
}


def current_year() -> int:
    return time.localtime().tm_year


def normalize_text(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def compact_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def is_metadata_entry(title: str, conference_label: str, year: int) -> bool:
    normalized = normalize_text(title)
    conference_token = conference_label.lower()
    return (
        "proceedings" in normalized
        or (conference_token in normalized and str(year) in normalized and "symposium" in normalized)
    )


def inverted_index_to_abstract(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    tokens: list[str] = []
    for word, positions in index.items():
        for pos in positions:
            if pos >= len(tokens):
                tokens.extend([""] * (pos - len(tokens) + 1))
            tokens[pos] = word
    return compact_spaces(" ".join(tokens))


def detect_local_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


class PaperRepository:
    def __init__(self, cache_path: Path) -> None:
        self.cache_path = cache_path
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.session.trust_env = False
        self.cache = self._load_cache()

    def _load_cache(self) -> dict[str, Any]:
        if not self.cache_path.exists():
            return {
                "papers": {},
                "conference_years": {},
                "metadata": {"created_at": 0},
            }
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {
                "papers": {},
                "conference_years": {},
                "metadata": {"created_at": 0},
            }

    def _save_cache(self) -> None:
        payload = json.dumps(self.cache, ensure_ascii=False, indent=2)
        self.cache_path.write_text(payload, encoding="utf-8")

    def _get_json(self, url: str) -> dict[str, Any]:
        response = self.session.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()

    def _get_text(self, url: str) -> str:
        response = self.session.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.text

    def get_latest_years(self, conference: str, lookback_years: int = DEFAULT_LOOKBACK_YEARS) -> list[int]:
        slug = CONFERENCES[conference]["dblp_slug"]
        cached = self.cache["conference_years"].get(conference, {})
        expires_at = cached.get("expires_at", 0)
        if cached.get("years") and expires_at > time.time():
            return cached["years"][:lookback_years]

        html_text = self._get_text(f"https://dblp.org/db/conf/{slug}/index.html")
        found_years = sorted(
            {
                int(year)
                for year in re.findall(rf"{re.escape(slug)}(\d{{4}})\.html", html_text)
                if int(year) <= current_year()
            },
            reverse=True,
        )
        years = found_years[:lookback_years]
        with self.lock:
            self.cache["conference_years"][conference] = {
                "years": years,
                "expires_at": time.time() + CACHE_TTL_SECONDS,
            }
            self._save_cache()
        return years

    def get_papers(self, conference: str, force_refresh: bool = False) -> list[dict[str, Any]]:
        years = self.get_latest_years(conference)
        papers: list[dict[str, Any]] = []
        for year in years:
            papers.extend(self._get_papers_for_year(conference, year, force_refresh=force_refresh))
        papers.sort(key=lambda item: (-int(item["year"]), item["title"].lower()))
        return papers

    def _get_papers_for_year(self, conference: str, year: int, force_refresh: bool = False) -> list[dict[str, Any]]:
        cache_key = f"{conference}:{year}"
        cached = self.cache["papers"].get(cache_key)
        if cached and cached.get("expires_at", 0) > time.time() and not force_refresh:
            return cached["items"]

        items = self._fetch_dblp_papers(conference, year)
        with self.lock:
            self.cache["papers"][cache_key] = {
                "items": items,
                "expires_at": time.time() + CACHE_TTL_SECONDS,
                "updated_at": time.time(),
            }
            self._save_cache()
        return items

    def _fetch_dblp_papers(self, conference: str, year: int) -> list[dict[str, Any]]:
        slug = CONFERENCES[conference]["dblp_slug"]
        query = urllib.parse.quote(f'toc:db/conf/{slug}/{slug}{year}.bht:')
        url = f"https://dblp.org/search/publ/api?format=json&h=1000&q={query}"
        payload = self._get_json(url)
        raw_hits = payload.get("result", {}).get("hits", {}).get("hit", [])
        if isinstance(raw_hits, dict):
            raw_hits = [raw_hits]

        papers: list[dict[str, Any]] = []
        for hit in raw_hits:
            info = hit.get("info", {})
            authors = info.get("authors", {}).get("author", [])
            if isinstance(authors, dict):
                authors = [authors]
            title = compact_spaces(BeautifulSoup(info.get("title", ""), "html.parser").get_text(" "))
            if is_metadata_entry(title, CONFERENCES[conference]["label"], int(info.get("year") or year)):
                continue
            paper = {
                "conference": conference,
                "conference_label": CONFERENCES[conference]["label"],
                "year": int(info.get("year") or year),
                "title": title,
                "authors": [compact_spaces(author.get("text", "")) for author in authors if author.get("text")],
                "pages": info.get("pages", ""),
                "type": info.get("type", ""),
                "access": info.get("access", ""),
                "dblp_key": info.get("key", ""),
                "dblp_url": info.get("url", ""),
                "source_url": info.get("ee", ""),
                "abstract": "",
                "abstract_source": "",
                "openalex_id": "",
                "doi": "",
            }
            papers.append(paper)

        with ThreadPoolExecutor(max_workers=ABSTRACT_WORKERS) as executor:
            future_map = {
                executor.submit(self._find_openalex_abstract, paper["title"], paper["year"]): paper
                for paper in papers
            }
            for future in as_completed(future_map):
                abstract_info = future.result()
                if abstract_info:
                    future_map[future].update(abstract_info)
        return papers

    def _find_openalex_abstract(self, title: str, year: int) -> dict[str, str]:
        encoded_title = urllib.parse.quote(title)
        url = f"https://api.openalex.org/works?search={encoded_title}&per-page={MAX_OPENALEX_CANDIDATES}"
        try:
            results = self._get_json(url).get("results", [])
        except requests.RequestException:
            return {}

        normalized_title = normalize_text(title)
        best: tuple[float, dict[str, Any] | None] = (0.0, None)
        for item in results:
            display_name = item.get("display_name", "")
            abstract = inverted_index_to_abstract(item.get("abstract_inverted_index"))
            if not display_name or not abstract:
                continue

            score = difflib.SequenceMatcher(None, normalized_title, normalize_text(display_name)).ratio()
            publication_year = item.get("publication_year")
            if publication_year == year:
                score += 0.2
            elif isinstance(publication_year, int) and abs(publication_year - year) <= 1:
                score += 0.05

            if score > best[0]:
                best = (score, item)

        if not best[1] or best[0] < 0.78:
            return {}

        selected = best[1]
        return {
            "abstract": inverted_index_to_abstract(selected.get("abstract_inverted_index")),
            "abstract_source": "OpenAlex",
            "openalex_id": selected.get("id", ""),
            "doi": selected.get("doi") or "",
        }


REPOSITORY = PaperRepository(CACHE_PATH)


def build_frontend_html() -> str:
    conference_options = "\n".join(
        f'<option value="{key}">{value["label"]}</option>'
        for key, value in CONFERENCES.items()
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>A 类系统会议论文浏览</title>
  <style>
    :root {{
      --bg: #f3efe5;
      --panel: rgba(255, 250, 242, 0.92);
      --ink: #1f1a16;
      --muted: #665d55;
      --accent: #9f3d1d;
      --accent-soft: #f0d6ca;
      --border: rgba(31, 26, 22, 0.12);
      --shadow: 0 24px 60px rgba(53, 35, 24, 0.14);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Noto Serif SC", "Source Han Serif SC", serif;
      background:
        radial-gradient(circle at top left, rgba(159, 61, 29, 0.18), transparent 32%),
        radial-gradient(circle at right 15%, rgba(33, 106, 89, 0.18), transparent 28%),
        linear-gradient(180deg, #faf6ee 0%, var(--bg) 100%);
      color: var(--ink);
    }}
    .shell {{
      max-width: 1280px;
      margin: 0 auto;
      padding: 32px 20px 56px;
    }}
    .hero {{
      margin-bottom: 18px;
      padding: 28px;
      border-radius: 24px;
      background: linear-gradient(135deg, rgba(255,255,255,0.78), rgba(246, 230, 219, 0.94));
      box-shadow: var(--shadow);
      border: 1px solid rgba(255,255,255,0.8);
    }}
    h1 {{
      margin: 0 0 12px;
      font-size: clamp(30px, 4vw, 48px);
      line-height: 1.08;
    }}
    .sub {{
      margin: 0;
      color: var(--muted);
      max-width: 860px;
      line-height: 1.7;
    }}
    .toolbar {{
      position: sticky;
      top: 16px;
      z-index: 2;
      margin: 18px 0 24px;
      padding: 18px;
      border-radius: 20px;
      background: var(--panel);
      backdrop-filter: blur(10px);
      border: 1px solid var(--border);
      box-shadow: var(--shadow);
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 12px;
    }}
    label {{
      display: flex;
      flex-direction: column;
      gap: 8px;
      font-size: 13px;
      color: var(--muted);
      letter-spacing: 0.02em;
    }}
    select, input {{
      width: 100%;
      padding: 12px 14px;
      border-radius: 14px;
      border: 1px solid var(--border);
      background: rgba(255,255,255,0.86);
      color: var(--ink);
      font: inherit;
    }}
    .toggle {{
      align-self: end;
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 12px 14px;
      border-radius: 14px;
      border: 1px solid var(--border);
      background: rgba(255,255,255,0.86);
      color: var(--ink);
    }}
    .summary {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-bottom: 20px;
    }}
    .stat {{
      min-width: 180px;
      padding: 16px 18px;
      border-radius: 18px;
      background: rgba(255,255,255,0.72);
      border: 1px solid rgba(255,255,255,0.8);
      box-shadow: 0 12px 30px rgba(53, 35, 24, 0.08);
    }}
    .stat strong {{
      display: block;
      font-size: 28px;
      margin-bottom: 4px;
    }}
    .results {{
      display: grid;
      gap: 16px;
    }}
    .paper {{
      padding: 22px;
      border-radius: 20px;
      background: rgba(255,255,255,0.78);
      border: 1px solid rgba(255,255,255,0.86);
      box-shadow: 0 16px 36px rgba(53, 35, 24, 0.08);
      animation: rise 320ms ease;
    }}
    .meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-bottom: 10px;
      color: var(--muted);
      font-size: 13px;
    }}
    .tag {{
      padding: 4px 10px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
    }}
    .paper h2 {{
      margin: 0 0 12px;
      font-size: 24px;
      line-height: 1.35;
    }}
    .authors {{
      margin: 0 0 14px;
      color: var(--muted);
      line-height: 1.6;
    }}
    .abstract {{
      margin: 0;
      line-height: 1.75;
    }}
    .links {{
      display: flex;
      gap: 14px;
      margin-top: 14px;
      flex-wrap: wrap;
    }}
    a {{
      color: var(--accent);
      text-decoration: none;
      border-bottom: 1px solid rgba(159, 61, 29, 0.25);
    }}
    .empty {{
      padding: 42px 20px;
      text-align: center;
      color: var(--muted);
      background: rgba(255,255,255,0.72);
      border-radius: 20px;
      border: 1px solid rgba(255,255,255,0.86);
    }}
    .loading {{
      color: var(--muted);
      margin-bottom: 16px;
    }}
    @keyframes rise {{
      from {{ opacity: 0; transform: translateY(10px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}
    @media (max-width: 960px) {{
      .toolbar {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 640px) {{
      .shell {{ padding: 18px 14px 40px; }}
      .hero {{ padding: 22px; }}
      .toolbar {{ grid-template-columns: 1fr; top: 8px; }}
      .paper h2 {{ font-size: 21px; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <h1>系统 A 类会议近 3 年录稿论文浏览</h1>
      <p class="sub">当前先覆盖 OSDI、NSDI、SOSP。后端实时从 DBLP 获取论文列表，并尝试用 OpenAlex 补摘要；页面支持按会议、年份、关键词和是否有摘要筛选。</p>
    </section>

    <section class="toolbar">
      <label>会议
        <select id="conference">{conference_options}</select>
      </label>
      <label>年份
        <select id="year">
          <option value="">全部年份</option>
        </select>
      </label>
      <label>关键词
        <input id="keyword" type="search" placeholder="标题 / 作者 / 摘要">
      </label>
      <label>排序
        <select id="sort">
          <option value="year_desc">年份降序</option>
          <option value="title_asc">标题 A-Z</option>
        </select>
      </label>
      <label class="toggle">
        <input id="hasAbstract" type="checkbox">
        只看有摘要
      </label>
    </section>

    <section class="summary" id="summary"></section>
    <div class="loading" id="loading">正在加载数据...</div>
    <section class="results" id="results"></section>
  </div>

  <script>
    const state = {{
      raw: [],
      filtered: [],
      conference: 'osdi',
    }};

    const conferenceEl = document.getElementById('conference');
    const yearEl = document.getElementById('year');
    const keywordEl = document.getElementById('keyword');
    const hasAbstractEl = document.getElementById('hasAbstract');
    const sortEl = document.getElementById('sort');
    const summaryEl = document.getElementById('summary');
    const resultsEl = document.getElementById('results');
    const loadingEl = document.getElementById('loading');

    conferenceEl.addEventListener('change', () => {{
      state.conference = conferenceEl.value;
      loadData();
    }});
    yearEl.addEventListener('change', render);
    keywordEl.addEventListener('input', render);
    hasAbstractEl.addEventListener('change', render);
    sortEl.addEventListener('change', render);

    function escapeHtml(value) {{
      return value
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
    }}

    async function loadData() {{
      loadingEl.textContent = '正在加载数据...';
      resultsEl.innerHTML = '';
      summaryEl.innerHTML = '';
      try {{
        const res = await fetch(`/api/papers?conference=${{encodeURIComponent(state.conference)}}`);
        const payload = await res.json();
        state.raw = payload.papers || [];
        fillYears(payload.available_years || []);
        render();
        loadingEl.textContent = '';
      }} catch (error) {{
        loadingEl.textContent = `加载失败：${{error.message}}`;
      }}
    }}

    function fillYears(years) {{
      const current = yearEl.value;
      yearEl.innerHTML = '<option value="">全部年份</option>';
      years.forEach((year) => {{
        const option = document.createElement('option');
        option.value = String(year);
        option.textContent = String(year);
        yearEl.appendChild(option);
      }});
      if ([...yearEl.options].some((item) => item.value === current)) {{
        yearEl.value = current;
      }}
    }}

    function render() {{
      const year = yearEl.value;
      const keyword = keywordEl.value.trim().toLowerCase();
      const requireAbstract = hasAbstractEl.checked;
      const sort = sortEl.value;

      let papers = state.raw.filter((paper) => {{
        if (year && String(paper.year) !== year) {{
          return false;
        }}
        if (requireAbstract && !paper.abstract) {{
          return false;
        }}
        if (!keyword) {{
          return true;
        }}
        const haystack = [
          paper.title,
          ...(paper.authors || []),
          paper.abstract || '',
        ].join(' ').toLowerCase();
        return haystack.includes(keyword);
      }});

      papers.sort((a, b) => {{
        if (sort === 'title_asc') {{
          return a.title.localeCompare(b.title);
        }}
        return Number(b.year) - Number(a.year) || a.title.localeCompare(b.title);
      }});

      state.filtered = papers;
      renderSummary();
      renderResults();
    }}

    function renderSummary() {{
      const total = state.filtered.length;
      const abstractCount = state.filtered.filter((item) => item.abstract).length;
      const yearCount = new Set(state.filtered.map((item) => item.year)).size;
      summaryEl.innerHTML = `
        <article class="stat"><strong>${{total}}</strong>当前结果</article>
        <article class="stat"><strong>${{abstractCount}}</strong>带摘要论文</article>
        <article class="stat"><strong>${{yearCount}}</strong>覆盖年份</article>
      `;
    }}

    function renderResults() {{
      if (!state.filtered.length) {{
        resultsEl.innerHTML = '<div class="empty">当前筛选条件下没有结果。</div>';
        return;
      }}

      resultsEl.innerHTML = state.filtered.map((paper) => {{
        const tags = [
          `<span class="tag">${{paper.conference_label}}</span>`,
          `<span class="tag">${{paper.year}}</span>`,
        ];
        if (paper.pages) {{
          tags.push(`<span class="tag">pp. ${{escapeHtml(paper.pages)}}</span>`);
        }}
        if (paper.abstract_source) {{
          tags.push(`<span class="tag">摘要: ${{escapeHtml(paper.abstract_source)}}</span>`);
        }}

        const links = [];
        if (paper.source_url) {{
          links.push(`<a href="${{paper.source_url}}" target="_blank" rel="noreferrer">原始页面</a>`);
        }}
        if (paper.dblp_url) {{
          links.push(`<a href="${{paper.dblp_url}}" target="_blank" rel="noreferrer">DBLP</a>`);
        }}
        if (paper.openalex_id) {{
          links.push(`<a href="${{paper.openalex_id}}" target="_blank" rel="noreferrer">OpenAlex</a>`);
        }}
        if (paper.doi) {{
          links.push(`<a href="${{paper.doi}}" target="_blank" rel="noreferrer">DOI</a>`);
        }}

        return `
          <article class="paper">
            <div class="meta">${{tags.join('')}}</div>
            <h2>${{escapeHtml(paper.title)}}</h2>
            <p class="authors">${{escapeHtml((paper.authors || []).join(', '))}}</p>
            <p class="abstract">${{escapeHtml(paper.abstract || '暂无摘要')}}</p>
            <div class="links">${{links.join('')}}</div>
          </article>
        `;
      }}).join('');
    }}

    loadData();
  </script>
</body>
</html>
"""


class PaperRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self._send_html(build_frontend_html())
            return
        if parsed.path == "/api/papers":
            self._handle_api_papers(parsed.query)
            return
        self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

    def _handle_api_papers(self, query_string: str) -> None:
        params = urllib.parse.parse_qs(query_string)
        conference = params.get("conference", ["osdi"])[0].lower()
        force_refresh = params.get("refresh", ["0"])[0] == "1"
        if conference not in CONFERENCES:
            self._send_json(
                {"error": f"Unsupported conference: {conference}"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        try:
            papers = REPOSITORY.get_papers(conference, force_refresh=force_refresh)
            years = sorted({paper["year"] for paper in papers}, reverse=True)
            self._send_json(
                {
                    "conference": conference,
                    "conference_label": CONFERENCES[conference]["label"],
                    "available_years": years,
                    "count": len(papers),
                    "papers": papers,
                }
            )
        except requests.RequestException as exc:
            self._send_json(
                {
                    "error": "Failed to fetch remote conference data.",
                    "detail": str(exc),
                },
                status=HTTPStatus.BAD_GATEWAY,
            )

    def _send_html(self, content: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer((SERVER_HOST, SERVER_PORT), PaperRequestHandler)
    local_ip = detect_local_ip()
    print(f"Serving on http://127.0.0.1:{SERVER_PORT}")
    print(f"Serving on http://{local_ip}:{SERVER_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
