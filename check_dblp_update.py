import argparse
import datetime
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import e2me
import requests
from bs4 import BeautifulSoup


BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "paper_cache"
CONFERENCE_CONFIG_PATH = BASE_DIR / "CONFERENCE.txt"
JOURNAL_CONFIG_PATH = BASE_DIR / "JOURNAL.txt"
DEFAULT_LOOKBACK_YEARS = 5
REQUEST_TIMEOUT = 10
REQUEST_RETRIES = 4
RETRY_BACKOFF_SECONDS = 1.5
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36"
)


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


REQUEST_TIMEOUT = env_int("CHECK_DBLP_REQUEST_TIMEOUT", REQUEST_TIMEOUT)


def current_year() -> int:
    return time.localtime().tm_year


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


def load_venue_config(config_path: Path, venue_kind: str) -> tuple[int, dict[str, dict[str, Any]]]:
    if not config_path.exists():
        if venue_kind == "journals":
            return DEFAULT_LOOKBACK_YEARS, {}
        raise FileNotFoundError(f"Conference config not found: {config_path}")

    lookback_years = DEFAULT_LOOKBACK_YEARS
    venues: dict[str, dict[str, Any]] = {}
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
VENUES = {**CONFERENCES, **JOURNALS}


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


def load_cached_conference_years() -> dict[str, dict[str, Any]]:
    metadata_path = CACHE_DIR / "metadata.json"
    if not metadata_path.exists():
        return {}
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    conference_years = payload.get("conference_years", {})
    return conference_years if isinstance(conference_years, dict) else {}


def paper_info_path(venue: str, year: int) -> Path:
    return CACHE_DIR / str(year) / venue / "info.json"


class DblpUpdateChecker:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.cached_conference_years = load_cached_conference_years()

    def _get_text(self, url: str) -> str:
        last_error: requests.RequestException | None = None
        for attempt in range(REQUEST_RETRIES + 1):
            try:
                response = self.session.get(url, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
                return response.text
            except requests.RequestException as exc:
                last_error = exc
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                if attempt >= REQUEST_RETRIES or status_code in {401, 403, 404}:
                    break
                time.sleep(RETRY_BACKOFF_SECONDS * (2**attempt))
        assert last_error is not None
        raise last_error

    def fetch_year_entries(self, venue_key: str) -> dict[int, list[str]]:
        venue = VENUES[venue_key]
        slug = venue["dblp_slug"]
        venue_kind = venue["venue_kind"]
        url = f"https://dblp.org/db/{venue_kind}/{slug}/index.html"
        html_text = self._get_text(url)
        if venue_kind == "journals":
            return self._extract_journal_year_entries(html_text, slug)
        return self._extract_conf_year_entries(html_text, slug)

    def _extract_conf_year_entries(self, html_text: str, slug: str) -> dict[int, list[str]]:
        entries: dict[int, set[str]] = {}
        soup = BeautifulSoup(html_text, "html.parser")

        toc_pattern = re.compile(rf"/db/conf/{re.escape(slug)}/([^/?#]+)\.html(?:$|[?#])")
        for anchor in soup.find_all("a", href=True):
            match = toc_pattern.search(anchor["href"])
            if not match:
                continue
            entry_name = match.group(1)
            if not is_valid_conf_entry_name(entry_name, slug):
                continue
            year_match = re.search(r"(19|20)\d{2}", entry_name)
            if not year_match:
                continue
            year = int(year_match.group(0))
            if year <= current_year():
                entries.setdefault(year, set()).add(entry_name)

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
                if year <= current_year():
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
            if year <= current_year():
                entries.setdefault(year, set()).add(entry_name)
        return {year: sorted(names) for year, names in entries.items()}

    def find_updates(
        self,
        selected_venues: list[str] | None = None,
        show_progress: bool = True,
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        updates: list[dict[str, Any]] = []
        failures: dict[str, str] = {}
        venue_keys = selected_venues or list(VENUES)

        for index, venue_key in enumerate(venue_keys, start=1):
            venue = VENUES[venue_key]
            if show_progress:
                print(f"[conference {index}/{len(venue_keys)}] {venue['label']} ({venue_key})")
            try:
                remote_entries_by_year = self.fetch_year_entries(venue_key)
            except requests.RequestException as exc:
                failures[venue_key] = str(exc)
                if show_progress:
                    print(f"[{venue['label']}] DBLP check failed: {exc}")
                continue

            lookback_years = int(venue["lookback_years"])
            min_year = current_year() - lookback_years + 1
            remote_years = [
                year
                for year in sorted(remote_entries_by_year, reverse=True)
                if year >= min_year
            ][:lookback_years]
            if show_progress:
                print(f"[{venue['label']}] years={remote_years}")

            cached = self.cached_conference_years.get(venue_key, {})
            cached_year_entries = cached.get("year_entries", {}) if isinstance(cached, dict) else {}
            for year in remote_years:
                remote_entries = remote_entries_by_year.get(year, [])
                cached_entries = cached_year_entries.get(str(year), []) if isinstance(cached_year_entries, dict) else []
                missing_entries = sorted(set(remote_entries) - set(cached_entries))
                has_cached_papers = paper_info_path(venue_key, year).exists()
                if missing_entries or not has_cached_papers:
                    if show_progress:
                        entry_text = ", ".join(missing_entries) if missing_entries else "metadata already listed"
                        cache_text = "cached" if has_cached_papers else "missing cache"
                        print(f"[{venue['label']} {year}] update found: {entry_text}; {cache_text}")
                    updates.append(
                        {
                            "venue": venue_key,
                            "label": venue["label"],
                            "kind": venue["venue_kind"],
                            "year": year,
                            "missing_entries": missing_entries,
                            "remote_entries": remote_entries,
                            "has_cached_papers": has_cached_papers,
                            "dblp_url": f"https://dblp.org/db/{venue['venue_kind']}/{venue['dblp_slug']}/index.html",
                        }
                    )

        updates.sort(key=lambda item: (item["kind"], item["venue"], -item["year"]))
        return updates, failures


def parse_venue_filters(values: list[str] | None) -> list[str]:
    if not values:
        return []
    selected: list[str] = []
    for value in values:
        for part in value.split(","):
            venue = part.strip().lower()
            if not venue:
                continue
            if venue not in VENUES:
                raise SystemExit(f"Unknown conference/journal: {venue}")
            if venue not in selected:
                selected.append(venue)
    return selected


def format_email_subject(updates: list[dict[str, Any]]) -> str:
    if len(updates) == 1:
        item = updates[0]
        return f"Paper Notifier: DBLP update for {item['label']} {item['year']}"
    return f"Paper Notifier: {len(updates)} DBLP venue-year updates"


def format_email_body(updates: list[dict[str, Any]], failures: dict[str, str]) -> str:
    lines = [
        f"DBLP update check found {len(updates)} venue-year update(s).",
        "",
    ]
    for item in updates:
        entry_text = ", ".join(item["missing_entries"]) if item["missing_entries"] else "metadata already listed"
        cache_text = "yes" if item["has_cached_papers"] else "no"
        lines.extend(
            [
                f"{item['label']} ({item['venue']}) {item['year']}",
                f"- DBLP entries: {entry_text}",
                f"- local paper cache exists: {cache_text}",
                f"- DBLP: {item['dblp_url']}",
                f"- build locally: python3 build-cache.py --conference {item['venue']} --year {item['year']}",
                "",
            ]
        )

    if failures:
        lines.append("Failed DBLP checks:")
        for venue in sorted(failures):
            lines.append(f"- {venue}: {failures[venue]}")
        lines.append("")

    lines.append("visit paper for details: https://paper-notifier.vercel.app/")
    return "\n".join(lines).strip()


def send_email(subject: str, body: str) -> None:
    e2me.send_email(subject, body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Check DBLP for new configured conference/journal updates.")
    parser.add_argument(
        "--conference",
        action="append",
        default=[],
        help="Only check the specified conference/journal key. Repeatable or comma-separated.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print detected updates without sending email.",
    )
    args = parser.parse_args()

    selected_venues = parse_venue_filters(args.conference)
    checker = DblpUpdateChecker()
    updates, failures = checker.find_updates(selected_venues or None)

    if not updates:
        print("No DBLP updates found.")
        if failures:
            print("Failed DBLP checks:")
            for venue in sorted(failures):
                print(f"- {venue}: {failures[venue]}")
        return

    print(f"DBLP updates found: {len(updates)}")
    for item in updates:
        entries = ", ".join(item["missing_entries"]) if item["missing_entries"] else "metadata already listed"
        cache_text = "cached" if item["has_cached_papers"] else "missing cache"
        print(f"- {item['label']} ({item['venue']}) {item['year']}: {entries}; {cache_text}")

    subject = format_email_subject(updates)
    body = format_email_body(updates, failures)
    if args.dry_run:
        print("")
        print(subject)
        print(body)
        return
    send_email(subject, body)
    print("Notification email sent.")


if __name__ == "__main__":
    run_start_time = datetime.datetime.now()
    print(f"Script started at {run_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    main()
