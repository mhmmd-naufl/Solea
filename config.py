"""Konfigurasi identitas aplikasi yang tidak terikat merek tertentu.

Nama usaha/aplikasi dibaca dari environment variable ``BUSINESS_NAME``.
Default-nya sengaja generik supaya repo ini bisa dipakai sebagai template
tanpa perlu mengganti teks di banyak file.

Frontend memakai ``VITE_BUSINESS_NAME`` (lihat ``frontend/src/config.js``).
"""

import os

from dotenv import load_dotenv

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_BASE_DIR, ".env"))
load_dotenv(os.path.join(_BASE_DIR, "privat", ".env"), override=False)

BUSINESS_NAME = os.getenv("BUSINESS_NAME", "Solea").strip() or "Solea"

# Prefiks nomor tracking publik. Boleh diganti sesuai kebutuhan pemakai.
TRACKING_PREFIX = os.getenv("TRACKING_PREFIX", "TRX-").strip() or "TRX-"

# "development" (default) atau "production". Di produksi FRONTEND_ORIGINS
# wajib diisi — lihat _asal_cors() di main.py.
APP_ENV = os.getenv("APP_ENV", "development").strip().lower() or "development"
