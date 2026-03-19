import argparse
import json
import re
from pathlib import Path
from typing import Any
import json
import requests
from requests.adapters import HTTPAdapter
from bs4 import BeautifulSoup

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
        "you do not have access",
        "purchase this article",
        "access provided by",
        "accept & close",
    ]
    return not any(fragment in lowered for fragment in blocked_fragments)


def extract_ieee_metadata_abstract(html_text: str) -> str:
    match = re.search(r"xplGlobal\.document\.metadata\s*=\s*(\{.*?\});", html_text, flags=re.S)
    if not match:
        return ""
    try:
        metadata = json.loads(match.group(1))
    except json.JSONDecodeError:
        return ""
    text = clean_abstract_heading(compact_spaces(metadata.get("abstract") or ""))
    if is_probable_abstract(text):
        return text
    return ""


def extract_abstract_from_html(html_text: str) -> str:
    metadata_abstract = extract_ieee_metadata_abstract(html_text)
    if metadata_abstract:
        return metadata_abstract

    soup = BeautifulSoup(html_text, "html.parser")
    selectors = [
        "div.abstract-text",
        "section.abstract-text",
        ".document-abstract .abstract-text",
        ".abstract-desktop-div .abstract-text",
        ".abstract-mobile-div .abstract-text",
        "[class*='abstract-text']",
        "meta[name='citation_abstract']",
    ]

    for selector in selectors:
        for node in soup.select(selector):
            if node.name == "meta":
                text = clean_abstract_heading(compact_spaces(node.get("content") or ""))
                if is_probable_abstract(text):
                    return text
                continue

            paragraph_nodes = node.select("div, p")
            paragraphs: list[str] = []
            for paragraph_node in paragraph_nodes:
                text = clean_abstract_heading(compact_spaces(paragraph_node.get_text(" ", strip=True)))
                if text and is_probable_abstract(text):
                    paragraphs.append(text)
            if paragraphs:
                return "\n\n".join(paragraphs)

            text = clean_abstract_heading(compact_spaces(node.get_text(" ", strip=True)))
            if is_probable_abstract(text):
                return text

    meta_keys = {"description", "og:description", "twitter:description"}
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


def fetch_ieee_page(session: requests.Session, doi_url: str) -> dict[str, Any]:
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
        or "cf-browser-verification" in lowered
        or ("ieeexplore.ieee.org" in final_url and "purchase this article" in lowered and "abstract" not in lowered)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify whether an IEEE DOI landing page exposes an abstract.")
    parser.add_argument("doi_url", help="DOI URL, e.g. https://doi.org/10.1109/HPCA56546.2023.10070953")
    parser.add_argument("--output", default="", help="Optional JSON output path")
    args = parser.parse_args()

    session = build_session()
    warm_up_session(session, args.doi_url)
    result = fetch_ieee_page(session, args.doi_url)
    html_abstract = result["html_abstract"].strip()
    payload = {
        "doi_url": args.doi_url,
        "status_code": result["status_code"],
        "blocked": result["blocked"],
        "ok": result["ok"],
        "final_url": result["final_url"],
        "error": result["error"],
        "html_abstract_chars": len(html_abstract),
        "html_abstract_preview": html_abstract[:240],
        "html_abstract": html_abstract,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
