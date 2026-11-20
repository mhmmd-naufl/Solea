# Solea

> Sistem operasional laundry sepatu — full-stack, produksi-ready.

Solea mengubah cara kerja usaha cucian sepatu: dari catat manual yang rawan
*human error* menjadi sistem terpusat. Pelanggan booking & pantau status
sendiri, teknisi kerjakan sesuai antrean, admin pantau omzet & komisi real-time.

Dibangun production-grade: **Supabase + FastAPI + React**, dengan RLS di semua
tabel, audit log, backup otomatis, dan titik integrasi pembayaran.

<div align="center">
  <img src="frontend/public/screenshoots/Dashboard.png" alt="Dashboard Solea" width="100%" />
</div>

**Preview Live:** [https://solea-ten-pi.vercel.app](https://solea-ten-pi.vercel.app)

| Login | `6280000000000` / `admin123` |

[Fitur](#-fitur) · [Tampilan](#-tampilan) · [Arsitektur](#-arsitektur) ·
[Mulai Cepat](#-mulai-cepat) · [Lisensi](#-lisensi)

---

## 📸 Tampilan

### Dashboard Admin
Statistik omzet, distribusi status, kinerja teknisi, tren bulanan — semua dihitung deterministik di backend.

<div align="center">
  <img src="frontend/public/screenshoots/Dashboard.png" alt="Dashboard" width="60%" />
</div>

### Login
Halaman masuk dengan validasi format nomor & tampilan password.

<div align="center">
  <img src="frontend/public/screenshoots/Login.png" alt="Login" width="40%" />
</div>

### Katalog Sepatu
Daftar layanan (master data & harga) yang bisa difilter & dikelola admin.

<div align="center">
  <img src="frontend/public/screenshoots/Katalog.png" alt="Katalog" width="80%" />
</div>

### Detail Sepatu
Detail satu layanan lengkap dengan rentang harga, estimasi hari, dan keterangan treatment.

<div align="center">
  <img src="frontend/public/screenshoots/Detail Sepatu.png" alt="Detail Sepatu" width="60%" />
</div>

### Buat Transaksi
Form booking multi-pasang dengan validasi jenis & nama sepatu, metode bayar, catatan konsumen.

<div align="center">
  <img src="frontend/public/screenshoots/Buat Transaksi.png" alt="Buat Transaksi" width="70%" />
</div>

### Manajemen Stok
Tabel stok alat & bahan dengan indikator kritis (di bawah batas minimum).

<div align="center">
  <img src="frontend/public/screenshoots/Stok.png" alt="Stok" width="80%" />
</div>

### Manajemen Pengguna
CRUD user: admin, teknisi, pelanggan, drop point — termasuk reset password & audit log.

<div align="center">
  <img src="frontend/public/screenshoots/User.png" alt="Users" width="80%" />
</div>

---

## ✨ Fitur

**Alur status:** `Diterima` → `Diproses` → `Diperiksa` → `Selesai` → `Siap Diambil`

| Peran | Yang bisa dilakukan |
| :--- | :--- |
| **Pelanggan** | Booking multi-pasang, cek status + foto bukti, riwayat |
| **Teknisi** | Antrean tugas, update progres, wajib foto sebelum "Selesai" |
| **Admin** | Dashboard omzet/stok/komisi, master harga, audit log |
| **Mitra (drop point)** | Input transaksi outlet, pantau performa cabang |

### Fitur produksi yang sudah termasuk

- 🔔 **Notifikasi WhatsApp** — otomatis saat booking & perubahan status (adapter gateway apa pun).
- 🧾 **Audit log** — jejak aksi sensitif (reset sandi, ubah harga, hapus data) via `GET /api/audit-log`.
- 💳 **Pembayaran** — titik integrasi gateway QRIS/VA. Tanpa provider, endpoint mengembalikan 503 yang jelas, bukan QRIS palsu.
- 📊 **Analitik deterministik** — angka dihitung di backend; AI hanya membuat narasi, dengan fallback bila provider gagal.
- 🔒 **Keamanan berlapis** — Argon2id, RBAC, rate limiting, CORS fail-closed di produksi, RLS Supabase.
- 🔄 **Backup otomatis** — workflow harian + offsite S3/R2 opsional.

---

## ⚙️ Arsitektur

```mermaid
flowchart TB
    P["Pelanggan · Teknisi · Admin · Mitra"] --> FE["Frontend<br/>React + Vite + Tailwind"]
    FE -->|"HTTPS · JWT Bearer"| BE["Backend FastAPI<br/>RBAC · Pydantic · Rate limiting"]
    BE --> DB[("Supabase PostgreSQL<br/>RLS aktif di semua tabel")]
    BE --> ST[("Supabase Storage<br/>foto bukti")]
    BE --> AI["OpenRouter · opsional<br/>narasi & saran"]

    classDef store fill:#ecfdf5,stroke:#10b981;
    class DB,ST store;
```

Frontend **tidak pernah mengakses database langsung**. Semua validasi &
otorisasi terpusat di backend yang memakai *service-role key*. RLS diaktifkan
di semua tabel — jadi bocornya anon key pun tidak membuka data.

---

## 🚀 Mulai Cepat

<details>
<summary><b>Butuh setup cepat? Klik di sini</b> (4 langkah)</summary>

**Prasyarat:** Python 3.12+, Node.js 20+ & npm, akun Supabase.

1. **Backend** — clone, buat venv, `pip install -r requirements.txt`.
2. **Environment** — salin `.env.example` → `.env`, isi `SUPABASE_URL`,
   `SUPABASE_SERVICE_ROLE_KEY`, `JWT_SECRET_KEY` (atau `python setup_env.py`).
3. **Database** — jalankan `SETUP_SOLEA.sql` di Supabase, lalu
   `python seed_direct.py`.
4. **Frontend** — di `frontend/`: `npm ci`, salin `.env.example` → `.env`,
   `npm run dev`; jalankan backend di port 8001. Buka **http://localhost:3000**.

</details>

Instruksi lengkap, tabel migrasi, dan daftar variabel ada di
[Setup](#-detail-setup) dan [Dokumentasi](#-dokumentasi).

---

## 🔐 Lisensi

Solea dirilis dengan **lisensi MIT** — bebas dipakai, dimodifikasi, dan
disebarluaskan untuk evaluasi dan pembelajaran.

> Kredensial produksi disimpan di repo **privat terpisah**
> (pola di `docs/REPO-PRIVAT.md`) — tidak ada secret yang di-commit di sini.

---

## 📁 Struktur Repository

```text
.
├── main.py                 # App FastAPI, config bersama, middleware
├── routers/                # Endpoint per domain (auth, transaksi, stats, audit, payments)
├── analytics.py            # Analitik deterministik + ringkasan AI opsional
├── config.py               # BUSINESS_NAME, TRACKING_PREFIX, APP_ENV
├── notifications.py        # Adapter WhatsApp (best-effort)
├── payments.py             # Adapter gateway pembayaran
├── SETUP_SOLEA.sql         # Skema + RLS + GRANTs (satu file, idempotent)
├── seed_direct.py          # Seed layanan, admin, stock, drop point
├── check_*.py, test_api.py # Pemeriksa skema & audit auth
├── scripts/                # Backup, komisi, seed demo, utilitas
├── frontend/               # React (rute lazy-loaded) + tes Playwright
│   ├── public/screenshots/ # Screenshot untuk README
│   └── src/
├── docs/                   # PRD, arsitektur, panduan, serah terima
└── .github/workflows/      # CI, cleanup foto, backup harian, deploy Fly.io
```

---

## ⌨️ Detail Setup

### Prasyarat
- Python 3.12+, Node.js 20+ dengan npm, akun Supabase

### 1. Clone & backend
```bash
git clone https://github.com/mhmmd-naufl/Solea.git
cd Solea
python -m venv .venv
.\.venv\Scripts\Activate.ps1     # Windows
source .venv/bin/activate        # macOS/Linux
pip install -r requirements.txt
```

### 2. Environment
```bash
cp .env.example .env             # Windows: copy .env.example .env
```
Isi nilai wajib: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `JWT_SECRET_KEY`.
Alternatif interaktif: `python setup_env.py`.

> Backend juga membaca `privat/.env` bila ada. Jangan commit `.env` maupun folder `privat/`.

### 3. Database
Jalankan di **Supabase SQL Editor**:

| Kondisi | File |
| :--- | :--- |
| Database baru | `SETUP_SOLEA.sql` |
| Database lama (migrasi) | `schema.sql` + `audit_log.sql` + `payments.sql` |
| Semua kondisi | RLS & GRANTs sudah di `SETUP_SOLEA.sql` |

Lalu:
```bash
python seed_direct.py      # seed layanan, admin, stock, drop point
```

### 4. Frontend
```bash
cd frontend
npm ci
cp .env.example .env
npm run dev
```
Set `VITE_API_BASE_URL=http://127.0.0.1:8001`, lalu di terminal lain dari root:
```bash
uvicorn main:app --reload --port 8001
```
Buka **http://localhost:3000**.

---

## ⚙️ Konfigurasi

### Identitas aplikasi
| Lokasi | Variabel | Default | Dipakai untuk |
| :--- | :--- | :--- | :--- |
| `.env` | `BUSINESS_NAME` | `Solea` | Judul API, `/api/config`, pesan WhatsApp |
| `frontend/.env` | `VITE_BUSINESS_NAME` | `Solea` | Navbar, footer, judul halaman |

Keduanya harus bernilai sama. Ikon & favicon generik, bisa diganti aset sendiri.

### Variabel lain yang sering diubah
| Variabel | Fungsi |
| :--- | :--- |
| `BUSINESS_PHONE`, `BUSINESS_EMAIL`, `BUSINESS_ADDRESS` | Kontak di pesan & halaman status |
| `BUSINESS_HOURS`, `ACCEPTED_PAYMENTS`, `DROP_POINTS` | Jam buka, metode bayar, daftar drop point |
| `TRACKING_PREFIX` | Prefiks nomor tracking (default `TRX-`) |
| `APP_ENV`, `FRONTEND_ORIGINS` | `production` mewajibkan daftar origin; CORS tak boleh `*` |
| `WA_GATEWAY_*` | Notifikasi WhatsApp opsional |
| `PAYMENT_API_URL`, `PAYMENT_API_KEY`, `PAYMENT_WEBHOOK_TOKEN` | Gateway pembayaran opsional |
| `OPENROUTER_API_KEY` | Ringkasan AI opsional |
| `CLEANUP_TOKEN` | Penghapusan foto otomatis via GitHub Actions |

> ⚠️ Nilai di `.env.example` masih contoh (`6280000000000`, `contact@example.com`). Ganti sebelum produksi.

---

## 🛠️ Script & Utilitas

| Perintah | Fungsi |
| :--- | :--- |
| `python setup_env.py` | Membuat/melengkapi `.env` |
| `python seed_direct.py` | Seed layanan, admin, stock, drop point |
| `python scripts/seed_demo.ps1` | Seed data demo (transaksi, teknisi, pelanggan) |
| `python scripts/update_txn_status.py` | Update status + selesai_at untuk data demo |
| `python check_schema.py` | Cek kolom database |
| `python check_auth.py` | Audit proteksi endpoint |
| `python test_api.py` | Uji endpoint & RBAC |
| `python scripts/backup_database.py` | Backup ke JSON lokal |
| `python scripts/hitung_ulang_komisi.py` | Hitung ulang komisi teknisi |

---

## 🧪 Pengujian

```bash
cd frontend
npm run lint        # ESLint
npm run build       # Vite build
npm run check       # lint + build
npm run test:e2e    # Playwright, menyalakan Vite otomatis di :3000
```
Tes login E2E dilewati sampai `E2E_ADMIN_PHONE` & `E2E_ADMIN_PASSWORD` diisi.

Backend: `check_schema.py`, `check_auth.py`, `test_api.py`. **Jangan jalankan seed ke database produksi.**

CI (`.github/workflows/ci.yml`) menjalankan lint + build, `py_compile`, smoke test `import main`, serta uji endpoint bila secret `CI_SUPABASE_*` tersedia.

---

## 🚢 Deployment

- **Backend:** `fly.toml` + `Dockerfile` (Fly.io) atau `render.yaml` (Render) atau `Procfile` (Railway).
- **Frontend:** `vercel.json` (Vercel) atau `npm run build` ke hosting statis mana pun.
- Set `APP_ENV=production` + `FRONTEND_ORIGINS`. Tanpa keduanya, backend sengaja gagal start daripada membuka CORS.
- Jalankan `SETUP_SOLEA.sql` (termasuk RLS) **sebelum** aplikasi dibuka publik.
- Deploy otomatis: `.github/workflows/fly-deploy.yml` trigger push ke `main`.

### Live Instance
| Komponen | URL |
| :--- | :--- |
| Frontend | https://solea-ten-pi.vercel.app |
| Backend | https://solea-backend.fly.dev |
| Database | Supabase (txeudyvncawbvubcvjaq) |

---

## 💾 Backup

`.github/workflows/backup.yml` berjalan harian (18:00 UTC / 01:00 WIB) & bisa manual.

| Secret | Fungsi |
| :--- | :--- |
| `BACKUP_SUPABASE_URL` | URL project yang dibackup |
| `BACKUP_SUPABASE_SERVICE_ROLE_KEY` | Service-role key (bukan anon) |

Hasil jadi artifact (retensi 30 hari). Untuk retensi lebih lama, isi `BACKUP_S3_BUCKET`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` (+ `BACKUP_S3_ENDPOINT` untuk R2). File backup berisi data sensitif — jangan di-commit.

---

## 📚 Dokumentasi

- [`docs/prd.md`](docs/prd.md) — product requirements
- [`docs/blueprint.md`](docs/blueprint.md) — cetakan teknis
- [`docs/design.md`](docs/design.md) — UI/UX design system
- [`docs/ARSITEKTUR.md`](docs/ARSITEKTUR.md) — arsitektur & skema data
- [`docs/OPERASIONAL.md`](docs/OPERASIONAL.md) — alur kerja operasional
- [`docs/PANDUAN-PENGGUNA.md`](docs/PANDUAN-PENGGUNA.md) — panduan pemakai
- [`docs/data.md`](docs/data.md) — data yang perlu disiapkan
- [`docs/HANDOVER.md`](docs/HANDOVER.md) — serah terima
- [`docs/REPO-PRIVAT.md`](docs/REPO-PRIVAT.md) — pola kredensial terpisah

---

## 📄 Lisensi

MIT — lihat [`LICENSE`](LICENSE). Bebas dipakai dan dimodifikasi.
