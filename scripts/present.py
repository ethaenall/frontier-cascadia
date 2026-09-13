"""Serve only the cinematic presentation on loopback; never starts sensor services."""
from __future__ import annotations
import argparse
import hashlib
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1] / "static" / "cinematic"
ALLOWED_SUFFIXES = {".html", ".js", ".css", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".woff2", ".ico"}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass
    def do_HEAD(self):
        self.do_GET(head=True)
    def do_GET(self, head=False):
        path = urlsplit(self.path).path
        if path == "/__version":
            items = sorted(p for p in ROOT.glob("*") if p.suffix in {".html", ".css", ".js"})
            digest = hashlib.sha256()
            for p in items:
                digest.update(p.name.encode())
                digest.update(str(p.stat().st_mtime_ns).encode())
            data = json.dumps({"version": digest.hexdigest()[:20]}).encode()
            self.send_payload(data, "application/json", head)
            return
        raw = unquote(path).lstrip("/") or "index.html"
        target = (ROOT / raw).resolve()
        if not target.is_relative_to(ROOT.resolve()) or any(x.startswith(".") or x in {"node_modules", "tests"} for x in Path(raw).parts) or target.suffix not in ALLOWED_SUFFIXES or not target.is_file():
            self.send_error(404)
            return
        self.send_payload(target.read_bytes(), mimetypes.guess_type(target.name)[0] or "application/octet-stream", head)
    def send_payload(self, data, content_type, head=False):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        if not head:
            self.wfile.write(data)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8894)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be 0..65535")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"THRESHOLD PRESENTATION http://127.0.0.1:{server.server_port}/", flush=True)
    print("Static presentation only. No sensing, authentication tokens, hardware, or calls.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
