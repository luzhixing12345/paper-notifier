#!/usr/bin/env python3

import argparse
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup


BASE_DIR = Path(__file__).resolve().parent
CONFERENCE_CONFIG_PATH = BASE_DIR / "CONFERENCE.txt"
JOURNAL_CONFIG_PATH = BASE_DIR / "JOURNAL.txt"
USER_AGENT = "paper-abstract-venue-resolver/1.0"
REQUEST_TIMEOUT = 20
DEFAULT_LOOKBACK_YEARS = 5

SPECIAL_LABELS = {
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


def format_venue_label(key: str) -> str:
    return SPECIAL_LABELS.get(key, key.upper())


def load_venue_config(config_path: Path, venue_kind: str) -> tuple[int, dict[str, dict[str, str]]]:
    if not config_path.exists():
        if venue_kind == "journals":
            return DEFAULT_LOOKBACK_YEARS, {}
        raise FileNotFoundError(f"Venue config not found: {config_path}")

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
            continue

        key = line.lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", key):
            raise ValueError(f"Invalid venue key on line {line_number}: {line}")
        venues[key] = {
            "label": format_venue_label(key),
            "dblp_slug": key,
            "venue_kind": venue_kind,
            "lookback_years": str(lookback_years),
        }
    return lookback_years, venues


_, CONFERENCES = load_venue_config(CONFERENCE_CONFIG_PATH, "conf")
_, JOURNALS = load_venue_config(JOURNAL_CONFIG_PATH, "journals")
VENUES = {**CONFERENCES, **JOURNALS}


def build_slug_candidates(name: str) -> list[str]:
    normalized = name.strip().lower()
    compact = re.sub(r"[^a-z0-9]+", "", normalized)
    dashed = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    underscored = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    candidates = []
    for candidate in [normalized, compact, dashed, underscored]:
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def probe_dblp_slug(slug: str, venue_kind: str, session: requests.Session) -> dict[str, str] | None:
    url = f"https://dblp.org/db/{venue_kind}/{slug}/index.html"
    response = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
    if response.status_code != 200:
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    if "dblp" not in title.lower():
        return None

    heading = ""
    h1 = soup.find("h1")
    if h1:
        heading = h1.get_text(" ", strip=True)

    return {
        "dblp_slug": slug,
        "venue_kind": venue_kind,
        "url": response.url,
        "page_title": title,
        "heading": heading,
    }


def print_config_status(key: str) -> None:
    if key in CONFERENCES:
        print("configured: yes")
        print("configured_kind: conf")
        print(f"configured_label: {CONFERENCES[key]['label']}")
        return
    if key in JOURNALS:
        print("configured: yes")
        print("configured_kind: journals")
        print(f"configured_label: {JOURNALS[key]['label']}")
        return
    print("configured: no")


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve a conference/journal key and check whether it exists")
    parser.add_argument("name", help="Venue key or informal name, for example: hotos / tcad")
    parser.add_argument(
        "--kind",
        choices=["auto", "conf", "journals"],
        default="auto",
        help="Restrict lookup to conferences or journals; default is auto",
    )
    args = parser.parse_args()

    key = re.sub(r"[^a-z0-9]+", "", args.name.lower())
    if not key:
        raise SystemExit("Venue name is empty after normalization.")

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    candidates = build_slug_candidates(args.name)
    kinds = ["conf", "journals"] if args.kind == "auto" else [args.kind]

    matches = []
    for venue_kind in kinds:
        for slug in candidates:
            try:
                result = probe_dblp_slug(slug, venue_kind, session)
            except requests.RequestException:
                result = None
            if result:
                matches.append(result)

    print(f"input_key: {key}")
    print(f"suggested_label: {format_venue_label(key)}")
    print_config_status(key)
    print("slug_candidates:")
    for slug in candidates:
        print(f"  - {slug}")

    if not matches:
        print("exists_on_dblp: no")
        print("resolved_kind: <not found>")
        print("resolved_dblp_slug: <not found>")
        print("tip: try another spelling or open https://dblp.org/db/conf/ or https://dblp.org/db/journals/ manually.")
        return

    best = matches[0]
    print("exists_on_dblp: yes")
    print(f"resolved_kind: {best['venue_kind']}")
    print(f"resolved_dblp_slug: {best['dblp_slug']}")
    print(f"dblp_url: {best['url']}")
    if best["page_title"]:
        print(f"page_title: {best['page_title']}")
    if best["heading"]:
        print(f"heading: {best['heading']}")
    print(
        "suggested_mapping: "
        + "{"
        + f"'label': '{format_venue_label(key)}', 'dblp_slug': '{best['dblp_slug']}', 'venue_kind': '{best['venue_kind']}'"
        + "}"
    )


if __name__ == "__main__":
    main()
