import json
import mimetypes
import urllib.parse
from pathlib import Path
from typing import Callable, Iterable

from app import ASSETS_DIR, CONFERENCES, REPOSITORY


def json_response(status: str, payload: dict) -> tuple[str, list[tuple[str, str]], list[bytes]]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
    ]
    return status, headers, [body]


def asset_response(asset_name: str) -> tuple[str, list[tuple[str, str]], list[bytes]]:
    assets_root = ASSETS_DIR.resolve()
    asset_path = (ASSETS_DIR / asset_name).resolve()
    if assets_root not in asset_path.parents and asset_path != assets_root:
        return json_response("400 Bad Request", {"error": "Invalid asset path"})
    if not asset_path.exists() or not asset_path.is_file():
        return json_response("404 Not Found", {"error": "Asset not found"})

    payload = asset_path.read_bytes()
    content_type, _ = mimetypes.guess_type(str(asset_path))
    if asset_path.suffix == ".js":
        content_type = "application/javascript; charset=utf-8"
    elif asset_path.suffix == ".css":
        content_type = "text/css; charset=utf-8"
    elif asset_path.suffix == ".html":
        content_type = "text/html; charset=utf-8"
    elif asset_path.suffix == ".svg":
        content_type = "image/svg+xml"

    headers = [
        ("Content-Type", content_type or "application/octet-stream"),
        ("Content-Length", str(len(payload))),
        ("Cache-Control", "no-store"),
    ]
    return "200 OK", headers, [payload]


def papers_response(query_string: str) -> tuple[str, list[tuple[str, str]], list[bytes]]:
    params = urllib.parse.parse_qs(query_string)
    conference = params.get("conference", ["osdi"])[0].lower()
    if conference not in CONFERENCES:
        return json_response("400 Bad Request", {"error": f"Unsupported conference: {conference}"})

    papers = REPOSITORY.get_cached_papers(conference)
    years = sorted({paper["year"] for paper in papers}, reverse=True)
    if not papers:
        return json_response(
            "503 Service Unavailable",
            {
                "error": "No cached data available.",
                "detail": "Build the local cache before deploying to Vercel.",
            },
        )

    return json_response(
        "200 OK",
        {
            "conference": conference,
            "conference_label": CONFERENCES[conference]["label"],
            "available_years": years,
            "count": len(papers),
            "papers": papers,
        },
    )


def app(environ: dict, start_response: Callable) -> Iterable[bytes]:
    path = environ.get("PATH_INFO", "/")
    query_string = environ.get("QUERY_STRING", "")
    params = urllib.parse.parse_qs(query_string)
    route_path = params.get("path", [path])[0]

    if route_path == "/":
        status, headers, body = asset_response("index.html")
    elif route_path.startswith("/assets/"):
        status, headers, body = asset_response(route_path.removeprefix("/assets/"))
    elif route_path == "/api/papers":
        status, headers, body = papers_response(query_string)
    else:
        status, headers, body = json_response("404 Not Found", {"error": "Not found"})

    start_response(status, headers)
    return body
