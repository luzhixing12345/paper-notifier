import argparse
import json
import re
import time
import urllib.parse
from html import unescape
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 20
SEMANTIC_SCHOLAR_RETRIES = 2
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


def extract_doi(url: str) -> str:
    if not url:
        return ""
    match = re.search(r"10\.\d{4,9}/[-._;()/:a-z0-9]+", url, flags=re.I)
    return match.group(0) if match else ""


def strip_jats_tags(value: str) -> str:
    value = re.sub(r"</?(jats:)?(p|i|b|sup|sub|sc|italic|bold)>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return compact_spaces(unescape(value))


def clean_abstract_text(value: str) -> str:
    paragraphs: list[str] = []
    for part in re.split(r"\n\s*\n", value or ""):
        cleaned = strip_jats_tags(part)
        if cleaned:
            paragraphs.append(cleaned)
    return "\n\n".join(paragraphs)


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
        or ('challenge-platform' in lowered and 'cf-browser-verification' in lowered)
        or "cf-browser-verification" in lowered
        or "dl.acm.org" in final_url and "captcha" in lowered
    )


def get_json(session: requests.Session, url: str) -> dict[str, Any]:
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"Unexpected JSON payload type for {url}: {type(payload).__name__}")
    return payload


def parse_retry_after_seconds(value: str) -> float:
    text = compact_spaces(value)
    if not text:
        return 0.0
    try:
        return max(0.0, float(text))
    except ValueError:
        return 0.0


def fetch_semantic_scholar_abstract(session: requests.Session, doi: str) -> dict[str, Any]:
    if not doi:
        return {
            "ok": False,
            "source": "Semantic Scholar",
            "error": "missing doi",
            "paper_id": "",
            "title": "",
            "year": None,
            "abstract": "",
        }

    api_url = (
        "https://api.semanticscholar.org/graph/v1/paper/"
        f"DOI:{urllib.parse.quote(doi, safe='')}?fields=title,abstract,year,externalIds,url"
    )
    last_error = ""
    for _ in range(SEMANTIC_SCHOLAR_RETRIES + 1):
        try:
            payload = get_json(session, api_url)
            break
        except requests.HTTPError as exc:
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", None)
            if status_code == 429:
                retry_after = parse_retry_after_seconds((response.headers or {}).get("Retry-After", ""))
                wait_seconds = retry_after if retry_after > 0 else 2.0
                last_error = (
                    f"{status_code} Client Error: rate limited by Semantic Scholar"
                    f"{f', retrying after {wait_seconds:.0f}s' if wait_seconds else ''}"
                )
                time.sleep(wait_seconds)
                continue
            last_error = str(exc)
        except Exception as exc:
            last_error = str(exc)
    else:
        return {
            "ok": False,
            "source": "Semantic Scholar",
            "error": last_error,
            "paper_id": "",
            "title": "",
            "year": None,
            "abstract": "",
        }

    abstract = clean_abstract_text(payload.get("abstract") or "")
    abstract_ok = is_probable_abstract(abstract)
    paper_url = payload.get("url") or ""
    paper_id = paper_url.rstrip("/").split("/")[-1] if paper_url else ""
    return {
        "ok": abstract_ok,
        "source": "Semantic Scholar",
        "error": "" if abstract_ok else "Semantic Scholar returned no usable abstract",
        "paper_id": paper_id,
        "title": payload.get("title") or "",
        "year": payload.get("year"),
        "abstract": abstract if abstract_ok else "",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify whether a DOI landing page exposes a full abstract.")
    parser.add_argument("doi_url", help="DOI URL, e.g. https://doi.org/10.1145/3575693.3575714")
    parser.add_argument("--output", default="", help="Optional JSON output path")
    args = parser.parse_args()

    session = build_session()
    warm_up_session(session, args.doi_url)
    doi_result = fetch_doi_page(session, args.doi_url)
    html_abstract = doi_result["html_abstract"].strip()
    doi = extract_doi(args.doi_url)
    semantic_scholar_result = {
        "ok": False,
        "source": "Semantic Scholar",
        "error": "not attempted",
        "paper_id": "",
        "title": "",
        "year": None,
        "abstract": "",
    }
    if not html_abstract:
        semantic_scholar_result = fetch_semantic_scholar_abstract(session, doi)

    payload = {
        "doi_url": args.doi_url,
        "doi": doi,
        "doi_page": {
            "status_code": doi_result["status_code"],
            "blocked": doi_result["blocked"],
            "ok": doi_result["ok"],
            "final_url": doi_result["final_url"],
            "error": doi_result["error"],
            "html_abstract_chars": len(html_abstract),
            "html_abstract_preview": html_abstract[:240],
            "html_abstract": html_abstract,
        },
        "semantic_scholar": {
            "ok": semantic_scholar_result["ok"],
            "source": semantic_scholar_result["source"],
            "error": semantic_scholar_result["error"],
            "paper_id": semantic_scholar_result["paper_id"],
            "title": semantic_scholar_result["title"],
            "year": semantic_scholar_result["year"],
            "abstract_chars": len(semantic_scholar_result["abstract"]),
            "abstract_preview": semantic_scholar_result["abstract"][:240],
            "abstract": semantic_scholar_result["abstract"],
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
