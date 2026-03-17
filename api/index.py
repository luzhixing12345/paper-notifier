import json
import mimetypes
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from app import ASSETS_DIR, CONFERENCES, REPOSITORY


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        route_path = params.get("path", [parsed.path])[0]

        if route_path == "/":
            self._send_asset("index.html")
            return
        if route_path.startswith("/assets/"):
            self._send_asset(route_path.removeprefix("/assets/"))
            return
        if route_path == "/api/papers":
            self._handle_api_papers(params)
            return
        self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

    def _handle_api_papers(self, params: dict[str, list[str]]) -> None:
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
                    "detail": "Build the local cache before deploying to Vercel.",
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
        elif asset_path.suffix == ".svg":
            content_type = "image/svg+xml"

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
