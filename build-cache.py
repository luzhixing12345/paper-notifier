import argparse
import difflib
import json
import os
import re
from html import unescape
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any
import datetime
import requests
from requests.adapters import HTTPAdapter
from bs4 import BeautifulSoup
from prettytable import PrettyTable
import e2me


BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "paper_cache"
ASSETS_DIR = BASE_DIR / "assets"
STATIC_DATA_PATH = ASSETS_DIR / "papers-data.json"
CONFERENCE_CONFIG_PATH = BASE_DIR / "CONFERENCE.txt"
JOURNAL_CONFIG_PATH = BASE_DIR / "JOURNAL.txt"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 5
REQUEST_RETRIES = 4
RETRY_BACKOFF_SECONDS = 1.5
MAX_OPENALEX_CANDIDATES = 10
ABSTRACT_WORKERS = 8
DBLP_ENTRY_WORKERS = 4
YEAR_FETCH_WORKERS = 4
BUILD_WORKERS = 4
TRANSLATE_CHAR_LIMIT = 5000
DEBUG_SLOW_REQUEST_SECONDS = 2.0
DEBUG_SLOW_STAGE_SECONDS = 5.0
BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "max-age=0",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Chromium";v="134", "Google Chrome";v="134", "Not:A-Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Linux"',
}

DEFAULT_LOOKBACK_YEARS = 5


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


REQUEST_TIMEOUT = env_int("BUILD_CACHE_REQUEST_TIMEOUT", REQUEST_TIMEOUT)
ABSTRACT_WORKERS = env_int("BUILD_CACHE_ABSTRACT_WORKERS", 4)
DBLP_ENTRY_WORKERS = env_int("BUILD_CACHE_DBLP_ENTRY_WORKERS", 2)
YEAR_FETCH_WORKERS = env_int("BUILD_CACHE_YEAR_FETCH_WORKERS", 2)
BUILD_WORKERS = env_int("BUILD_CACHE_BUILD_WORKERS", 2)
ABSTRACT_FETCH_RETRIES = env_int("BUILD_CACHE_ABSTRACT_FETCH_RETRIES", 3)
ABSTRACT_TRANSLATION_RETRIES = env_int("BUILD_CACHE_ABSTRACT_TRANSLATION_RETRIES", 3)


def print_progress_item(conference_label: str, year: int, index: int, total: int, title: str, action: str = "") -> None:
    short_title = title if len(title) <= 110 else title[:107] + "..."
    prefix = f"[{conference_label} {year}][{index}/{total}]"
    if action:
        print(f"{prefix} {action} {short_title}")
        return
    print(f"{prefix} {short_title}")


def static_data_shard_path(conference: str) -> Path:
    return ASSETS_DIR / f"papers-{conference}-data.json"


def parse_conference_filters(values: list[str] | None) -> list[str]:
    if not values:
        return []
    conferences: list[str] = []
    for value in values:
        for part in value.split(","):
            conference = part.strip().lower()
            if not conference:
                continue
            if conference not in VENUES:
                raise SystemExit(f"Unknown conference/journal: {conference}")
            if conference not in conferences:
                conferences.append(conference)
    return conferences


def parse_year_filters(values: list[str] | None) -> list[int]:
    if not values:
        return []
    years: list[int] = []
    for value in values:
        for part in value.split(","):
            text = part.strip()
            if not text:
                continue
            try:
                year = int(text)
            except ValueError as exc:
                raise SystemExit(f"Invalid year: {text}") from exc
            if year not in years:
                years.append(year)
    return years


def send_email(subject: str = "New Paper Alert", body: str = "Check out the latest papers in your field!") -> None:
    
    e2me.send_email(subject, body)

def format_conference_label(key: str) -> str:
    custom_labels = {
        "atc": "USENIX ATC",
        "eurosys": "EuroSys",
        "hotnets": "HotNets",
        "hotos": "HotOS",
        "imc": "IMC",
        "mobisys": "MobiSys",
        "middleware": "Middleware",
        "sensys": "SenSys",
        "sigmetrics": "SIGMETRICS",
        "taco": "TACO",
        "tcad": "TCAD",
    }
    return custom_labels.get(key, key.upper())


def get_venue_kind(key: str) -> str:
    return "journals" if key in {"taco", "tcad"} else "conf"


def load_venue_config(config_path: Path, venue_kind: str) -> tuple[int, dict[str, dict[str, str]]]:
    if not config_path.exists():
        if venue_kind == "journals":
            return DEFAULT_LOOKBACK_YEARS, {}
        raise FileNotFoundError(f"Conference config not found: {config_path}")

    lookback_years = DEFAULT_LOOKBACK_YEARS
    venues: dict[str, dict[str, str]] = {}
    for line_number, raw_line in enumerate(config_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if "=" in line:
            key, value = [part.strip() for part in line.split("=", 1)]
            if key.lower() != "lookback_years":
                raise ValueError(f"Unsupported config key on line {line_number}: {key}")
            lookback_years = int(value)
            if lookback_years <= 0:
                raise ValueError("lookback_years must be a positive integer")
            continue

        key = line.lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", key):
            raise ValueError(
                f"Invalid venue key on line {line_number}: {line}. Use lowercase words like `osdi` or `sigcomm`."
            )
        venues[key] = {
            "label": format_conference_label(key),
            "dblp_slug": key,
            "venue_kind": venue_kind,
            "lookback_years": lookback_years,
        }

    if not venues and venue_kind == "conf":
        raise ValueError(f"No conferences configured in {config_path}")
    return lookback_years, venues


CONFERENCE_LOOKBACK_YEARS, CONFERENCES = load_venue_config(CONFERENCE_CONFIG_PATH, "conf")
JOURNAL_LOOKBACK_YEARS, JOURNALS = load_venue_config(JOURNAL_CONFIG_PATH, "journals")
LOOKBACK_YEARS = CONFERENCE_LOOKBACK_YEARS
VENUES = {**CONFERENCES, **JOURNALS}


def current_year() -> int:
    return time.localtime().tm_year


def normalize_text(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def compact_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def compact_spaces_preserve_paragraphs(value: str) -> str:
    paragraphs = [compact_spaces(part) for part in re.split(r"\n\s*\n", value or "")]
    paragraphs = [part for part in paragraphs if part]
    return "\n\n".join(paragraphs)


def clean_abstract_heading(value: str) -> str:
    text = value.strip()
    text = re.sub(r"^\s*abstract\s*[:\-]?\s*", "", text, flags=re.I)
    return text.strip()


def clean_author_name(value: str) -> str:
    return compact_spaces(re.sub(r"\s+\d{4}$", "", value))


def is_valid_conf_entry_name(entry_name: str, slug: str) -> bool:
    if not re.fullmatch(r"[a-z0-9_-]+", entry_name):
        return False
    if not re.search(r"(19|20)\d{2}", entry_name):
        return False
    return entry_name.startswith(slug) or bool(re.fullmatch(r"(?:19|20)\d{2}[a-z0-9_-]*", entry_name))


def dedupe_conf_entry_names(entries: list[str], slug: str) -> list[str]:
    unique_entries = sorted(set(entries))
    canonical_entries = set(unique_entries)
    slug_prefixed_suffixes = {
        entry[len(slug):]
        for entry in unique_entries
        if entry.startswith(slug) and re.fullmatch(r"(?:19|20)\d{2}[a-z0-9_-]*", entry[len(slug):])
    }
    for entry in unique_entries:
        if not entry.startswith(slug) and entry in slug_prefixed_suffixes:
            canonical_entries.discard(entry)
    return sorted(canonical_entries)


def is_metadata_entry(title: str, conference_label: str, year: int) -> bool:
    normalized = normalize_text(title)
    conference_token = conference_label.lower()
    return (
        "proceedings" in normalized
        or (conference_token in normalized and str(year) in normalized and "symposium" in normalized)
    )


def is_excluded_paper_type(paper_type: str) -> bool:
    return compact_spaces(paper_type).lower() == "editorship"


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


def normalize_latex_fragment(value: str) -> str:
    text = compact_spaces(unescape(value or ""))
    replacements = {
        r"\times": "×",
        r"\cdot": "·",
        r"\leq": "<=",
        r"\geq": ">=",
        r"\neq": "!=",
        r"\alpha": "alpha",
        r"\beta": "beta",
        r"\gamma": "gamma",
        r"\lambda": "lambda",
        r"\mu": "mu",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = text.replace("{", "").replace("}", "")
    text = text.strip("$")
    return compact_spaces(text)


def strip_inline_markup(value: str) -> str:
    if not value:
        return ""

    text = unescape(value)

    def replace_formula(match: re.Match[str]) -> str:
        return f" {normalize_latex_fragment(match.group(1) or '')} "

    text = re.sub(
        r"<inline-formula\b[^>]*>.*?<tex-math\b[^>]*>(.*?)</tex-math>.*?</inline-formula>",
        replace_formula,
        text,
        flags=re.I | re.S,
    )
    text = re.sub(
        r"<inline-formula\b[^>]*>(.*?)</inline-formula>",
        replace_formula,
        text,
        flags=re.I | re.S,
    )
    text = re.sub(r"</?(?:monospace|italic|bold|sub|sup)\b[^>]*>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return compact_spaces(text)


def clean_abstract_text(value: str) -> str:
    paragraphs: list[str] = []
    for part in re.split(r"\n\s*\n", value or ""):
        cleaned = strip_inline_markup(part)
        if cleaned:
            paragraphs.append(cleaned)
    return "\n\n".join(paragraphs)


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


def format_cache_fill_email_subject(updates: dict[str, list[dict[str, Any]]]) -> str:
    entries = [entry for items in updates.values() for entry in items]
    if len(entries) == 1:
        entry = entries[0]
        paper_label = "Paper" if entry["paper_count"] == 1 else "Papers"
        return f"Paper Notifier: {entry['conference_label']} [{entry['year']}] {entry['paper_count']} {paper_label}"

    total_venue_years = len(entries)
    return f"Paper Notifier: {total_venue_years} new venue-year cache{'s' if total_venue_years != 1 else ''}"


def format_cache_fill_email_body(updates: dict[str, list[dict[str, Any]]]) -> str:
    total_venue_years = sum(len(items) for items in updates.values())
    entries = [entry for items in updates.values() for entry in items]
    if len(entries) == 1:
        entry = entries[0]
        return "\n".join(
            [
                "Detected a newly cached venue-year entry during build-cache.",
                "",
                f"Conference: {entry['conference_label']} ({entry['conference']})",
                f"Year: {entry['year']}",
                f"Papers: {entry['paper_count']}",
            ]
        )

    lines = [
        "Detected newly cached venue-year entries during build-cache.",
        "",
        f"Total new venue-years: {total_venue_years}",
        "",
    ]
    for conference in sorted(updates):
        entries = sorted(updates[conference], key=lambda item: -int(item["year"]))
        label = entries[0]["conference_label"] if entries else conference.upper()
        lines.append(f"{label} ({conference}): {len(entries)} new cache(s)")
        for entry in entries:
            lines.append(f"- [{entry['year']}] {entry['paper_count']} paper(s)")
        lines.append("")
    return "\n".join(lines).strip()


class PaperRepository:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self._debug_lock = threading.Lock()
        self._thread_local = threading.local()
        self._new_cache_keys: set[str] = set()
        self._repair_commands: list[str] = []
        self.debug_enabled = False
        self.debug_filters: set[str] = set()
        self.cache = self._load_cache()

    def configure_debug(self, enabled: bool, filters: list[str] | None = None) -> None:
        self.debug_enabled = enabled
        self.debug_filters = {value.lower() for value in (filters or []) if value.strip()}

    def _debug_matches(self, context: str) -> bool:
        if not self.debug_filters:
            return True
        lowered = context.lower()
        return any(token in lowered for token in self.debug_filters)

    def _debug(self, message: str, *, conference: str = "", year: int | None = None, force: bool = False) -> None:
        if not self.debug_enabled:
            return
        context = " ".join(part for part in [conference, str(year) if year is not None else ""] if part)
        if not force and not self._debug_matches(f"{context} {message}"):
            return
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        thread_name = threading.current_thread().name
        prefix = f"[debug {timestamp} {thread_name}]"
        if context:
            prefix += f" [{context}]"
        with self._debug_lock:
            print(f"{prefix} {message}")

    def _build_session(self, trust_env: bool) -> requests.Session:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})
        session.trust_env = trust_env
        adapter = HTTPAdapter(pool_connections=1, pool_maxsize=1, max_retries=0)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def _session_for(self, *, translation: bool = False) -> requests.Session:
        attr_name = "translation_session" if translation else "session"
        session = getattr(self._thread_local, attr_name, None)
        if session is None:
            session = self._build_session(trust_env=True)
            setattr(self._thread_local, attr_name, session)
        return session

    def _empty_cache(self) -> dict[str, Any]:
        return {
            "papers": {},
            "conference_years": {},
            "metadata": {},
        }

    def _metadata_path(self) -> Path:
        return self.cache_dir / "metadata.json"

    def _paper_info_path(self, conference: str, year: int) -> Path:
        return self.cache_dir / str(year) / conference / "info.json"

    def _infer_cached_paper_years(self, conference: str, lookback_years: int) -> list[int]:
        years = [
            int(year_dir.name)
            for year_dir in self.cache_dir.iterdir()
            if year_dir.is_dir()
            and year_dir.name.isdigit()
            and self._paper_info_path(conference, int(year_dir.name)).exists()
        ]
        years.sort(reverse=True)
        return years[:lookback_years]

    def _load_cache(self) -> dict[str, Any]:
        cache = self._empty_cache()
        metadata_path = self._metadata_path()
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                if isinstance(metadata, dict):
                    cache["metadata"] = self._sanitize_metadata(metadata.get("metadata", cache["metadata"]))
                    cache["conference_years"] = self._sanitize_conference_years(metadata.get("conference_years", {}))
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
                cache["papers"][cache_key] = self._sanitize_paper_payload(payload)

        return cache

    def _sanitize_metadata(self, metadata: Any) -> dict[str, Any]:
        if not isinstance(metadata, dict):
            return {}
        return {key: value for key, value in metadata.items() if key not in {"created_at", "last_build_at"}}

    def _sanitize_conference_years(self, conference_years: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(conference_years, dict):
            return {}

        sanitized: dict[str, dict[str, Any]] = {}
        for conference, payload in conference_years.items():
            if not isinstance(payload, dict):
                continue
            venue = VENUES.get(conference, {})
            slug = venue.get("dblp_slug", conference)
            venue_kind = venue.get("venue_kind", "conf")
            raw_year_entries = payload.get("year_entries", {})
            sanitized_year_entries: dict[str, list[str]] = {}
            if isinstance(raw_year_entries, dict):
                for year_text, entries in raw_year_entries.items():
                    if not isinstance(entries, list):
                        continue
                    if venue_kind == "journals":
                        cleaned_entries = [entry for entry in entries if isinstance(entry, str)]
                    else:
                        cleaned_entries = dedupe_conf_entry_names([
                            entry
                            for entry in entries
                            if isinstance(entry, str) and is_valid_conf_entry_name(entry, slug)
                        ], slug)
                    if cleaned_entries:
                        sanitized_year_entries[str(year_text)] = sorted(set(cleaned_entries))
            sanitized[conference] = {
                "years": payload.get("years", []),
                "year_entries": sanitized_year_entries,
            }
        return sanitized

    def _sanitize_paper_payload(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {"items": []}

        sanitized = {
            key: value
            for key, value in payload.items()
            if key not in {"expires_at", "updated_at"}
        }
        items = sanitized.get("items", [])
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("abstract"):
                    item["abstract"] = clean_abstract_text(str(item.get("abstract", "")))
                if item.get("abstract_zh"):
                    item["abstract_zh"] = clean_abstract_text(str(item.get("abstract_zh", "")))
        return sanitized

    def _save_cache(self) -> None:
        started_at = time.perf_counter()
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
        duration = time.perf_counter() - started_at
        if self.debug_enabled:
            self._debug(
                (
                    f"_save_cache finished in {duration:.2f}s "
                    f"(paper_entries={len(self.cache.get('papers', {}))}, "
                    f"conference_entries={len(self.cache.get('conference_years', {}))})"
                ),
                force=duration >= DEBUG_SLOW_STAGE_SECONDS,
            )

    def _request(
        self,
        url: str,
        *,
        expect_json: bool,
        headers: dict[str, str] | None = None,
        session: requests.Session | None = None,
    ) -> Any:
        last_error: requests.RequestException | None = None
        for attempt in range(REQUEST_RETRIES + 1):
            started_at = time.perf_counter()
            try:
                response = (session or self._session_for()).get(url, timeout=REQUEST_TIMEOUT, headers=headers)
                response.raise_for_status()
                duration = time.perf_counter() - started_at
                if self.debug_enabled and duration >= DEBUG_SLOW_REQUEST_SECONDS:
                    self._debug(
                        f"slow request {duration:.2f}s status={response.status_code} expect_json={expect_json} url={url}",
                        force=True,
                    )
                return response.json() if expect_json else response.text
            except requests.RequestException as exc:
                last_error = exc
                duration = time.perf_counter() - started_at
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                self._debug(
                    (
                        f"request failed attempt={attempt + 1}/{REQUEST_RETRIES + 1} "
                        f"after {duration:.2f}s expect_json={expect_json} url={url} error={exc}"
                    ),
                    force=True,
                )
                if attempt >= REQUEST_RETRIES or status_code in {401, 403, 404}:
                    break
                time.sleep(RETRY_BACKOFF_SECONDS * (2**attempt))
        assert last_error is not None
        raise last_error

    def _get_json(self, url: str) -> dict[str, Any]:
        payload = self._request(url, expect_json=True)
        if not isinstance(payload, dict):
            raise ValueError(f"Unexpected JSON payload type for {url}: {type(payload).__name__}")
        return payload

    def _get_text(self, url: str) -> str:
        payload = self._request(url, expect_json=False)
        if not isinstance(payload, str):
            raise ValueError(f"Unexpected text payload type for {url}: {type(payload).__name__}")
        return payload

    def _get_text_with_headers(
        self,
        url: str,
        *,
        headers: dict[str, str],
        session: requests.Session | None = None,
    ) -> str:
        payload = self._request(url, expect_json=False, headers=headers, session=session)
        if not isinstance(payload, str):
            raise ValueError(f"Unexpected text payload type for {url}: {type(payload).__name__}")
        return payload

    def get_latest_years(self, conference: str, lookback_years: int | None = None) -> list[int]:
        venue = VENUES[conference]
        slug = venue["dblp_slug"]
        venue_kind = venue.get("venue_kind", "conf")
        lookback_years = lookback_years or int(venue.get("lookback_years", LOOKBACK_YEARS))
        cached = self.cache["conference_years"].get(conference, {})
        cached_years = cached.get("years", [])
        cached_year_entries = cached.get("year_entries", {})
        if (
            cached_years
            and len(cached_years) >= lookback_years
            and cached_year_entries
            and all(cached_year_entries.get(str(year)) for year in cached_years[:lookback_years])
        ):
            self._debug(
                f"using cached year list {cached['years'][:lookback_years]}",
                conference=conference,
            )
            return cached["years"][:lookback_years]

        fallback_years = self._infer_cached_paper_years(conference, lookback_years)
        self._debug(
            f"refreshing year list from DBLP, fallback_years={fallback_years}",
            conference=conference,
        )
        try:
            html_text = self._get_text(f"https://dblp.org/db/{venue_kind}/{slug}/index.html")
        except requests.RequestException:
            if fallback_years:
                self._debug(
                    f"failed to refresh year list, fallback to cached paper years={fallback_years}",
                    conference=conference,
                    force=True,
                )
                return fallback_years
            raise

        year_entries = self._extract_year_entries(html_text, slug, venue_kind)
        found_years = sorted(year_entries.keys(), reverse=True)
        min_year = current_year() - lookback_years + 1
        years = [year for year in found_years if year >= min_year][:lookback_years]
        if not years and fallback_years:
            self._debug(
                f"no recent years parsed from DBLP, fallback to cached paper years={fallback_years}",
                conference=conference,
                force=True,
            )
            return fallback_years
        with self.lock:
            self.cache["conference_years"][conference] = {
                "years": years,
                "year_entries": {str(year): year_entries.get(year, []) for year in years},
            }
            self._save_cache()
        self._debug(
            f"resolved years={years}, entry_counts={{{', '.join(f'{year}:{len(year_entries.get(year, []))}' for year in years)}}}",
            conference=conference,
        )
        return years

    def _extract_year_entries(self, html_text: str, slug: str, venue_kind: str) -> dict[int, list[str]]:
        if venue_kind == "journals":
            return self._extract_journal_year_entries(html_text, slug)

        entries: dict[int, set[str]] = {}
        soup = BeautifulSoup(html_text, "html.parser")

        toc_pattern = re.compile(rf"/db/conf/{re.escape(slug)}/([^/?#]+)\.html(?:$|[?#])")
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            match = toc_pattern.search(href)
            if not match:
                continue
            entry_name = match.group(1)
            if not is_valid_conf_entry_name(entry_name, slug):
                continue
            year_match = re.search(r"(19|20)\d{2}", entry_name)
            if not year_match:
                continue
            year = int(year_match.group(0))
            if year > current_year():
                continue
            entries.setdefault(year, set()).add(entry_name)

        # Some venues such as DAC / HPCA expose year pages via dblp keys like `conf/dac/2025`
        # instead of legacy `/db/conf/dac/dac2025.html` links.
        if not entries:
            dblp_key_pattern = re.compile(rf"\bconf/{re.escape(slug)}/((?:19|20)\d{{2}}[a-z0-9_-]*)\b")
            for match in dblp_key_pattern.finditer(html_text):
                entry_name = match.group(1)
                if not is_valid_conf_entry_name(entry_name, slug):
                    continue
                year_match = re.search(r"(19|20)\d{2}", entry_name)
                if not year_match:
                    continue
                year = int(year_match.group(0))
                if year > current_year():
                    continue
                entries.setdefault(year, set()).add(entry_name)
        return {year: dedupe_conf_entry_names(list(names), slug) for year, names in entries.items()}

    def _extract_journal_year_entries(self, html_text: str, slug: str) -> dict[int, list[str]]:
        entries: dict[int, set[str]] = {}
        soup = BeautifulSoup(html_text, "html.parser")
        pattern = re.compile(rf"/db/journals/{re.escape(slug)}/([^\"/]+)\.html?$")
        for anchor in soup.find_all("a", href=True):
            match = pattern.search(anchor["href"])
            if not match:
                continue
            entry_name = match.group(1)
            if not entry_name.startswith(slug):
                continue
            container = anchor.find_parent(["li", "cite", "div", "nav"]) or anchor.parent
            context = container.get_text(" ", strip=True)
            years = [int(match.group(0)) for match in re.finditer(r"\b(?:19|20)\d{2}\b", context)]
            if not years:
                continue
            year = max(years)
            if year > current_year():
                continue
            entries.setdefault(year, set()).add(entry_name)
        return {year: sorted(names) for year, names in entries.items()}

    def _get_year_entry_names(self, conference: str, year: int) -> list[str]:
        cached = self.cache["conference_years"].get(conference, {})
        year_entries = cached.get("year_entries", {})
        if str(year) in year_entries and year_entries[str(year)]:
            entries = list(year_entries[str(year)])
            if VENUES[conference].get("venue_kind") == "journals":
                return entries
            slug = VENUES[conference]["dblp_slug"]
            return dedupe_conf_entry_names(
                [entry for entry in entries if is_valid_conf_entry_name(entry, slug)],
                slug,
            )
        slug = VENUES[conference]["dblp_slug"]
        if VENUES[conference].get("venue_kind") == "journals":
            return []
        return [f"{slug}{year}"]

    def get_cached_latest_years(self, conference: str) -> list[int]:
        cached = self.cache["conference_years"].get(conference, {})
        return list(cached.get("years", []))

    def has_usable_cache(self, conferences: list[str] | None = None) -> bool:
        conferences = conferences or list(VENUES.keys())
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

    def get_papers(
        self,
        conference: str,
        force_refresh: bool = False,
        show_progress: bool = False,
        years: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        started_at = time.perf_counter()
        years = list(years) if years else self.get_latest_years(conference)
        self._debug(f"start fetching years={years} force_refresh={force_refresh}", conference=conference)
        papers: list[dict[str, Any]] = []
        if show_progress:
            print(f"[{VENUES[conference]['label']}] years={years}")
        for year in years:
            papers.extend(
                self._get_papers_for_year(
                    conference,
                    year,
                    force_refresh=force_refresh,
                    show_progress=show_progress,
                )
            )
        papers.sort(key=lambda item: (-int(item["year"]), item["title"].lower()))
        self._debug(
            f"finished conference with papers={len(papers)} in {time.perf_counter() - started_at:.2f}s",
            conference=conference,
            force=True,
        )
        return papers

    def get_cached_papers(self, conference: str) -> list[dict[str, Any]]:
        years = self.get_cached_latest_years(conference)
        papers: list[dict[str, Any]] = []
        for year in years:
            cache_key = f"{conference}:{year}"
            cached = self.cache["papers"].get(cache_key, {})
            items = [
                item
                for item in cached.get("items", [])
                if not is_excluded_paper_type(item.get("type", ""))
            ]
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
        years: list[int] | None = None,
    ) -> dict[str, int]:
        started_at = time.perf_counter()
        conferences = conferences or list(VENUES.keys())
        summary: dict[str, int] = {}
        failures: dict[str, str] = {}
        self._new_cache_keys = set()
        self._repair_commands = []
        for index, conference in enumerate(conferences, start=1):
            if show_progress:
                print(f"[conference {index}/{len(conferences)}] {VENUES[conference]['label']} ({conference})")
            try:
                summary[conference] = len(
                    self.get_papers(
                        conference,
                        force_refresh=force_refresh,
                        show_progress=show_progress,
                        years=years,
                    )
                )
            except (requests.RequestException, ValueError) as exc:
                failures[conference] = str(exc)
                summary[conference] = 0
            except Exception as exc:
                failures[conference] = repr(exc)
                summary[conference] = 0
        self.cache["metadata"] = {"mode": "prefetched"}
        self._save_cache()
        self.export_static_data()
        self._send_cache_fill_notification()
        self._debug(
            f"build_cache completed in {time.perf_counter() - started_at:.2f}s for {len(conferences)} conferences",
            force=True,
        )
        if failures:
            print("\nFailed conferences:")
            for conference in sorted(failures):
                print(f"- {conference}: {failures[conference]}")
        if self._repair_commands:
            print("\nRepair commands:")
            for command in self._repair_commands:
                print("```bash")
                print(command)
                print("```")
        return summary

    def export_static_data(self, output_path: Path = STATIC_DATA_PATH) -> Path:
        papers_by_conference = {
            key: self.get_cached_papers(key)
            for key in VENUES
        }
        available_conferences = [
            {"key": key, "label": value["label"]}
            for key, value in VENUES.items()
            if papers_by_conference[key]
        ]
        output_path.parent.mkdir(parents=True, exist_ok=True)

        shard_files: list[dict[str, str]] = []
        all_years: set[int] = set()
        total_papers = 0
        for conference in VENUES:
            papers = papers_by_conference[conference]
            if not papers:
                continue
            papers.sort(key=lambda item: (-int(item["year"]), item["title"].lower()))
            all_years.update(int(paper["year"]) for paper in papers)
            total_papers += len(papers)
            shard_path = static_data_shard_path(conference)
            shard_payload = {
                "conference": conference,
                "conference_label": VENUES[conference]["label"],
                "count": len(papers),
                "papers": papers,
            }
            shard_path.write_text(json.dumps(shard_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            shard_files.append({"conference": conference, "file": shard_path.name})

        payload = {
            "available_conferences": available_conferences,
            "available_years": sorted(all_years, reverse=True),
            "count": total_papers,
            "data_files": shard_files,
        }
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return output_path

    def _send_cache_fill_notification(self) -> None:
        if not self._new_cache_keys:
            print("No new cache entries added.")
            return

        print("New cache entries added for: " + ", ".join(sorted(self._new_cache_keys)))
        updates: dict[str, list[dict[str, Any]]] = {}
        for cache_key in sorted(self._new_cache_keys):
            conference, year_text = cache_key.split(":", 1)
            payload = self.cache["papers"].get(cache_key, {})
            items = payload.get("items", [])
            updates.setdefault(conference, []).append(
                {
                    "conference": conference,
                    "conference_label": VENUES[conference]["label"],
                    "year": int(year_text),
                    "paper_count": len(items),
                }
            )

        send_email(
            format_cache_fill_email_subject(updates),
            format_cache_fill_email_body(updates),
        )

    def _get_papers_for_year(
        self,
        conference: str,
        year: int,
        force_refresh: bool = False,
        show_progress: bool = False,
    ) -> list[dict[str, Any]]:
        started_at = time.perf_counter()
        cache_key = f"{conference}:{year}"
        cached = self.cache["papers"].get(cache_key)
        if cached and cached.get("items") and not force_refresh:
            items = cached["items"]
            for item in items:
                self._normalize_links(item)
            self._debug(f"cache hit items={len(items)}", conference=conference, year=year)
            return items

        self._debug("cache miss, fetching papers", conference=conference, year=year, force=True)
        items = self._fetch_dblp_papers(conference, year, show_progress=show_progress)
        with self.lock:
            self.cache["papers"][cache_key] = {
                "items": items,
            }
            if not force_refresh:
                self._new_cache_keys.add(cache_key)
            self._save_cache()
        self._debug(
            f"year fetch finished items={len(items)} in {time.perf_counter() - started_at:.2f}s",
            conference=conference,
            year=year,
            force=True,
        )
        return items

    def _fetch_dblp_papers(self, conference: str, year: int, show_progress: bool = False) -> list[dict[str, Any]]:
        started_at = time.perf_counter()
        venue = VENUES[conference]
        slug = venue["dblp_slug"]
        venue_kind = venue.get("venue_kind", "conf")
        entry_names = self._get_year_entry_names(conference, year)
        self._debug(
            f"entry names={entry_names}",
            conference=conference,
            year=year,
            force=True,
        )
        raw_hits: list[dict[str, Any]] = []
        for entry_name in entry_names:
            entry_hits = self._fetch_dblp_entry_hits(venue_kind, slug, entry_name)
            raw_hits.extend(entry_hits)
            if show_progress:
                print(f"[{VENUES[conference]['label']} {year}] entry {entry_name} hits={len(entry_hits)}")
            self._debug(
                f"entry {entry_name} returned hits={len(entry_hits)}",
                conference=conference,
                year=year,
            )

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
            paper_type = info.get("type", "")
            if is_excluded_paper_type(paper_type):
                continue
            title = clean_dblp_title(info.get("title", ""))
            if is_metadata_entry(title, VENUES[conference]["label"], int(info.get("year") or year)):
                continue
            paper = {
                "conference": conference,
                "conference_label": VENUES[conference]["label"],
                "year": int(info.get("year") or year),
                "title": title,
                "authors": [clean_author_name(author.get("text", "")) for author in authors if author.get("text")],
                "pages": info.get("pages", ""),
                "type": paper_type,
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

        self._debug(
            f"parsed raw_hits={len(raw_hits)} unique_papers={len(papers)}",
            conference=conference,
            year=year,
            force=True,
        )

        process_started_at = time.perf_counter()
        processing_summary = self._populate_abstracts_and_translations_serial(
            papers,
            conference=conference,
            year=year,
            show_progress=show_progress,
        )
        abstracts_found = processing_summary["updated_abstracts"]
        translated = processing_summary["updated_translations"]
        failed_abstracts = processing_summary["failed_abstracts"]
        if show_progress:
            print("")
            print("Done.")
            print(f"Updated abstracts: {abstracts_found}")
            print(f"Updated translations: {translated}")
            print(f"Failed abstracts: {failed_abstracts}")
        if failed_abstracts:
            self._repair_commands.append(f"python3 full_miss_abstract.py {conference} {year}")
        self._debug(
            (
                f"paper processing finished in {time.perf_counter() - process_started_at:.2f}s "
                f"with abstracts={abstracts_found}/{len(papers)} translated={translated}/{abstracts_found}"
            ),
            conference=conference,
            year=year,
            force=True,
        )
        self._debug(
            f"_fetch_dblp_papers finished in {time.perf_counter() - started_at:.2f}s",
            conference=conference,
            year=year,
            force=True,
        )
        return papers

    def _fetch_dblp_entry_hits(self, venue_kind: str, slug: str, entry_name: str) -> list[dict[str, Any]]:
        query = urllib.parse.quote(f'toc:db/{venue_kind}/{slug}/{entry_name}.bht:')
        url = f"https://dblp.org/search/publ/api?format=json&h=1000&q={query}"
        payload = self._get_json(url)
        entry_hits = payload.get("result", {}).get("hits", {}).get("hit", [])
        if isinstance(entry_hits, dict):
            return [entry_hits]
        return entry_hits

    def _normalize_links(self, item: dict[str, Any]) -> None:
        doi = item.get("doi") or extract_doi(item.get("source_url", "")) or extract_doi(item.get("doi_url", ""))
        item["doi"] = doi
        item["doi_url"] = build_doi_url(doi)
        item["source_url"] = normalize_source_url(item.get("source_url") or item["doi_url"])

    def _populate_abstracts_and_translations_serial(
        self,
        papers: list[dict[str, Any]],
        conference: str,
        year: int,
        show_progress: bool = False,
    ) -> dict[str, int]:
        conference_label = VENUES.get(conference, {}).get("label", conference.upper())
        total = len(papers)
        updated_abstracts = 0
        updated_translations = 0
        failed_abstracts = 0
        for index, paper in enumerate(papers, start=1):
            title = paper.get("title", "<untitled>")
            if show_progress:
                print_progress_item(conference_label, year, index, total, title)
            abstract_info = self._find_best_abstract(paper)
            if abstract_info:
                paper.update(abstract_info)
                updated_abstracts += 1
                if show_progress:
                    print(
                        f"  -> \033[92mabstract ok\033[0m: source={paper.get('abstract_source') or '<unknown>'}, "
                        f"chars={len(paper.get('abstract', ''))}"
                    )
            else:
                failed_abstracts += 1
                if show_progress:
                    print("  -> \033[91mabstract failed\033[0m")
                continue

            translated = self._translate_paper_abstract(paper)
            if translated:
                paper["abstract_zh"] = translated
                updated_translations += 1
                if show_progress:
                    print(f"  -> \033[92mtranslation ok\033[0m: chars={len(translated)}")
            else:
                paper["abstract_zh"] = ""
                if show_progress:
                    print("  -> \033[91mtranslation skipped/failed\033[0m")
        return {
            "updated_abstracts": updated_abstracts,
            "updated_translations": updated_translations,
            "failed_abstracts": failed_abstracts,
        }

    def _populate_abstracts(
        self,
        papers: list[dict[str, Any]],
        conference: str,
        year: int,
        show_progress: bool = False,
    ) -> None:
        for paper in papers:
            abstract_info = self._find_best_abstract(paper)
            if abstract_info:
                paper.update(abstract_info)

    def _populate_translations(
        self,
        papers: list[dict[str, Any]],
        conference: str,
        year: int,
        show_progress: bool = False,
    ) -> None:
        for paper in papers:
            if not paper.get("abstract"):
                continue
            translated = self._translate_paper_abstract(paper)
            if translated:
                paper["abstract_zh"] = translated

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
        for attempt in range(1, ABSTRACT_FETCH_RETRIES + 1):
            doi = paper.get("doi") or extract_doi(paper.get("doi_url", "")) or extract_doi(paper.get("source_url", ""))
            abstract_info: dict[str, str] = {}
            if doi:
                abstract_info = self._find_doi_landing_abstract(paper, doi)
                if not abstract_info:
                    abstract_info = self._find_source_abstract(paper)
                if not abstract_info:
                    abstract_info = self._find_acm_abstract_by_doi(doi)
            else:
                abstract_info = self._find_source_abstract(paper)
                if not abstract_info:
                    abstract_info = self._find_openalex_abstract(paper["title"], paper["year"])
                    if abstract_info:
                        self._debug(
                            f"OpenAlex fallback matched title={paper['title'][:80]}",
                            conference=paper.get("conference", ""),
                            year=paper.get("year"),
                        )
            if abstract_info:
                return abstract_info
            print(f"  -> \033[93mretrying abstract fetch... (attempt {attempt}/{ABSTRACT_FETCH_RETRIES})\033[0m")
            self._debug(
                (
                    f"abstract fetch retry {attempt}/{ABSTRACT_FETCH_RETRIES} failed "
                    f"title={paper['title'][:80]}"
                ),
                conference=paper.get("conference", ""),
                year=paper.get("year"),
                force=attempt < ABSTRACT_FETCH_RETRIES,
            )
        self._debug(
            f"no abstract found title={paper['title'][:80]}",
            conference=paper.get("conference", ""),
            year=paper.get("year"),
        )
        return {}

    def _translate_paper_abstract(self, paper: dict[str, Any]) -> str:
        for attempt in range(1, ABSTRACT_TRANSLATION_RETRIES + 1):
            translated = self._translate_to_chinese(paper.get("abstract", ""))
            if translated:
                return translated
            self._debug(
                (
                    f"translation retry {attempt}/{ABSTRACT_TRANSLATION_RETRIES} failed "
                    f"title={paper.get('title', '')[:80]}"
                ),
                conference=paper.get("conference", ""),
                year=paper.get("year"),
                force=attempt < ABSTRACT_TRANSLATION_RETRIES,
            )
        return ""

    def _warm_up_doi_session(self, session: requests.Session, doi_url: str) -> None:
        steps = [
            ("https://doi.org/", {"Sec-Fetch-Site": "none", "Referer": ""}),
            (doi_url, {"Sec-Fetch-Site": "same-origin", "Referer": "https://doi.org/"}),
        ]
        for url, overrides in steps:
            headers = dict(BROWSER_HEADERS)
            headers.update(overrides)
            try:
                self._get_text_with_headers(url, headers=headers, session=session)
            except requests.RequestException:
                return

    def _find_doi_landing_abstract(self, paper: dict[str, Any], doi: str) -> dict[str, str]:
        doi_url = build_doi_url(doi)
        if not doi_url:
            return {}
        session = self._session_for()
        self._warm_up_doi_session(session, doi_url)
        headers = dict(BROWSER_HEADERS)
        headers["Referer"] = "https://doi.org/"
        try:
            html_text = self._get_text_with_headers(doi_url, headers=headers, session=session)
        except requests.RequestException as exc:
            self._debug(
                f"doi landing fetch failed doi={doi} url={doi_url} error={exc}",
                conference=paper.get("conference", ""),
                year=paper.get("year"),
                force=True,
            )
            return {}

        abstract = self._extract_abstract_from_html(html_text)
        if not abstract:
            self._debug(
                f"doi landing returned no abstract doi={doi} url={doi_url}",
                conference=paper.get("conference", ""),
                year=paper.get("year"),
            )
            return {}

        self._debug(
            f"doi landing abstract matched doi={doi} url={doi_url}",
            conference=paper.get("conference", ""),
            year=paper.get("year"),
            force=True,
        )
        return {
            "abstract": abstract,
            "abstract_source": "DOI Page",
            "doi": doi,
            "doi_url": doi_url,
        }

    def _find_source_abstract(self, paper: dict[str, Any]) -> dict[str, str]:
        url = paper.get("source_url", "")
        doi = extract_doi(url)
        if not url:
            self._debug(
                f"skip source fetch because source_url is empty title={paper['title'][:80]}",
                conference=paper.get("conference", ""),
                year=paper.get("year"),
            )
            return {}
        try:
            html_text = self._get_text(url)
        except requests.RequestException as exc:
            self._debug(
                f"source fetch failed, fallback to DOI={doi or '<none>'} url={url} error={exc}",
                conference=paper.get("conference", ""),
                year=paper.get("year"),
                force=True,
            )
            if not doi:
                return {}
            return self._find_acm_abstract_by_doi(doi)

        abstract = self._extract_abstract_from_html(html_text)
        if not abstract:
            doi = extract_doi(url)
            self._debug(
                f"source page returned no abstract, fallback DOI={doi or '<none>'} url={url}",
                conference=paper.get("conference", ""),
                year=paper.get("year"),
            )
            if doi:
                return self._find_acm_abstract_by_doi(doi)
            return {}

        self._debug(
            f"source page abstract matched url={url}",
            conference=paper.get("conference", ""),
            year=paper.get("year"),
        )
        return {
            "abstract": abstract,
            "abstract_source": "Source Page",
        }

    def _extract_ieee_metadata_abstract(self, html_text: str) -> str:
        match = re.search(r"xplGlobal\.document\.metadata\s*=\s*(\{.*?\});", html_text, flags=re.S)
        if not match:
            return ""
        try:
            metadata = json.loads(match.group(1))
        except json.JSONDecodeError:
            return ""

        text = clean_abstract_heading(clean_abstract_text(metadata.get("abstract") or ""))
        if is_probable_abstract(text):
            return text
        return ""

    def _extract_abstract_from_html(self, html_text: str) -> str:
        ieee_abstract = self._extract_ieee_metadata_abstract(html_text)
        if ieee_abstract:
            return ieee_abstract

        soup = BeautifulSoup(html_text, "html.parser")

        # Prefer site-specific content blocks before generic meta descriptions.
        selectors = [
            ".field-name-field-paper-description",
            "section#summary-abstract",
            "#summary-abstract",
            "#core-tabbed-abstracts section[role='doc-abstract']",
            "section#abstract",
            ".abstractSection",
            "#abstract",
            "[data-title='Abstract']",
        ]
        for selector in selectors:
            for node in soup.select(selector):
                paragraph_nodes = node.select("[role='paragraph'], p")
                paragraphs: list[str] = []
                for paragraph_node in paragraph_nodes:
                    text = clean_abstract_heading(clean_abstract_text(paragraph_node.get_text(" ", strip=True)))
                    if text:
                        paragraphs.append(text)
                if paragraphs:
                    text = "\n\n".join(paragraphs)
                else:
                    raw_lines = [
                        clean_abstract_heading(clean_abstract_text(line))
                        for line in node.get_text("\n", strip=True).splitlines()
                    ]
                    paragraphs = [line for line in raw_lines if line]
                    text = "\n\n".join(paragraphs)
                if is_probable_abstract(text):
                    return text

        meta_keys = {"citation_abstract", "description", "og:description", "twitter:description"}
        for meta in soup.find_all("meta"):
            key = (meta.get("name") or meta.get("property") or "").lower()
            if key not in meta_keys:
                continue
            text = clean_abstract_heading(clean_abstract_text(meta.get("content") or ""))
            if is_probable_abstract(text):
                return text

        return ""

    def _find_acm_abstract_by_doi(self, doi: str) -> dict[str, str]:
        api_url = f"https://api.crossref.org/works/{urllib.parse.quote(doi)}"
        try:
            message = self._get_json(api_url).get("message", {})
        except requests.RequestException as exc:
            self._debug(f"Crossref lookup failed doi={doi} error={exc}", force=True)
            return {}

        abstract = clean_abstract_text(strip_jats_tags(message.get("abstract") or ""))
        if not is_probable_abstract(abstract):
            self._debug(f"Crossref lookup returned no usable abstract doi={doi}")
            return {}

        self._debug(f"Crossref abstract matched doi={doi}")
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
        except requests.RequestException as exc:
            self._debug(
                f"OpenAlex lookup failed title={title[:80]} error={exc}",
                year=year,
                force=True,
            )
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
            self._debug(
                f"OpenAlex no good match title={title[:80]} best_score={best[0]:.2f}",
                year=year,
            )
            return {}

        selected = best[1]
        self._debug(
            f"OpenAlex selected score={best[0]:.2f} title={title[:80]}",
            year=year,
        )
        return {
            "abstract": clean_abstract_text(inverted_index_to_abstract(selected.get("abstract_inverted_index"))),
            "abstract_source": "OpenAlex",
            "openalex_id": selected.get("id", ""),
            "doi": extract_doi(selected.get("doi") or ""),
            "doi_url": build_doi_url(extract_doi(selected.get("doi") or "")),
        }

    def _translate_to_chinese(self, text: str) -> str:
        source = compact_spaces_preserve_paragraphs(text)
        if not source or len(source) > TRANSLATE_CHAR_LIMIT:
            return ""
        paragraphs = [part for part in re.split(r"\n\s*\n", source) if part]
        try:
            url = "https://translate.googleapis.com/translate_a/single"
            translated_paragraphs: list[str] = []
            for paragraph in paragraphs:
                response = self._session_for(translation=True).get(
                    url,
                    params={"client": "gtx", "sl": "en", "tl": "zh-CN", "dt": "t", "q": paragraph},
                    timeout=REQUEST_TIMEOUT,
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list) or not payload or not isinstance(payload[0], list):
                    return ""
                translated_parts = [part[0] for part in payload[0] if isinstance(part, list) and part and part[0]]
                translated_paragraph = compact_spaces("".join(translated_parts))
                if translated_paragraph:
                    translated_paragraphs.append(translated_paragraph)
        except (requests.RequestException, ValueError, json.JSONDecodeError):
            return ""
        return "\n\n".join(translated_paragraphs)


REPOSITORY = PaperRepository(CACHE_DIR)


def print_summary(summary: dict[str, int]) -> None:
    table = PrettyTable()
    table.field_names = ["Conference", "Papers Cached"]
    table.align["Conference"] = "l"
    table.align["Papers Cached"] = "r"
    for conference in sorted(summary):
        table.add_row([conference, summary[conference]])
    print(table)


def main() -> None:
    parser = argparse.ArgumentParser(description="Conference paper cache builder and static JSON exporter")
    parser.add_argument(
        "command",
        nargs="?",
        default="build-cache",
        choices=["build-cache", "build-static"],
        help=(
            "build-cache: build missing cache and export static data; "
            "build-static: export static JSON from existing cache"
        ),
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug logs for build-cache stages, requests, and abstract fetching.",
    )
    parser.add_argument(
        "--debug-filter",
        action="append",
        default=[],
        help="Only emit debug logs whose context or message contains this token. Repeatable.",
    )
    parser.add_argument(
        "--conference",
        action="append",
        default=[],
        help="Only update the specified conference/journal key. Repeatable or comma-separated.",
    )
    parser.add_argument(
        "--year",
        action="append",
        default=[],
        help="Only update the specified year. Repeatable or comma-separated, e.g. --year 2025,2024.",
    )
    args = parser.parse_args()
    env_debug = os.environ.get("BUILD_CACHE_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
    env_filters = [value.strip() for value in os.environ.get("BUILD_CACHE_DEBUG_FILTER", "").split(",") if value.strip()]
    REPOSITORY.configure_debug(args.debug or env_debug, [*env_filters, *args.debug_filter])
    selected_conferences = parse_conference_filters(args.conference)
    selected_years = parse_year_filters(args.year)

    if args.command == "build-cache":
        print("Building missing cache data...")
        summary = REPOSITORY.build_cache(
            conferences=selected_conferences or None,
            force_refresh=False,
            show_progress=True,
            years=selected_years or None,
        )
        print_summary(summary)
        print(f"static data exported to {STATIC_DATA_PATH}")
        print("\nopen assets/index.html in a browser to view the papers list")
        return

    if not REPOSITORY.has_usable_cache():
        raise SystemExit("No usable cache found. Run `python3 build-cache.py build-cache` first.")
    output_path = REPOSITORY.export_static_data()
    print(f"static data exported to {output_path}")
    print("\nopen assets/index.html in a browser to view the papers list")


if __name__ == "__main__":
    # print running start time (YY:MM:DD HH:MM:SS) for easier debugging of cache build duration and potential timeouts.
    run_start_time = datetime.datetime.now()
    print(f"Script started at {run_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    main()
