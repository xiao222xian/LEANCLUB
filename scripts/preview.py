"""Local-only static frontend and same-origin API development proxy."""
import http.client
import json
import os
import socket
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


WEB_ROOT = Path(__file__).resolve().parents[1] / "web" / "creator"
MAX_BODY = 8 * 1024 * 1024 + 64 * 1024
MAX_RESPONSE = 32 * 1024 * 1024
ROUTES = {
    "/": "/index.html",
    "/schools": "/lean-school-ranking.html",
    "/join": "/school-submit.html",
    "/city-vote": "/city-vote/index.html",
    "/city-vote/": "/city-vote/index.html",
    "/admin": "/admin/index.html",
    "/admin/": "/admin/index.html",
    "/events": "/events/index.html",
    "/events/": "/events/index.html",
    "/ticket": "/ticket/index.html",
    "/ticket/": "/ticket/index.html",
    "/check-in": "/check-in/index.html",
    "/check-in/": "/check-in/index.html",
}
REQUEST_HEADERS = {"content-type", "origin", "referer", "sec-fetch-site", "cookie", "x-csrf-token", "accept"}
RESPONSE_HEADERS = {
    "content-type", "cache-control", "x-content-type-options", "set-cookie",
    "content-disposition", "etag", "last-modified", "vary", "retry-after",
    "cross-origin-resource-policy",
    "access-control-allow-origin", "access-control-allow-credentials",
}


class Handler(SimpleHTTPRequestHandler):
    server_version = "LeanClubLocal"
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map,
                      ".webp": "image/webp", ".svg": "image/svg+xml",
                      ".js": "text/javascript; charset=utf-8",
                      ".css": "text/css; charset=utf-8", ".woff2": "font/woff2"}

    def __init__(self, *args, **kwargs):
        self._static_response = False
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def setup(self):
        super().setup()
        self.connection.settimeout(25)

    def log_message(self, fmt, *args):
        # Never log request bodies, credentials, cookies, or query parameters.
        sys.stderr.write("%s %s\n" % (self.command, self.path.split("?", 1)[0]))

    def error_json(self, status, message):
        self._static_response = False
        raw = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def safe_path(self):
        try:
            parsed = urlsplit(self.path)
            path = unquote(parsed.path, errors="strict")
        except (ValueError, UnicodeError):
            return None
        if parsed.scheme or parsed.netloc or not path.startswith("/"):
            return None
        if any(char in path for char in ("\\", "\x00", ":")):
            return None
        if any(part in (".", "..") for part in path.split("/")):
            return None
        return path

    def api(self):
        self._static_response = False
        if self.headers.get("Transfer-Encoding"):
            return self.error_json(400, "本地预览不支持分块请求，请发送带长度的请求。")
        lengths = self.headers.get_all("Content-Length", [])
        try:
            if len(lengths) > 1:
                raise ValueError
            length = int(lengths[0]) if lengths else 0
            if length < 0:
                raise ValueError
        except ValueError:
            return self.error_json(400, "请求长度无效。")
        if length > MAX_BODY:
            self.close_connection = True
            return self.error_json(413, "图片过大，请将文件控制在 8 MiB 以内。")
        try:
            body = self.rfile.read(length) if length else None
        except (TimeoutError, socket.timeout, OSError):
            self.close_connection = True
            return self.error_json(408, "请求接收超时，请重试。")
        if body is not None and len(body) != length:
            return self.error_json(400, "请求内容未完整接收。")
        request_headers = {key: value for key, value in self.headers.items()
                           if key.lower() in REQUEST_HEADERS}
        connection = http.client.HTTPConnection("127.0.0.1", self.server.api_port, timeout=20)
        try:
            connection.request(self.command, self.path, body=body, headers=request_headers)
            response = connection.getresponse()
            data = response.read(MAX_RESPONSE + 1)
            if len(data) > MAX_RESPONSE:
                return self.error_json(502, "API 返回内容过大，请检查本地服务。")
            headers = response.getheaders()
            self.send_response(response.status)
            for key, value in headers:
                if key.lower() in RESPONSE_HEADERS:
                    # Each Set-Cookie is sent separately; commas may appear in Expires.
                    self.send_header(key, value)
            if not any(key.lower() == "x-content-type-options" for key, _ in headers):
                self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
        except (OSError, TimeoutError, http.client.HTTPException):
            return self.error_json(502, "暂时无法连接本地 API，请确认服务已启动。")
        finally:
            connection.close()

    def serve_static(self, head=False):
        path = self.safe_path()
        if path is None:
            return self.error_json(400, "请求路径无效。")
        if path.startswith("/api/"):
            return self.api()
        try:
            target = (WEB_ROOT / ROUTES.get(path, path).lstrip("/")).resolve()
        except (OSError, ValueError):
            return self.error_json(400, "请求路径无效。")
        root = WEB_ROOT.resolve()
        if root not in target.parents or not target.is_file():
            return self.error_json(404, "页面或文件不存在。")
        # Reject symlinks that resolve outside the static root and all directories.
        self.path = "/" + target.relative_to(root).as_posix()
        self._resolved_static_path = target
        self._static_response = True
        if head:
            return super().do_HEAD()
        return super().do_GET()

    def end_headers(self):
        if self._static_response:
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def list_directory(self, path):
        self.error_json(404, "目录不可访问。")
        return None

    def translate_path(self, path):
        # Avoid a second URL decode by SimpleHTTPRequestHandler after validation.
        return str(self._resolved_static_path)

    def do_GET(self):
        return self.serve_static()

    def do_HEAD(self):
        return self.serve_static(head=True)

    def do_POST(self):
        path = self.safe_path()
        if path is None:
            return self.error_json(400, "请求路径无效。")
        if not path.startswith("/api/"):
            return self.error_json(404, "接口不存在。")
        return self.api()


def port_from_env(name, default):
    value = int(os.environ.get(name, default))
    if not 1 <= value <= 65535:
        raise ValueError("%s must be between 1 and 65535" % name)
    return value


def main():
    port = port_from_env("PREVIEW_PORT", "4180")
    api_port = port_from_env("CLUB_PORT", "5381")
    with ThreadingHTTPServer(("127.0.0.1", port), Handler) as server:
        server.daemon_threads = True
        server.api_port = api_port
        print("Lean Club: http://127.0.0.1:%s/" % port, flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
