"""Serve frontend/dist as SPA with fallback to index.html (dev-only)."""
import http.server
import os
from pathlib import Path

DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


class SPAHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIST), **kwargs)

    def do_GET(self):
        # If requested file doesn't exist in dist, serve index.html
        path = DIST / self.path.lstrip("/")
        if not (path.exists() and path.is_file()):
            self.path = "/index.html"
        return super().do_GET()

    def log_message(self, *args):
        pass  # quiet


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "4173"))
    httpd = http.server.HTTPServer(("127.0.0.1", port), SPAHandler)
    print(f"Serving {DIST} on http://127.0.0.1:{port}")
    httpd.serve_forever()
