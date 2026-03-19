import argparse
import json
import re
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from bs4 import BeautifulSoup


BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "paper_cache"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 20
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


def compact_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def clean_abstract_heading(value: str) -> str:
    text = value.strip()
    text = re.sub(r"^\s*abstract\s*[:\-]?\s*", "", text, flags=re.I)
    return text.strip()


def is_probable_abstract(text: str) -> bool:
    normalized = compact_spaces(text)
    if len(normalized) < 120:
        return False
    lowered = normalized.lower()
    blocked_fragments = [
        "just a moment",
        "enable javascript and cookies",
        "page not found",
        "javascript is disabled",
        "skip to main content",
        "access provided by",
        "you do not have access",
    ]
    return not any(fragment in lowered for fragment in blocked_fragments)


def extract_abstract_from_html(html_text: str) -> str:
    soup = BeautifulSoup(html_text, "html.parser")
    selectors = [
        ".field-name-field-paper-description",
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
                text = clean_abstract_heading(compact_spaces(paragraph_node.get_text(" ", strip=True)))
                if text:
                    paragraphs.append(text)
            if paragraphs:
                text = "\n\n".join(paragraphs)
            else:
                raw_lines = [clean_abstract_heading(compact_spaces(line)) for line in node.get_text("\n", strip=True).splitlines()]
                paragraphs = [line for line in raw_lines if line]
                text = "\n\n".join(paragraphs)
            if is_probable_abstract(text):
                return text

    meta_keys = {"citation_abstract", "description", "og:description", "twitter:description"}
    for meta in soup.find_all("meta"):
        key = (meta.get("name") or meta.get("property") or "").lower()
        if key not in meta_keys:
            continue
        text = clean_abstract_heading(compact_spaces(meta.get("content") or ""))
        if is_probable_abstract(text):
            return text
    return ""


def load_papers(conference: str, year: int) -> list[dict[str, Any]]:
    info_path = CACHE_DIR / str(year) / conference / "info.json"
    payload = json.loads(info_path.read_text(encoding="utf-8"))
    return payload.get("items", [])


def build_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = True
    session.headers.clear()
    session.headers.update(BROWSER_HEADERS)
    adapter = HTTPAdapter(pool_connections=1, pool_maxsize=1, max_retries=0)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def warm_up_session(session: requests.Session, doi_url: str) -> None:
    steps = [
        ("https://doi.org/", {"Sec-Fetch-Site": "none", "Referer": ""}),
        (doi_url, {"Sec-Fetch-Site": "same-origin", "Referer": "https://doi.org/"}),
    ]
    for url, overrides in steps:
        headers = dict(BROWSER_HEADERS)
        headers.update(overrides)
        try:
            session.get(url, timeout=REQUEST_TIMEOUT, headers=headers, allow_redirects=True)
        except requests.RequestException:
            return


def fetch_doi_page(session: requests.Session, doi_url: str) -> dict[str, Any]:
    headers = dict(BROWSER_HEADERS)
    headers["Referer"] = "https://doi.org/"
    try:
        response = session.get(doi_url, timeout=REQUEST_TIMEOUT, allow_redirects=True, headers=headers)
        response.raise_for_status()
    except requests.RequestException as exc:
        response = getattr(exc, "response", None)
        text = response.text if response is not None and isinstance(response.text, str) else ""
        final_url = response.url if response is not None else doi_url
        status_code = response.status_code if response is not None else None
        return {
            "ok": False,
            "status_code": status_code,
            "final_url": final_url,
            "blocked": looks_blocked(text, final_url, status_code),
            "error": str(exc),
            "html_abstract": "",
        }

    html_abstract = extract_abstract_from_html(response.text)
    blocked = looks_blocked(response.text, response.url, response.status_code)
    if html_abstract:
        print(html_abstract)
        blocked = False
    return {
        "ok": True,
        "status_code": response.status_code,
        "final_url": response.url,
        "blocked": blocked,
        "error": "",
        "html_abstract": html_abstract,
    }


def looks_blocked(html_text: str, final_url: str, status_code: int | None) -> bool:
    lowered = (html_text or "").lower()
    return bool(
        status_code in {401, 403}
        or "just a moment" in lowered
        or "enable javascript and cookies" in lowered
        or "challenge-platform" in lowered
        or "cf-browser-verification" in lowered
        or "dl.acm.org" in final_url and "captcha" in lowered
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify whether DOI landing pages expose full abstracts in batch.")
    parser.add_argument("--conference", required=True, help="Conference key, e.g. asplos")
    parser.add_argument("--year", required=True, type=int, help="Year, e.g. 2023")
    parser.add_argument("--limit", type=int, default=10, help="Max number of papers to verify")
    parser.add_argument("--contains", default="", help="Only test titles containing this substring")
    parser.add_argument("--output", default="", help="Optional JSON output path")
    args = parser.parse_args()

    papers = load_papers(args.conference, args.year)
    targets = [paper for paper in papers if paper.get("doi_url")]
    if args.contains:
        needle = args.contains.lower()
        targets = [paper for paper in targets if needle in paper.get("title", "").lower()]
    if args.limit > 0:
        targets = targets[:args.limit]

    session = build_session()

    results: list[dict[str, Any]] = []
    for index, paper in enumerate(targets, start=1):
        doi_url = paper.get("doi_url", "")
        warm_up_session(session, doi_url)
        result = fetch_doi_page(session, doi_url)
        cached_abstract = compact_spaces(paper.get("abstract", ""))
        html_abstract = result["html_abstract"].strip()
        row = {
            "index": index,
            "title": paper.get("title", ""),
            "doi": paper.get("doi", ""),
            "doi_url": doi_url,
            "openalex_id": paper.get("openalex_id", ""),
            "cached_abstract_chars": len(cached_abstract),
            "html_abstract_chars": len(html_abstract),
            "html_longer_than_cached": len(html_abstract) > len(cached_abstract),
            "status_code": result["status_code"],
            "blocked": result["blocked"],
            "ok": result["ok"],
            "final_url": result["final_url"],
            "error": result["error"],
            "cached_abstract_preview": cached_abstract[:240],
            "html_abstract_preview": html_abstract[:240],
        }
        results.append(row)
        print(
            json.dumps(
                {
                    "index": row["index"],
                    "title": row["title"],
                    "status_code": row["status_code"],
                    "blocked": row["blocked"],
                    "html_chars": row["html_abstract_chars"],
                    "cached_chars": row["cached_abstract_chars"],
                    "html_longer_than_cached": row["html_longer_than_cached"],
                    "final_url": row["final_url"],
                    "error": row["error"],
                },
                ensure_ascii=False,
            )
        )

    summary = {
        "tested": len(results),
        "success": sum(1 for row in results if row["ok"]),
        "blocked": sum(1 for row in results if row["blocked"]),
        "html_abstract_found": sum(1 for row in results if row["html_abstract_chars"] > 0),
        "html_longer_than_cached": sum(1 for row in results if row["html_longer_than_cached"]),
    }
    print(json.dumps({"summary": summary}, ensure_ascii=False, indent=2))

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

# python3 verify_doi_abstracts.py --conference asplos --year 2024 --contains 'Performance-aware Scale Analysis with Reserve for Homomorphic Encryption.' --limit 1