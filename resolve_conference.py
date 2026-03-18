#!/usr/bin/env python3

import argparse
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup


USER_AGENT = "paper-abstract-conference-resolver/1.0"
REQUEST_TIMEOUT = 20

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
}


def format_conference_label(key: str) -> str:
    return SPECIAL_LABELS.get(key, key.upper())


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


def probe_dblp_slug(slug: str, session: requests.Session) -> dict[str, str] | None:
    url = f"https://dblp.org/db/conf/{slug}/index.html"
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
        "url": response.url,
        "page_title": title,
        "heading": heading,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve a conference key to a suggested label and DBLP slug")
    parser.add_argument("name", help="Conference key or informal name, for example: hotos")
    args = parser.parse_args()

    key = re.sub(r"[^a-z0-9]+", "", args.name.lower())
    if not key:
        raise SystemExit("Conference name is empty after normalization.")

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    candidates = build_slug_candidates(args.name)
    matches = []
    for slug in candidates:
        try:
            result = probe_dblp_slug(slug, session)
        except requests.RequestException:
            result = None
        if result:
            matches.append(result)

    print(f"input_key: {key}")
    print(f"suggested_label: {format_conference_label(key)}")
    print("slug_candidates:")
    for slug in candidates:
        print(f"  - {slug}")

    if not matches:
        print("resolved_dblp_slug: <not found>")
        print("tip: try another spelling or open https://dblp.org/db/conf/ manually.")
        return

    best = matches[0]
    print(f"resolved_dblp_slug: {best['dblp_slug']}")
    print(f"dblp_url: {best['url']}")
    if best["page_title"]:
        print(f"page_title: {best['page_title']}")
    if best["heading"]:
        print(f"heading: {best['heading']}")
    print(f"suggested_mapping: {{'label': '{format_conference_label(key)}', 'dblp_slug': '{best['dblp_slug']}'}}")


if __name__ == "__main__":
    main()
