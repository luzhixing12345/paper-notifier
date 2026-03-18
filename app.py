import argparse
import difflib
import json
import re
from html import unescape
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm


BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "paper_cache"
ASSETS_DIR = BASE_DIR / "assets"
STATIC_DATA_PATH = ASSETS_DIR / "papers-data.json"
USER_AGENT = "paper-abstract-cache-builder/1.0"
REQUEST_TIMEOUT = 20
CACHE_TTL_SECONDS = 60 * 60 * 24
DEFAULT_LOOKBACK_YEARS = 5
MAX_OPENALEX_CANDIDATES = 10
ABSTRACT_WORKERS = 8
TRANSLATE_CHAR_LIMIT = 5000

CONFERENCES = {
    "osdi": {"label": "OSDI", "dblp_slug": "osdi"},
    "nsdi": {"label": "NSDI", "dblp_slug": "nsdi"},
    "sosp": {"label": "SOSP", "dblp_slug": "sosp"},
    "asplos": {"label": "ASPLOS", "dblp_slug": "asplos"},
    "eurosys": {"label": "EuroSys", "dblp_slug": "eurosys"},
    "fast": {"label": "FAST", "dblp_slug": "fast"},
    "dac": {"label": "DAC", "dblp_slug": "dac"},
    "isca": {"label": "ISCA", "dblp_slug": "isca"},
    "micro": {"label": "MICRO", "dblp_slug": "micro"},
    "hpca": {"label": "HPCA", "dblp_slug": "hpca"},
    "sigmod": {"label": "SIGMOD", "dblp_slug": "sigmod"},
    "sigcomm": {"label": "SIGCOMM", "dblp_slug": "sigcomm"},
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


def strip_jats_tags(value: str) -> str:
    value = re.sub(r"</?(jats:)?(p|i|b|sup|sub|sc|italic|bold)>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return compact_spaces(unescape(value))


def clean_dblp_title(value: str) -> str:
    text = value or ""
    if "<" in text and ">" in text:
        text = BeautifulSoup(text, "html.parser").get_text(" ")
    else:
        text = unescape(text)
    return compact_spaces(text)


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


def build_doi_url(doi: str) -> str:
    return f"https://doi.org/{doi}" if doi else ""


def normalize_source_url(url: str) -> str:
    doi = extract_doi(url)
    if doi and doi.startswith("10.1145/"):
        return f"https://dl.acm.org/doi/abs/{doi}"
    return url


class PaperRepository:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.session.trust_env = False
        self.translation_session = requests.Session()
        self.translation_session.headers.update({"User-Agent": USER_AGENT})
        self.translation_session.trust_env = True
        self.cache = self._load_cache()

    def _empty_cache(self) -> dict[str, Any]:
        return {
            "papers": {},
            "conference_years": {},
            "metadata": {"created_at": 0},
        }

    def _metadata_path(self) -> Path:
        return self.cache_dir / "metadata.json"

    def _paper_info_path(self, conference: str, year: int) -> Path:
        return self.cache_dir / str(year) / conference / "info.json"

    def _load_cache(self) -> dict[str, Any]:
        cache = self._empty_cache()
        metadata_path = self._metadata_path()
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                if isinstance(metadata, dict):
                    cache["metadata"] = metadata.get("metadata", cache["metadata"])
                    cache["conference_years"] = metadata.get("conference_years", {})
            except (json.JSONDecodeError, OSError):
                pass

        for year_dir in self.cache_dir.iterdir():
            if not year_dir.is_dir() or not year_dir.name.isdigit():
                continue
            for conf_dir in year_dir.iterdir():
                if not conf_dir.is_dir():
                    continue
                info_path = conf_dir / "info.json"
                if not info_path.exists():
                    continue
                try:
                    payload = json.loads(info_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                conference = conf_dir.name
                year = int(year_dir.name)
                cache_key = f"{conference}:{year}"
                cache["papers"][cache_key] = payload

        return cache

    def _save_cache(self) -> None:
        metadata_payload = json.dumps(
            {
                "metadata": self.cache.get("metadata", {}),
                "conference_years": self.cache.get("conference_years", {}),
            },
            ensure_ascii=False,
            indent=2,
        )
        self._metadata_path().write_text(metadata_payload, encoding="utf-8")

        for cache_key, payload in self.cache.get("papers", {}).items():
            conference, year_text = cache_key.split(":", 1)
            info_path = self._paper_info_path(conference, int(year_text))
            info_path.parent.mkdir(parents=True, exist_ok=True)
            info_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

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
        cached_years = cached.get("years", [])
        cached_year_entries = cached.get("year_entries", {})
        if (
            cached_years
            and len(cached_years) >= lookback_years
            and cached_year_entries
            and all(cached_year_entries.get(str(year)) for year in cached_years[:lookback_years])
        ):
            return cached["years"][:lookback_years]

        html_text = self._get_text(f"https://dblp.org/db/conf/{slug}/index.html")
        year_entries = self._extract_year_entries(html_text, slug)
        found_years = sorted(year_entries.keys(), reverse=True)
        min_year = current_year() - lookback_years + 1
        years = [year for year in found_years if year >= min_year][:lookback_years]
        with self.lock:
            self.cache["conference_years"][conference] = {
                "years": years,
                "year_entries": {str(year): year_entries.get(year, []) for year in years},
                "expires_at": time.time() + CACHE_TTL_SECONDS,
            }
            self._save_cache()
        return years

    def _extract_year_entries(self, html_text: str, slug: str) -> dict[int, list[str]]:
        entries: dict[int, set[str]] = {}
        pattern = re.compile(rf"/db/conf/{re.escape(slug)}/([^\"/]*?(\d{{4}})[^\"/]*)\.html")
        for match in pattern.finditer(html_text):
            entry_name = match.group(1)
            if not entry_name.startswith(slug):
                continue
            year = int(match.group(2))
            if year > current_year():
                continue
            entries.setdefault(year, set()).add(entry_name)
        return {year: sorted(names) for year, names in entries.items()}

    def _get_year_entry_names(self, conference: str, year: int) -> list[str]:
        cached = self.cache["conference_years"].get(conference, {})
        year_entries = cached.get("year_entries", {})
        if str(year) in year_entries and year_entries[str(year)]:
            return list(year_entries[str(year)])
        slug = CONFERENCES[conference]["dblp_slug"]
        return [f"{slug}{year}"]

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
            for item in items:
                self._normalize_links(item)
            papers.extend(items)
        papers.sort(key=lambda item: (-int(item["year"]), item["title"].lower()))
        return papers

    def build_cache(
        self,
        conferences: list[str] | None = None,
        force_refresh: bool = False,
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
        self.export_static_data()
        return summary

    def export_static_data(self, output_path: Path = STATIC_DATA_PATH) -> Path:
        papers_by_conference = {
            key: self.get_cached_papers(key)
            for key in CONFERENCES
        }
        papers = [
            paper
            for conference in CONFERENCES
            for paper in papers_by_conference[conference]
        ]
        papers.sort(key=lambda item: (-int(item["year"]), item["conference"], item["title"].lower()))
        available_conferences = [
            {"key": key, "label": value["label"]}
            for key, value in CONFERENCES.items()
            if papers_by_conference[key]
        ]
        payload = {
            "conference": "all",
            "conference_label": "All Conferences",
            "available_conferences": available_conferences,
            "available_years": sorted({paper["year"] for paper in papers}, reverse=True),
            "count": len(papers),
            "papers": papers,
            "generated_at": int(time.time()),
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return output_path

    def _get_papers_for_year(
        self,
        conference: str,
        year: int,
        force_refresh: bool = False,
        show_progress: bool = False,
    ) -> list[dict[str, Any]]:
        cache_key = f"{conference}:{year}"
        cached = self.cache["papers"].get(cache_key)
        if cached and cached.get("items") and not force_refresh:
            items = cached["items"]
            for item in items:
                self._normalize_links(item)
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
        entry_names = self._get_year_entry_names(conference, year)
        list_progress = tqdm(
            total=len(entry_names),
            desc=f"{conference} {year} 抓论文列表",
            unit="step",
            leave=False,
            disable=not show_progress,
        )
        raw_hits: list[dict[str, Any]] = []
        for entry_name in entry_names:
            query = urllib.parse.quote(f'toc:db/conf/{slug}/{entry_name}.bht:')
            url = f"https://dblp.org/search/publ/api?format=json&h=1000&q={query}"
            payload = self._get_json(url)
            entry_hits = payload.get("result", {}).get("hits", {}).get("hit", [])
            if isinstance(entry_hits, dict):
                entry_hits = [entry_hits]
            raw_hits.extend(entry_hits)
            list_progress.update(1)
        list_progress.close()

        papers: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for hit in raw_hits:
            info = hit.get("info", {})
            dblp_key = info.get("key", "")
            if dblp_key and dblp_key in seen_keys:
                continue
            if dblp_key:
                seen_keys.add(dblp_key)
            authors = info.get("authors", {}).get("author", [])
            if isinstance(authors, dict):
                authors = [authors]
            title = clean_dblp_title(info.get("title", ""))
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
                "source_url": normalize_source_url(info.get("ee", "")),
                "abstract": "",
                "abstract_zh": "",
                "abstract_source": "",
                "openalex_id": "",
                "doi": extract_doi(info.get("ee", "")),
                "doi_url": build_doi_url(extract_doi(info.get("ee", ""))),
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

    def _normalize_links(self, item: dict[str, Any]) -> None:
        doi = item.get("doi") or extract_doi(item.get("source_url", "")) or extract_doi(item.get("doi_url", ""))
        item["doi"] = doi
        item["doi_url"] = build_doi_url(doi)
        item["source_url"] = normalize_source_url(item.get("source_url") or item["doi_url"])

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
            "doi_url": build_doi_url(doi),
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
            "doi_url": build_doi_url(extract_doi(selected.get("doi") or "")),
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


REPOSITORY = PaperRepository(CACHE_DIR)

def print_summary(summary: dict[str, int]) -> None:
    for conference, count in summary.items():
        print(f"{conference}: {count} papers cached")


def main() -> None:

    print("Building missing cache data...")
    summary = REPOSITORY.build_cache(force_refresh=False, show_progress=True)
    print_summary(summary)
    print(f"static data exported to {STATIC_DATA_PATH}")
    return


if __name__ == "__main__":
    main()
