import argparse
import difflib
import json
import mimetypes
import re
from html import unescape
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
from tqdm import tqdm


BASE_DIR = Path(__file__).resolve().parent
CACHE_PATH = BASE_DIR / "data" / "papers_cache.json"
ASSETS_DIR = BASE_DIR / "assets"
USER_AGENT = "paper-abstract-local-service/1.0"
REQUEST_TIMEOUT = 20
CACHE_TTL_SECONDS = 60 * 60 * 24
DEFAULT_LOOKBACK_YEARS = 3
MAX_OPENALEX_CANDIDATES = 10
ABSTRACT_WORKERS = 8
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 12315
TRANSLATE_CHAR_LIMIT = 5000

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


def strip_jats_tags(value: str) -> str:
    value = re.sub(r"</?(jats:)?(p|i|b|sup|sub|sc|italic|bold)>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return compact_spaces(unescape(value))


def is_probable_abstract(text: str) -> bool:
    normalized = compact_spaces(text)
    if len(normalized) < 120:
        return False
    lowered = normalized.lower()
    blocked_fragments = [
        "usenix is a nonprofit organization",
        "page not found",
        "javascript is disabled",
        "skip to main content",
        "access provided by",
        "you do not have access",
    ]
    return not any(fragment in lowered for fragment in blocked_fragments)


def extract_doi(url: str) -> str:
    if not url:
        return ""
    match = re.search(r"10\.\d{4,9}/[-._;()/:a-z0-9]+", url, flags=re.I)
    return match.group(0) if match else ""


class PaperRepository:
    def __init__(self, cache_path: Path) -> None:
        self.cache_path = cache_path
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.session.trust_env = False
        self.translation_session = requests.Session()
        self.translation_session.headers.update({"User-Agent": USER_AGENT})
        self.translation_session.trust_env = True
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

    def get_cached_latest_years(self, conference: str) -> list[int]:
        cached = self.cache["conference_years"].get(conference, {})
        return list(cached.get("years", []))

    def has_usable_cache(self, conferences: list[str] | None = None) -> bool:
        conferences = conferences or list(CONFERENCES.keys())
        for conference in conferences:
            years = self.get_cached_latest_years(conference)
            if not years:
                return False
            has_any_papers = any(
                self.cache["papers"].get(f"{conference}:{year}", {}).get("items")
                for year in years
            )
            if not has_any_papers:
                return False
        return True

    def get_papers(self, conference: str, force_refresh: bool = False, show_progress: bool = False) -> list[dict[str, Any]]:
        years = self.get_latest_years(conference)
        papers: list[dict[str, Any]] = []
        year_iter = tqdm(years, desc=f"{conference} years", unit="year", leave=False) if show_progress else years
        for year in year_iter:
            papers.extend(
                self._get_papers_for_year(
                    conference,
                    year,
                    force_refresh=force_refresh,
                    show_progress=show_progress,
                )
            )
        papers.sort(key=lambda item: (-int(item["year"]), item["title"].lower()))
        return papers

    def get_cached_papers(self, conference: str) -> list[dict[str, Any]]:
        years = self.get_cached_latest_years(conference)
        papers: list[dict[str, Any]] = []
        for year in years:
            cache_key = f"{conference}:{year}"
            cached = self.cache["papers"].get(cache_key, {})
            items = cached.get("items", [])
            if self._cached_items_need_upgrade(items):
                items = self._upgrade_cached_items(items, conference=conference, year=year, show_progress=False)
                self.cache["papers"][cache_key] = {
                    "items": items,
                    "expires_at": cached.get("expires_at", 0),
                    "updated_at": time.time(),
                }
                self._save_cache()
            papers.extend(items)
        papers.sort(key=lambda item: (-int(item["year"]), item["title"].lower()))
        return papers

    def build_cache(
        self,
        conferences: list[str] | None = None,
        force_refresh: bool = True,
        show_progress: bool = True,
    ) -> dict[str, int]:
        conferences = conferences or list(CONFERENCES.keys())
        summary: dict[str, int] = {}
        conference_iter = tqdm(conferences, desc="conferences", unit="conf") if show_progress else conferences
        for conference in conference_iter:
            papers = self.get_papers(conference, force_refresh=force_refresh, show_progress=show_progress)
            summary[conference] = len(papers)
        self.cache.setdefault("metadata", {})
        self.cache["metadata"]["last_build_at"] = time.time()
        self.cache["metadata"]["mode"] = "prefetched"
        self._save_cache()
        return summary

    def _get_papers_for_year(
        self,
        conference: str,
        year: int,
        force_refresh: bool = False,
        show_progress: bool = False,
    ) -> list[dict[str, Any]]:
        cache_key = f"{conference}:{year}"
        cached = self.cache["papers"].get(cache_key)
        if cached and cached.get("expires_at", 0) > time.time() and not force_refresh:
            items = cached["items"]
            if self._cached_items_need_upgrade(items):
                items = self._upgrade_cached_items(items, conference=conference, year=year, show_progress=show_progress)
                with self.lock:
                    self.cache["papers"][cache_key] = {
                        "items": items,
                        "expires_at": time.time() + CACHE_TTL_SECONDS,
                        "updated_at": time.time(),
                    }
                    self._save_cache()
            return items

        items = self._fetch_dblp_papers(conference, year, show_progress=show_progress)
        with self.lock:
            self.cache["papers"][cache_key] = {
                "items": items,
                "expires_at": time.time() + CACHE_TTL_SECONDS,
                "updated_at": time.time(),
            }
            self._save_cache()
        return items

    def _fetch_dblp_papers(self, conference: str, year: int, show_progress: bool = False) -> list[dict[str, Any]]:
        slug = CONFERENCES[conference]["dblp_slug"]
        list_progress = tqdm(
            total=1,
            desc=f"{conference} {year} 抓论文列表",
            unit="step",
            leave=False,
            disable=not show_progress,
        )
        query = urllib.parse.quote(f'toc:db/conf/{slug}/{slug}{year}.bht:')
        url = f"https://dblp.org/search/publ/api?format=json&h=1000&q={query}"
        payload = self._get_json(url)
        list_progress.update(1)
        list_progress.close()
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
                "abstract_zh": "",
                "abstract_source": "",
                "openalex_id": "",
                "doi": extract_doi(info.get("ee", "")),
            }
            papers.append(paper)

        self._populate_abstracts(
            papers,
            conference=conference,
            year=year,
            show_progress=show_progress,
        )
        self._populate_translations(
            papers,
            conference=conference,
            year=year,
            show_progress=show_progress,
        )
        return papers

    def _populate_abstracts(
        self,
        papers: list[dict[str, Any]],
        conference: str,
        year: int,
        show_progress: bool = False,
    ) -> None:
        progress = tqdm(
            total=len(papers),
            desc=f"{conference} {year} 抓原始摘要",
            unit="paper",
            leave=False,
            disable=not show_progress,
        )
        with ThreadPoolExecutor(max_workers=ABSTRACT_WORKERS) as executor:
            future_map = {
                executor.submit(self._find_best_abstract, paper): paper
                for paper in papers
            }
            for future in as_completed(future_map):
                abstract_info = future.result()
                if abstract_info:
                    future_map[future].update(abstract_info)
                progress.update(1)
        progress.close()

    def _populate_translations(
        self,
        papers: list[dict[str, Any]],
        conference: str,
        year: int,
        show_progress: bool = False,
    ) -> None:
        targets = [paper for paper in papers if paper.get("abstract")]
        progress = tqdm(
            total=len(targets),
            desc=f"{conference} {year} 翻译中文摘要",
            unit="paper",
            leave=False,
            disable=not show_progress or not targets,
        )
        with ThreadPoolExecutor(max_workers=ABSTRACT_WORKERS) as executor:
            future_map = {
                executor.submit(self._translate_paper_abstract, paper): paper
                for paper in targets
            }
            for future in as_completed(future_map):
                translated = future.result()
                if translated:
                    future_map[future]["abstract_zh"] = translated
                progress.update(1)
        progress.close()

    def _cached_items_need_upgrade(self, items: list[dict[str, Any]]) -> bool:
        return any(
            "abstract_zh" not in item
            or (item.get("abstract") and not item.get("abstract_zh"))
            or (
                item.get("source_url")
                and item.get("abstract_source") in {"", "OpenAlex"}
            )
            for item in items
        )

    def _upgrade_cached_items(
        self,
        items: list[dict[str, Any]],
        conference: str = "",
        year: int | None = None,
        show_progress: bool = False,
    ) -> list[dict[str, Any]]:
        targets = [
            item
            for item in items
            if (
                "abstract_zh" not in item
                or (item.get("abstract") and not item.get("abstract_zh"))
                or (
                    item.get("source_url")
                    and item.get("abstract_source") in {"", "OpenAlex"}
                )
            )
        ]
        self._populate_abstracts(targets, conference=conference, year=year or 0, show_progress=show_progress)
        self._populate_translations(targets, conference=conference, year=year or 0, show_progress=show_progress)
        for item in items:
            item.setdefault("abstract_zh", "")
        return items

    def _find_best_abstract(self, paper: dict[str, Any]) -> dict[str, str]:
        abstract_info = self._find_source_abstract(paper)
        if not abstract_info:
            abstract_info = self._find_openalex_abstract(paper["title"], paper["year"])
        return abstract_info

    def _translate_paper_abstract(self, paper: dict[str, Any]) -> str:
        return self._translate_to_chinese(paper.get("abstract", ""))

    def _find_source_abstract(self, paper: dict[str, Any]) -> dict[str, str]:
        url = paper.get("source_url", "")
        if not url:
            return {}
        try:
            html_text = self._get_text(url)
        except requests.RequestException:
            doi = extract_doi(url)
            if not doi:
                return {}
            return self._find_acm_abstract_by_doi(doi)

        abstract = self._extract_abstract_from_html(html_text)
        if not abstract:
            doi = extract_doi(url)
            if doi:
                return self._find_acm_abstract_by_doi(doi)
            return {}

        return {
            "abstract": abstract,
            "abstract_source": "Source Page",
        }

    def _extract_abstract_from_html(self, html_text: str) -> str:
        soup = BeautifulSoup(html_text, "html.parser")

        # Prefer site-specific content blocks before generic meta descriptions.
        selectors = [
            ".field-name-field-paper-description",
            "section#abstract",
            ".abstractSection",
            "#abstract",
            "[data-title='Abstract']",
        ]
        for selector in selectors:
            for node in soup.select(selector):
                text = compact_spaces(node.get_text(" ", strip=True))
                if is_probable_abstract(text):
                    return text

        meta_keys = {"citation_abstract", "description", "og:description", "twitter:description"}
        for meta in soup.find_all("meta"):
            key = (meta.get("name") or meta.get("property") or "").lower()
            if key not in meta_keys:
                continue
            text = compact_spaces(meta.get("content") or "")
            if is_probable_abstract(text):
                return text

        return ""

    def _find_acm_abstract_by_doi(self, doi: str) -> dict[str, str]:
        api_url = f"https://api.crossref.org/works/{urllib.parse.quote(doi)}"
        try:
            message = self._get_json(api_url).get("message", {})
        except requests.RequestException:
            return {}

        abstract = strip_jats_tags(message.get("abstract") or "")
        if not is_probable_abstract(abstract):
            return {}

        return {
            "abstract": abstract,
            "abstract_source": "Crossref",
            "doi": doi,
        }

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
            "doi": extract_doi(selected.get("doi") or ""),
        }

    def _translate_to_chinese(self, text: str) -> str:
        source = compact_spaces(text)
        if not source or len(source) > TRANSLATE_CHAR_LIMIT:
            return ""
        try:
            url = "https://translate.googleapis.com/translate_a/single"
            response = self.translation_session.get(
                url,
                params={"client": "gtx", "sl": "en", "tl": "zh-CN", "dt": "t", "q": source},
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError, json.JSONDecodeError):
            return ""

        if not isinstance(payload, list) or not payload or not isinstance(payload[0], list):
            return ""
        translated_parts = [part[0] for part in payload[0] if isinstance(part, list) and part and part[0]]
        return compact_spaces("".join(translated_parts))


REPOSITORY = PaperRepository(CACHE_PATH)


class PaperRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self._send_asset("index.html")
            return
        if parsed.path.startswith("/assets/"):
            self._send_asset(parsed.path.removeprefix("/assets/"))
            return
        if parsed.path == "/api/papers":
            self._handle_api_papers(parsed.query)
            return
        self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

    def _handle_api_papers(self, query_string: str) -> None:
        params = urllib.parse.parse_qs(query_string)
        conference = params.get("conference", ["osdi"])[0].lower()
        if conference not in CONFERENCES:
            self._send_json(
                {"error": f"Unsupported conference: {conference}"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        papers = REPOSITORY.get_cached_papers(conference)
        years = sorted({paper["year"] for paper in papers}, reverse=True)
        if not papers:
            self._send_json(
                {
                    "error": "No cached data available.",
                    "detail": "Run `python3 app.py build-cache` first, then open the web page.",
                },
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return

        self._send_json(
            {
                "conference": conference,
                "conference_label": CONFERENCES[conference]["label"],
                "available_years": years,
                "count": len(papers),
                "papers": papers,
            }
        )

    def _send_asset(self, asset_name: str) -> None:
        assets_root = ASSETS_DIR.resolve()
        asset_path = (ASSETS_DIR / asset_name).resolve()
        if assets_root not in asset_path.parents and asset_path != assets_root:
            self._send_json({"error": "Invalid asset path"}, status=HTTPStatus.BAD_REQUEST)
            return
        if not asset_path.exists() or not asset_path.is_file():
            self._send_json({"error": "Asset not found"}, status=HTTPStatus.NOT_FOUND)
            return

        payload = asset_path.read_bytes()
        content_type, _ = mimetypes.guess_type(str(asset_path))
        if asset_path.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        elif asset_path.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif asset_path.suffix == ".html":
            content_type = "text/html; charset=utf-8"

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        return


def run_server() -> None:
    server = ThreadingHTTPServer((SERVER_HOST, SERVER_PORT), PaperRequestHandler)
    local_ip = detect_local_ip()
    print(f"Serving on http://127.0.0.1:{SERVER_PORT}")
    print(f"Serving on http://{local_ip}:{SERVER_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        server.shutdown()
        server.server_close()
        print("Server stopped.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Conference paper cache builder and local viewer")
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=["run", "build-cache", "serve"],
        help="run: build cache then serve; build-cache: only build cache; serve: only serve cached data",
    )
    args = parser.parse_args()

    if args.command == "build-cache":
        print("Building local cache...")
        summary = REPOSITORY.build_cache(force_refresh=True, show_progress=True)
        for conference, count in summary.items():
            print(f"{conference}: {count} papers cached")

    if args.command == "run":
        if REPOSITORY.has_usable_cache():
            print("Cache found, starting server directly...")
        else:
            print("No usable cache found, building local cache...")
            summary = REPOSITORY.build_cache(force_refresh=True, show_progress=True)
            for conference, count in summary.items():
                print(f"{conference}: {count} papers cached")
        run_server()

    if args.command == "serve":
        run_server()


if __name__ == "__main__":
    main()
