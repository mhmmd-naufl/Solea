"""
Solea Screenshot Generator
==========================
Captures every key screen of Solea for LinkedIn / documentation.

The frontend build is served locally (SPA fallback), while all API calls go to
the live FastAPI backend. A valid JWT is injected into localStorage so the
ProtectedRoute gate lets us in — no need to drive the login form.

Usage:
    python scripts/screenshot_demo.py
    python scripts/screenshot_demo.py --url http://localhost:3000 --delay 2000

Output:
    frontend/public/screenshots/*.png
"""

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import re
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from playwright.async_api import async_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = BASE_DIR / "frontend" / "public" / "screenshots"
DIST = BASE_DIR / "frontend" / "dist"
ENV_FILE = BASE_DIR / ".env"

API_BASE = "https://solea-backend.fly.dev"
TOKEN_KEY = "cucisepatu_token"


def log(msg: str):
    print(f"[screenshot] {msg}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def make_token(secret: str, user_id: str, role: str) -> str:
    """Mint a JWT identical to main.create_access_token()."""
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    exp = datetime.now(timezone.utc) + timedelta(days=7)
    payload = _b64(json.dumps(
        {"sub": user_id, "role": role, "exp": int(exp.timestamp())},
        separators=(",", ":"),
    ).encode())
    signing = f"{header}.{payload}".encode()
    sig = _b64(hmac.new(secret.encode(), signing, hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


def load_env() -> dict:
    values = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line)
        if m and not line.strip().startswith("#"):
            values[m.group(1)] = m.group(2).strip('"').strip("'")
    return values


def api_request(path: str, method: str = "GET", token: str | None = None, body: dict | None = None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API_BASE}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.load(resp)


class SPAHandler(SimpleHTTPRequestHandler):
    """Serve frontend/dist with fallback to index.html for client-side routes."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIST), **kwargs)

    def do_GET(self):
        clean = unquote(urlparse(self.path).path).lstrip("/")
        target = DIST / clean
        if not (target.is_file()):
            self.path = "/index.html"
        return super().do_GET()

    def log_message(self, *args):
        pass


async def shot(page, name: str, full_page: bool = False):
    path = OUT_DIR / name
    await page.screenshot(path=str(path), full_page=full_page)
    log(f"saved {name}")


async def new_context(browser, token: str | None, width: int, height: int, me_override: dict | None = None):
    ctx = await browser.new_context(
        viewport={"width": width, "height": height},
        locale="en-US",
        device_scale_factor=1,
    )
    if token:
        await ctx.add_init_script(
            "localStorage.setItem(" + json.dumps(TOKEN_KEY) + ", " + json.dumps(token) + ");"
        )
    if me_override:
        async def rewrite_me(route):
            response = await route.fetch()
            body = await response.json()
            body.update(me_override)
            await route.fulfill(response=response, json=body)
        await ctx.route("**/api/auth/me", rewrite_me)
    return ctx


async def capture(page, url: str, name: str, delay: int, full_page: bool = False):
    await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    try:
        await page.wait_for_load_state("networkidle", timeout=30_000)
    except Exception:
        log(f"  (networkidle timeout on {name}, continuing)")
    await asyncio.sleep(delay / 1000)
    if "/login" in page.url and name not in ("03_login.png",):
        log(f"  WARNING: {name} was redirected to login")
    await shot(page, name, full_page=full_page)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:3000")
    p.add_argument("--width", type=int, default=1440)
    p.add_argument("--height", type=int, default=900)
    p.add_argument("--delay", type=int, default=1200, help="ms to settle each page")
    args = p.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    env = load_env()
    secret = env.get("JWT_SECRET_KEY", "")
    if not secret:
        raise SystemExit("JWT_SECRET_KEY missing from .env")

    # --- get real tokens from the live backend -----------------------------
    log("authenticating against live backend...")
    auth = api_request(
        "/api/auth/login", "POST",
        body={"phone": "6280000000000", "password": "admin123"},
    )
    admin_token = auth["token"]
    users = api_request("/api/users", token=admin_token)
    by_role = {}
    for u in users:
        by_role.setdefault(u["role"], u)
    log("users by role: " + ", ".join(f"{k}={v['full_name']}" for k, v in by_role.items()))

    def token_for(role: str) -> str:
        u = by_role.get(role)
        if not u:
            raise SystemExit(f"No '{role}' user found in database")
        return make_token(secret, u["id"], role)

    admin_t = admin_token
    tech_t = token_for("technician")
    cust_t = token_for("customer")
    # No drop_point user exists yet — use the admin token for data calls and
    # rewrite only /api/auth/me so the React role guard lets us in.
    dp_t = admin_t
    dp_override = {
        "id": "drop-point-demo",
        "full_name": "Mitra Drop Point",
        "phone": "6280000000000",
        "role": "drop_point",
        "is_verified": True,
    }

    # real tracking code makes the public tracker screenshot look alive
    trx = api_request("/api/transaksi", token=admin_t)
    kode = trx[0]["kode"] if trx else ""

    # --- serve built SPA ---------------------------------------------------
    # Port 3000 on localhost is in the backend's FRONTEND_ORIGINS allow-list,
    # so the browser can call the live API without CORS errors.
    server = ThreadingHTTPServer(("127.0.0.1", 3000), SPAHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log(f"serving {DIST} on http://localhost:3000")

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            base = args.url

            # 1-3: public pages
            ctx = await new_context(browser, None, args.width, args.height)
            page = await ctx.new_page()
            page.set_default_timeout(60_000)
            await capture(page, base, "01_home_catalog.png", args.delay)
            await capture(page, f"{base}/status?kode={kode}", "02_status_tracking.png", args.delay)
            await capture(page, f"{base}/login", "03_login.png", args.delay)
            await ctx.close()

            # 4-8: admin pages
            ctx = await new_context(browser, admin_t, args.width, args.height)
            page = await ctx.new_page()
            page.set_default_timeout(60_000)
            admin_pages = [
                ("/admin", "04_admin_dashboard.png", True),
                ("/admin/sepatu", "05_admin_services.png", False),
                ("/admin/transaksi", "06_admin_transactions.png", False),
                ("/admin/stock", "07_admin_stock.png", False),
                ("/admin/users", "08_admin_users.png", False),
                ("/admin/pengeluaran", "08b_admin_expenses.png", False),
            ]
            for path, name, full in admin_pages:
                await capture(page, f"{base}{path}", name, args.delay, full)
            await ctx.close()

            # 9: technician queue
            ctx = await new_context(browser, tech_t, args.width, args.height)
            page = await ctx.new_page()
            page.set_default_timeout(60_000)
            await capture(page, f"{base}/teknisi", "09_teknisi_queue.png", args.delay)
            await ctx.close()

            # 10: drop point partner
            ctx = await new_context(browser, dp_t, args.width, args.height, dp_override)
            page = await ctx.new_page()
            page.set_default_timeout(60_000)
            await capture(page, f"{base}/mitra", "10_mitra_drop_point.png", args.delay)
            await ctx.close()

            # 11: customer account
            ctx = await new_context(browser, cust_t, args.width, args.height)
            page = await ctx.new_page()
            page.set_default_timeout(60_000)
            await capture(page, f"{base}/akun", "11_customer_account.png", args.delay)
            await ctx.close()

            await browser.close()
    finally:
        server.shutdown()

    count = len(list(OUT_DIR.glob("*.png")))
    log(f"done — {count} PNG files in {OUT_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
