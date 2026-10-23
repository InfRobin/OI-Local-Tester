"""本地 HTTP 服务：静态资源 + JSON API。"""

from __future__ import annotations

import json
import mimetypes
import posixpath
import secrets
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

from . import __version__
from .paths import WEB_DIR
from .session import Session, SessionError


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class Context:
    def __init__(self, session: Session, token: str) -> None:
        self.session = session
        self.token = token
        self.started_at = time.time()
        self.last_ping = time.time()
        self.shutdown_cb = None

    def touch(self) -> None:
        self.last_ping = time.time()


class TesterServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, context: Context) -> None:
        self.ctx = context
        super().__init__(address, handler)


MIME_OVERRIDES = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
    ".woff2": "font/woff2",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "OITester"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------------ #
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        pass

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, b"", "text/plain")

    # ------------------------------------------------------------------ #
    def _dispatch(self, method: str) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        query = urllib.parse.parse_qs(parsed.query)

        if not path.startswith("/api/"):
            if method != "GET":
                self._send_json({"error": "方法不允许"}, 405)
                return
            self._serve_static(path)
            return

        if not self._authorized(query):
            self._send_json({"error": "访问令牌无效"}, 401)
            return

        try:
            payload: Dict[str, Any] = {}
            if method == "POST":
                payload = self._read_body()
            result = self._api(method, path, query, payload)
            if result is not None:
                self._send_json(result)
        except ApiError as exc:
            self._send_json({"error": str(exc)}, exc.status)
        except SessionError as exc:
            self._send_json({"error": str(exc)}, 400)
        except FileNotFoundError as exc:
            self._send_json({"error": str(exc)}, 404)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._send_json({"error": f"服务器内部错误：{exc}"}, 500)

    # ------------------------------------------------------------------ #
    def _authorized(self, query: Dict[str, list]) -> bool:
        token = (query.get("k") or [None])[0] or self.headers.get("X-Token")
        expected = self.server.ctx.token
        return bool(token) and secrets.compare_digest(str(token), expected)

    def _read_body(self) -> Dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if len(raw) > 4 * 1024 * 1024:
            raise ApiError("请求体过大", 413)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ApiError("请求体格式错误") from exc
        return data if isinstance(data, dict) else {}

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    # ------------------------------------------------------------------ #
    def _serve_static(self, path: str) -> None:
        if path in ("/", "/index.html"):
            index = WEB_DIR / "index.html"
            if not index.is_file():
                self._send(500, b"UI files missing", "text/plain; charset=utf-8")
                return
            html = index.read_text(encoding="utf-8")
            html = html.replace("%%TOKEN%%", self.server.ctx.token)
            html = html.replace("%%VERSION%%", __version__)
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            return

        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return

        relative = posixpath.normpath(path.lstrip("/"))
        if relative.startswith(".."):
            self._send(404, b"not found", "text/plain")
            return
        target = (WEB_DIR / relative).resolve()
        try:
            target.relative_to(WEB_DIR.resolve())
        except ValueError:
            self._send(404, b"not found", "text/plain")
            return
        if not target.is_file():
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        suffix = target.suffix.lower()
        content_type = MIME_OVERRIDES.get(suffix) or mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), content_type)

    # ------------------------------------------------------------------ #
    def _api(self, method: str, path: str, query: Dict[str, list], body: Dict[str, Any]) -> Optional[Dict]:
        ctx: Context = self.server.ctx
        session = ctx.session
        ctx.touch()

        if method == "GET":
            if path == "/api/state":
                return session.state()
            if path == "/api/ping":
                return {"ok": True, "version": __version__}
            if path == "/api/detail":
                case_id = (query.get("id") or [""])[0]
                parts = [p for p in ((query.get("parts") or ["in,ans,out,err"])[0]).split(",") if p]
                return session.case_detail(case_id, parts)
            raise ApiError("未知接口", 404)

        if path == "/api/open":
            target = str(body.get("path") or "").strip()
            if not target:
                raise ApiError("请提供文件夹路径")
            return session.open_folder(target)
        if path == "/api/close":
            return session.close_folder()
        if path == "/api/rescan":
            return session.rescan()
        if path == "/api/dialog":
            kind = str(body.get("kind") or "")
            if kind == "folder":
                return {"paths": [p for p in [session.pick_folder()] if p]}
            if kind == "source":
                return {"paths": session.pick_source()}
            if kind == "cases":
                return {"paths": session.pick_cases()}
            if kind == "checker":
                return {"paths": session.pick_checker()}
            raise ApiError("未知的对话框类型")
        if path == "/api/import/source":
            target = str(body.get("path") or "").strip()
            if not target:
                picked = session.pick_source()
                if not picked:
                    return {"cancelled": True, "state": session.state()}
                target = picked[0]
            return session.import_source(target)
        if path == "/api/import/cases":
            paths = body.get("paths")
            if not paths:
                picked = session.pick_cases()
                if not picked:
                    return {"cancelled": True, "state": session.state()}
                paths = picked
            if not isinstance(paths, list):
                raise ApiError("paths 必须是数组")
            return session.import_cases([str(p) for p in paths])
        if path == "/api/case/remove":
            return session.remove_case(str(body.get("id") or ""))
        if path == "/api/settings":
            patch = body.get("settings") if isinstance(body.get("settings"), dict) else body
            return session.update_settings(patch)
        if path == "/api/compile":
            return session.compile_now()
        if path == "/api/checker":
            target = body.get("path")
            enabled = body.get("enabled")
            if target is not None:
                target = str(target).strip() or None
            if enabled is not None and not isinstance(enabled, bool):
                raise ApiError("enabled 必须是布尔值")
            return session.set_checker(target, enabled)
        if path == "/api/checker/compile":
            return session.compile_checker_now()
        if path == "/api/judge":
            ids = body.get("ids")
            all_cases = bool(body.get("all"))
            if ids is not None and not isinstance(ids, list):
                raise ApiError("ids 必须是数组")
            return session.start_judge([str(i) for i in ids] if ids else None, all_cases)
        if path == "/api/stop":
            return session.stop_judge()
        if path == "/api/reveal":
            session.reveal(str(body.get("path") or ""))
            return {"ok": True}
        if path == "/api/example":
            from .paths import EXAMPLES_DIR
            name = str(body.get("name") or "P1001").strip()
            if not name or any(ch in name for ch in "\\/..:"):
                raise ApiError("示例名称不合法", 400)
            example = EXAMPLES_DIR / name
            if not example.is_dir():
                raise ApiError("示例题目不存在", 404)
            return session.open_folder(str(example))
        if path == "/api/quit":
            if ctx.shutdown_cb:
                threading.Thread(target=ctx.shutdown_cb, daemon=True).start()
            return {"ok": True}
        raise ApiError("未知接口", 404)


def create_server(session: Session, token: str, host: str = "127.0.0.1", port: int = 0):
    context = Context(session, token)
    httpd = TesterServer((host, port), Handler, context)
    return httpd, context
