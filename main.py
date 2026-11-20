import os
import secrets
import hmac
import hashlib
from datetime import date, datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, status, Depends, Response, Request, Header
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
import limits
from pydantic import BaseModel, Field, model_validator, BeforeValidator
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from typing import Optional, List, Callable, Annotated
from supabase import create_client, Client, ClientOptions
import httpx
import jwt
import math
from dotenv import load_dotenv

import analytics
from config import APP_ENV, BUSINESS_NAME, TRACKING_PREFIX


def _waktu_utc(value):
    """
    Paksa timestamp tanpa zona dari database dibaca sebagai UTC.

    Kolom `transactions.created_at` di database lama bertipe
    `timestamp without time zone` (walau schema.sql menyebut TIMESTAMPTZ),
    jadi PostgREST mengembalikan string tanpa offset, mis. "2026-10-23T11:29:20".
    Nilainya disimpan dalam UTC -- `get_now_iso()` memakai `timezone.utc`.
    Tanpa penanda zona, browser membacanya sebagai jam lokal WIB sehingga
    tampil 7 jam lebih awal (dan bisa jadi tanggal kemarin untuk transaksi
    dini hari).
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and "T" in value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
        if dt.tzinfo is None:
            return value + "+00:00"
    return value


# Dipakai di semua field timestamp respons: string naive diperlakukan UTC.
WaktuUTC = Annotated[datetime, BeforeValidator(_waktu_utc)]


# WAJIB: muat .env SEBELUM membaca konfigurasi di bawah.
# Tanpa ini os.getenv() selalu None dan backend berhenti dengan pesan
# "key belum diisi" -- disengaja, supaya salah konfigurasi gagal cepat
# dan tidak diam-diam memakai nilai default.
#
# Dua sumber, dibaca berurutan. Yang dimuat DULU menang, jadi:
#   1. .env di root  -- override lokal per-PC (opsional, boleh tidak ada)
#   2. privat/.env   -- kredensial utama, hasil clone repo privat
# Kredensial asli tinggal di repo privat; file .env di root hanya untuk
# menimpa sebagian nilai di satu komputer tanpa mengubah file yang ikut
# repo privat (mis. mau pakai OpenRouter key berbeda sementara).
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_BASE_DIR, ".env"))
load_dotenv(os.path.join(_BASE_DIR, "privat", ".env"), override=False)

# --- KONFIGURASI ---
# Semua dibaca dari .env (lihat .env.example). Tidak ada fallback hardcode
# untuk kunci/API key -- salah konfigurasi harus gagal cepat, bukan diam-diam.


def _is_placeholder(value: Optional[str]) -> bool:
    """Deteksi nilai .env yang masih contoh / belum diisi."""
    if not value:
        return True
    v = value.strip()
    if len(v) < 20:
        return True
    hints = ("ganti", "ubah", "xxx", "...", "your-", "paste", "todo", "changeme", "contoh")
    return any(h in v.lower() for h in hints)


SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
if _is_placeholder(SUPABASE_URL):
    raise RuntimeError(
        "SUPABASE_URL belum diisi dengan benar di .env.\n"
        "  Ambil dari Supabase Dashboard > Project Settings > API > Project URL."
    )


def get_supabase_key() -> str:
    """
    Backend memakai service-role key.

    Alasannya: service_role MELALUI RLS dan boleh menulis, sedangkan anon
    key tidak bisa membaca atau menulis tabel yang RLS-nya aktif (semua tabel).
    Kunci ini hanya boleh ada di backend -- jangan pernah ditaruh di
    frontend / .env frontend.
    """
    key = (
        os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_SERVICE_KEY")
    )
    if _is_placeholder(key):
        raise RuntimeError(
            "SUPABASE_SERVICE_ROLE_KEY belum diisi di .env.\n"
            "  Ambil dari Supabase Dashboard > Project Settings > API Keys > "
            "service_role ( Legacy / anon ).\n"
            "  Backend butuh kunci ini: service_role boleh menulis dan "
            "melewati RLS, anon key tidak. Jangan pakai anon key."
        )
    return key.strip()


JWT_SECRET = os.getenv("JWT_SECRET_KEY", "")
if _is_placeholder(JWT_SECRET):
    raise RuntimeError(
        "JWT_SECRET_KEY belum diisi dengan nilai acak yang kuat di .env.\n"
        "  Generate nilai baru dengan: python -c \"import secrets;"
        " print(secrets.token_urlsafe(48))\"\n"
        "  JWT menandatangani sesi login. Secret yang lemah atau shortcut "
        "berarti siapa pun bisa memalsukan token admin."
    )
JWT_SECRET = JWT_SECRET.strip()
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "10080"))  # 7 hari

# Satu klien HTTP dipakai bersama oleh PostgREST/Auth/Storage. `http2=False`
# bukan pilihan gaya: postgrest-py membuat httpx.Client(http2=True) sendiri, dan
# di Windows HTTP/2 sync yang dipakai dari threadpool FastAPI sesekali gagal
# dengan `[WinError 10035] a non-blocking socket operation could not be
# completed immediately`. Gejalanya: /api/stats/admin, /api/stock, dan
# /api/transaksi tiba-tiba 500 lalu normal lagi -- dashboard tampak kosong
# padahal datanya ada. HTTP/1.1 dengan connection pool ini stabil.
_http_supabase = httpx.Client(
    http2=False,
    timeout=httpx.Timeout(60.0, connect=10.0),
    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
)
supabase: Client = create_client(
    SUPABASE_URL,
    get_supabase_key(),
    options=ClientOptions(httpx_client=_http_supabase),
)

app = FastAPI(title=f"{BUSINESS_NAME} API", version="0.1.0")

# ==========================================
# RATE LIMITING (anti brute-force)
# ==========================================
def _ip_pelanggan(request: Request) -> str:
    """IP asli pengunjung, bukan IP proxy-nya.

    Di balik proxy (Railway / Vercel / nginx), request.client.host selalu
    mengembalikan IP proxy. Kalau dipakai apa adanya, SEMUA pengunjung
    dihitung sebagai satu orang -- satu orang spam membuat semua orang
    kena 429. Karena itu X-Forwarded-For (yang diisi proxy) didahulukan.

    Header itu bisa dipalsukan kalau langsung diakses tanpa proxy, jadi
    fallback-nya tetap request.client.host. Untuk kasus di belakang proxy
    (deploy produksi) hasilnya sudah benar.

    Kenapa per-IP: yang perlu dibatasi adalah pemilik IP, bukan user login.
    Pelaku brute-force password tidak punya token, jadi satu-satunya
    pengenal yang tersedia adalah alamat IP.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # X-Forwarded-For bisa berisi rantai "klien, proksi1, proksi2".
        # Yang paling kiri adalah pengirim asli.
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


# CATATAN JUJUR soal storage_uri="memory://":
#   Counter disimpan di memori proses Python. Artinya:
#     1. Reset setiap server restart / redeploy.
#     2. Tidak sharing antar worker -- kalau uvicorn dijalankan dengan
#        --workers 4, tiap worker punya hitungan sendiri (limit jadi 4x).
#   Untuk deployment sekarang (1 proses, free tier) ini sudah cukup dan
#   tidak butuh Redis. Kalau nanti scale, cukup ganti baris ini jadi
#   storage_uri="redis://..." -- kode endpoint tidak perlu diubah.
#
# RATE_LIMIT_ENABLED=false mematikan seluruh rate limit. Hanya untuk
# pengujian (test_api.py memanggil login belasan kali dari IP yang sama,
# pasti kena limit 5/menit). JANGAN pernah diset false di produksi.
RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").strip().lower() not in (
    "0", "false", "no", "off",
)
#
# default_limits = jaring pengaman global. Dipasang lewat middleware (lihat
# di bawah), BUKAN lewat decorator -- supaya tidak perlu menambahkan
# parameter `request: Request` ke ~60 endpoint yang ada. Endpoint yang
# butuh batas lebih ketat diberi decorator @limiter.limit sendiri.
limiter = Limiter(
    key_func=_ip_pelanggan,
    storage_uri="memory://",
    default_limits=["300/minute"],
    enabled=RATE_LIMIT_ENABLED,
)
app.state.limiter = limiter


def _detik_coba_lagi(detail: Optional[str], default: int = 60) -> int:
    """Detik sampai jendela rate limit terbuka lagi.

    slowapi memberi `exc.detail` berupa string seperti "5 per 1 minute",
    bukan angka. String itu bisa di-parse kembali oleh library `limits`
    (sudah jadi dependensi slowapi) untuk mengambil durasi jendelanya.

    Fallback-nya sengaja 60 detik: kalau format detail berubah di versi
    slowapi mendatang, pesan tetap masuk akal daripada error 500.
    """
    if not detail:
        return default
    try:
        return int(limits.parse_many(detail)[0].get_expiry())
    except Exception:
        return default


def _pesan_rate_limit(request: Request, exc: RateLimitExceeded) -> Response:
    """Balasan 429 dalam bahasa Indonesia + header yang berguna di client."""
    detik = _detik_coba_lagi(exc.detail)
    return JSONResponse(
        status_code=429,
        headers={
            "Retry-After": str(detik),
            "X-RateLimit-Limit": str(exc.detail or ""),
        },
        content={
            "detail": (
                f"Terlalu banyak permintaan. Coba lagi dalam {detik} detik. "
                "Ini batas keamanan, bukan gangguan sistem."
            )
        },
    )


app.add_exception_handler(RateLimitExceeded, _pesan_rate_limit)

# Middleware inilah yang menerapkan default_limits (300/menit) ke semua
# route tanpa perlu menyentuh signature tiap endpoint. Tanpa baris ini
# default_limits diam-diam tidak dipakai sama sekali.
from slowapi.middleware import SlowAPIMiddleware

app.add_middleware(SlowAPIMiddleware)

def _asal_cors() -> list[str]:
    """
    Daftar origin frontend yang boleh memanggil API ini.

    Produksi (APP_ENV=production) WAJIB mengisi FRONTEND_ORIGINS, contoh:
        FRONTEND_ORIGINS=https://your-app.pages.dev,https://your-app.example.com
    Mode development boleh memakai fallback "*" supaya npm run dev langsung
    jalan. Di produksi fallback itu sengaja DIMATIKAN: lebih baik backend
    gagal start daripada diam-diam menerima request dari origin mana pun.
    """
    env = os.getenv("FRONTEND_ORIGINS", "")
    daftar = [a.strip().rstrip("/") for a in env.split(",") if a.strip()]
    if daftar:
        return daftar
    if APP_ENV in ("production", "prod"):
        raise RuntimeError(
            "FRONTEND_ORIGINS wajib diisi saat APP_ENV=production.\n"
            "  Contoh: FRONTEND_ORIGINS=https://app.example.com\n"
            "  Tanpa ini backend membuka CORS untuk semua origin."
        )
    return ["*"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_asal_cors(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Tanpa ini, browser menyembunyikan header paginasi dari JavaScript
    # meski server mengirimnya. Frontend butuh X-Total-Count untuk merender
    # tombol "halaman 2".
    expose_headers=[
        "X-Total-Count",
        "X-Total-Pages",
        "X-Page",
        "X-Per-Page",
    ],
)

security = HTTPBearer(auto_error=False)

# ==========================================
# PYDANTIC MODELS (VALIDASI DATA)
# ==========================================

# Nama bucket foto transaksi (dipakai router transaksi & internal).
PHOTO_BUCKET = "transaksi-photos"

# --- Shoes (Master Layanan / Price List) ---
# POLA treatment sengaja TIDAK memakai enum. Daftar layanan (treatment)
# (Deep Cleaning, Unyellowing, Repaint, Reglue, Bag Cleaning, Helmet, ...)
# terus bertambah dan admin harus bisa menambah yang baru dari panel tanpa
# perlu ALTER TABLE. Yang dijaga hanya bentuknya: huruf/angka/spasi, maks 40.
# Kolomnya mempolakan CHECK constraint di migrate.sql bagian [6].
POLA_TREATMENT = r"^[A-Za-z][A-Za-z0-9 &/.,()\-]{0,40}$"
POLA_KELOMPOK = r"^[A-Za-z][A-Za-z0-9 &/,.\-]{0,60}$"

# Tiga kelompok katalog sesuai daftar harga resmi. Dipakai seed script dan
# sebagai opsi dropdown di panel admin, tapi TIDAK dipaksa di database supaya
# admin tetap bisa menambah kelompok baru sendiri.
KELOMPOK_KATALOG = [
    "Cuci Sepatu",
    "Bag, Hat & Helmet",
    "Repaint & Reglue",
]


class ShoeBase(BaseModel):
    """
    Satu baris = satu varian layanan, bukan satu merk sepatu.

    - `harga_cuci` = harga tetap untuk layanan yangtoharga tunggal
      (mis. Deep Cleaning White 30.000).
    - `harga_min`/`harga_max` = rentang. Transaksi dicatat dari harga_min dan
      harga akhirnya dikonfirmasi admin di outlet lewat endpoint finalisasi
      harga. Kalau rentang diisi, harga_cuci tetap ikut diisi dengan harga_min
      supaya kolom lama (yang dipakai booking) selalu punya angka.
    """
    merk: str
    model: Optional[str] = None
    harga_cuci: int
    harga_min: Optional[int] = Field(None, ge=0)
    harga_max: Optional[int] = Field(None, ge=0)
    kelompok: Optional[str] = Field(None, pattern=POLA_KELOMPOK)
    jenis_treatment: Optional[str] = Field(None, pattern=POLA_TREATMENT)
    keterangan_treatment: Optional[str] = None
    # Estimasi pengerjaan (hari kalender). NULL = frontend memakai default.
    # Per layanan, karena Repaint seminggu tidak boleh dijanjikan 2 hari
    # seperti Deep Cleaning.
    estimasi_hari: Optional[int] = Field(None, ge=1, le=60)
    status: bool = True

    @model_validator(mode="after")
    def _cek_rentang(self) -> "ShoeBase":
        # harga_max < harga_min hampir selalu salah input, dan kalau lolos ke
        # database akan tampil sebagai "Rp160.000 - Rp120.000" di katalog.
        if (
            self.harga_min is not None
            and self.harga_max is not None
            and self.harga_max < self.harga_min
        ):
            raise ValueError("harga_max tidak boleh lebih kecil dari harga_min")
        return self


class ShoeCreate(ShoeBase):
    pass


class ShoeUpdate(BaseModel):
    merk: Optional[str] = None
    model: Optional[str] = None
    harga_cuci: Optional[int] = None
    harga_min: Optional[int] = Field(None, ge=0)
    harga_max: Optional[int] = Field(None, ge=0)
    kelompok: Optional[str] = Field(None, pattern=POLA_KELOMPOK)
    jenis_treatment: Optional[str] = Field(None, pattern=POLA_TREATMENT)
    keterangan_treatment: Optional[str] = None
    estimasi_hari: Optional[int] = Field(None, ge=1, le=60)
    status: Optional[bool] = None

    @model_validator(mode="after")
    def _cek_rentang(self) -> "ShoeUpdate":
        if (
            self.harga_min is not None
            and self.harga_max is not None
            and self.harga_max < self.harga_min
        ):
            raise ValueError("harga_max tidak boleh lebih kecil dari harga_min")
        return self

class ShoeResponse(ShoeBase):
    id: str
    created_at: WaktuUTC

    class Config:
        from_attributes = True


# --- Transactions (Transaksi Cuci) ---
class TransactionBase(BaseModel):
    user_id: Optional[str] = None
    shoe_id: str
    tech_id: Optional[str] = None
    drop_point_id: Optional[str] = None
    harga: Optional[int] = None
    # JENIS sepatu yang titip (Sneakers, Canvas, Leather, ...). Wajib dipilih
    # konsumen sebelum order -- divalidasi manual di create_transaksi supaya
    # pesannya bisa berbahasa manusia (Field(...) untuk required menghasilkan
    # 422 generik yang sulit dibaca di UI).
    jenis_sepatu: Optional[str] = None
    # NAMA sepatu yang titip (merk/tipe, cth. "Nike Air Force 1"). Wajib
    # sebelahan dengan jenis_sepatu.
    nama_sepatu: Optional[str] = None
    catatan_konsumen: Optional[str] = None
    # Metode bayar pilihan konsumen saat booking (tunai/transfer/qris).
    # Divalidasi terhadap ACCEPTED_PAYMENTS saat create.
    payment_method: Optional[str] = None
    # Diisi adapter payments.py saat gateway pembayaran aktif.
    payment_reference: Optional[str] = None
    payment_status: Optional[str] = None
    # "Benang" untuk booking multi-pasang: beberapa transaksi yang dibuat
    # dalam satu sesi booking berbagi grup_id yang sama. NULL untuk booking
    # tunggal. Dibuat frontend per batch submit.
    grup_id: Optional[str] = None

class TransactionCreate(TransactionBase):
    pass

class TransactionStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(Diterima|Diproses|Diperiksa|Selesai|Siap diambil)$")
    tech_id: Optional[str] = None  # Assign teknisi saat update status pertama kali
    photo_before: Optional[str] = None
    photo_after: Optional[str] = None
    photo_defect: Optional[str] = None
    defect_notes: Optional[str] = None

class CustomerContact(BaseModel):
    """Kontak pelanggan. Hanya dikirim ke admin dan drop point."""
    id: str
    full_name: str
    phone: str


class ShoeBrief(BaseModel):
    """
    Ringkasan master layanan yang menempel di transaksi.

    `harga_min`/`harga_max` ikut karena frontend perlu menampilkan
    "mulai dari Rp80.000" untuk layanan ber-harga-rentang. Tidak ada
    informasi sensitif di sini, jadi semua peran boleh menerimanya.
    """
    id: str
    merk: str
    model: Optional[str] = None
    kelompok: Optional[str] = None
    jenis_treatment: Optional[str] = None
    keterangan_treatment: Optional[str] = None
    harga_min: Optional[int] = None
    harga_max: Optional[int] = None
    # Ikut ke tracking publik supaya konsumen melihat estimasi yang sesuai
    # layanannya, bukan angka hardcode.
    estimasi_hari: Optional[int] = None


class StaffBrief(BaseModel):
    """Nama teknisi. Tanpa nomor telepon dan tanpa data kontak lain."""
    id: str
    full_name: str


class TransactionResponse(TransactionBase):
    id: str
    # Nomor tracking yang dilihat konsumen (TRX-XXXXXX). Wajib ada di response
    # karena teknisi dan admin butuh menautkannya ke halaman status publik.
    kode: Optional[str] = None
    status: str
    # Opsional karena teknisi TIDAK boleh melihat angka ini: backend menghapus
    # field-nya sebelum response dibangun (lihat _sembunyikan_biaya), jadi
    # Pydantic harus siap menerima field yang tidak ada. Aturan bisnis, bukan
    # preferensi tampilan -- teknisi tidak bisa membacanya lewat DevTools.
    harga: Optional[int] = None
    tech_commission: Optional[int] = None
    # Potongan harga yang ditetapkan admin lewat endpoint finalisasi. NULL =
    # belum pernah diatur (diperlakukan 0). Ikut disembunyikan dari teknisi
    # bersama harga karena keduanya sama-sama angka buku kas.
    diskon: Optional[int] = None
    # Hanya terisi untuk admin dan drop point (lihat _sertakan_kontak).
    customer: Optional[CustomerContact] = None
    # Nama sepatu dan teknisi, hasil embed PostgREST. Tanpa ini tabel admin
    # cuma menampilkan UUID yang tidak berguna. Tidak ada data sensitif di
    # sini, jadi semua peran boleh menerimanya.
    shoe: Optional[ShoeBrief] = None
    tech: Optional[StaffBrief] = None
    photo_before: Optional[str] = None
    photo_after: Optional[str] = None
    photo_defect: Optional[str] = None
    defect_notes: Optional[str] = None
    catatan_konsumen: Optional[str] = None
    # Diisi backend saat teknisi menandai Selesai (lihat update_transaksi_status).
    # Laporan omzet memakai kolom ini, bukan created_at, supaya cucian yang masuk
    # tanggal 31 dan selesai tanggal 2 tidak terpotong dua bulan.
    selesai_at: Optional[WaktuUTC] = None
    created_at: WaktuUTC
    updated_at: Optional[WaktuUTC] = None

    class Config:
        from_attributes = True


# --- Profiles (Users: Admin, Customer, Technician, Drop Point) ---
class ProfileBase(BaseModel):
    full_name: str
    phone: str
    role: str = Field(..., pattern="^(admin|customer|technician|drop_point)$")

class ProfileCreate(ProfileBase):
    pass

class ProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = Field(None, pattern="^(admin|customer|technician|drop_point)$")
    is_verified: Optional[bool] = None

class ProfileResponse(ProfileBase):
    id: str
    is_verified: bool
    created_at: WaktuUTC

    class Config:
        from_attributes = True


# --- Drop Points ---
class DropPointBase(BaseModel):
    nama: str
    alamat: Optional[str] = None
    wa_contact: Optional[str] = None
    aktif: bool = True

class DropPointCreate(DropPointBase):
    pass

class DropPointUpdate(BaseModel):
    nama: Optional[str] = None
    alamat: Optional[str] = None
    wa_contact: Optional[str] = None
    aktif: Optional[bool] = None

class DropPointResponse(DropPointBase):
    id: str
    created_at: WaktuUTC

    class Config:
        from_attributes = True


# --- Stock (Alat & Bahan) ---
class StockBase(BaseModel):
    nama_item: str
    tipe: str = Field(..., pattern="^(alat|bahan)$")
    jumlah: int = 0
    satuan: str = Field(..., pattern="^(pcs|liter|kg|pack)$")
    batas_minimum: int = 5
    supplier: Optional[str] = None
    harga_beli: float = 0.0
    tanggal_masuk: Optional[str] = None  # ISO format date string

class StockCreate(StockBase):
    pass

class StockUpdate(BaseModel):
    nama_item: Optional[str] = None
    tipe: Optional[str] = Field(None, pattern="^(alat|bahan)$")
    jumlah: Optional[int] = None
    satuan: Optional[str] = Field(None, pattern="^(pcs|liter|kg|pack)$")
    batas_minimum: Optional[int] = None
    supplier: Optional[str] = None
    harga_beli: Optional[float] = None
    tanggal_masuk: Optional[str] = None

class StockResponse(StockBase):
    id: str
    last_updated: Optional[datetime] = None

    class Config:
        from_attributes = True


# --- Expenses (pengeluaran operasional: listrik, PDAM, dll.) ---
class ExpenseBase(BaseModel):
    kategori: str = Field(..., min_length=2, max_length=40)
    jumlah: int = Field(..., gt=0, description="Rupiah, harus > 0")
    tanggal: Optional[str] = None  # YYYY-MM-DD; default hari ini (WIB)
    keterangan: Optional[str] = Field(None, max_length=200)

class ExpenseCreate(ExpenseBase):
    pass

class ExpenseUpdate(BaseModel):
    kategori: Optional[str] = Field(None, min_length=2, max_length=40)
    jumlah: Optional[int] = Field(None, gt=0)
    tanggal: Optional[str] = None
    keterangan: Optional[str] = Field(None, max_length=200)

class ExpenseResponse(ExpenseBase):
    id: str
    created_at: Optional[WaktuUTC] = None
    updated_at: Optional[WaktuUTC] = None

    class Config:
        from_attributes = True


# --- Auth ---
class LoginRequest(BaseModel):
    phone: str
    password: str

class RegisterRequest(BaseModel):
    full_name: str = Field(..., min_length=2)
    phone: str
    password: str = Field(..., min_length=6)

class PasswordSet(BaseModel):
    password: str = Field(..., min_length=6)

class UserPublic(BaseModel):
    id: str
    full_name: str
    phone: str
    role: str
    is_verified: bool = False

class LoginResponse(BaseModel):
    token: str
    user: UserPublic


# --- Stats (Dashboard Admin) ---
class TechnicianIncome(BaseModel):
    id: str
    full_name: str
    jumlah_pekerjaan: int
    tech_commission: int

class PeriodeInfo(BaseModel):
    """Info periode yang dipakai, dikirim balik supaya frontend bisa menampilkan
    label yang sama persis dengan yang dihitung backend."""
    periode: str
    label: str
    granularitas: str = Field(..., description="'hari' atau 'bulan'")
    mulai: Optional[datetime] = None
    selesai: Optional[datetime] = None


class StatsResponse(BaseModel):
    total_transaksi: int
    shoes_washed: int
    total_pendapatan: int
    total_teknisi: int
    per_teknisi: List[TechnicianIncome]
    per_status: dict
    # Data yang masih di tahap awal. Sengaja dipisah dari di atas: yang
    # "belum selesai" tidak punya tanggal selesai sehingga tidak masuk hitungan
    # omzet periode, tapi admin tetap perlu tahu jumlahnya.
    masih_jalan: int = 0
    total_komisi: int = 0
    sisa_untuk_outlet: int = 0
    # Pengeluaran operasional (listrik, PDAM, dll.) di periode yang sama,
    # dan sisa setelah dikurangi keduanya -- "profit bersih" versi sederhana.
    total_pengeluaran: int = 0
    sisa_bersih: int = 0
    periode: PeriodeInfo


# ==========================================
# HELPER FUNCTIONS
# ==========================================

# Persentase komisi teknisi (default 40%, bisa diubah lewat env TECHNICIAN_COMMISSION_PERCENT)
TECHNICIAN_COMMISSION_PERCENT = int(os.getenv("TECHNICIAN_COMMISSION_PERCENT", "40"))

def calculate_commission(harga: int) -> int:
    """Hitung komisi teknisi (default 40%) dari harga terpasang."""
    return int(harga * TECHNICIAN_COMMISSION_PERCENT / 100)


def komisi_untuk(harga: int, tech_id: Optional[str]) -> int:
    """
    Komisi berdasarkan SIAPA yang dinyatakan mengerjakan.

    Aturan pemilik:
    - tech_id kosong -> 0. Tidak ada pengerja yang digaji, jadi tidak ada
      yang berhak atas potongannya; omzet penuh milik outlet.
    - Pengerja ber-role ADMIN -> 0. "Khusus admin tidak ada potongan 40%":
      kalau pemilik mengerjakan sendiri, hasilnya utuh.
    - Pengerja ber-role technician -> potongan standard
      (TECHNICIAN_COMMISSION_PERCENT).

    Role selalu dibaca dari profiles di sisi server, bukan dikirim klien --
    kalau kirim dari klien, siapa pun bisa mengimplikasikan dirinya admin
    dan komisinya jadi 0. Id yang hilang (profiling dihapus) dihitung 0,
    bukan menyimpan komisi menggantung tanpa penerima.
    """
    if not tech_id:
        return 0
    cari = (
        supabase.from_("profiles")
        .select("role")
        .eq("id", tech_id)
        .execute()
        .data
        or []
    )
    peran = (cari[0].get("role") if cari else "") or ""
    if peran == "technician":
        return calculate_commission(harga)
    # Selain technician (admin, drop_point, customer, profil hilang) -> 0.
    # Satu jalur penolakan untuk semuanya: tidak ada cara menitip komisi.
    return 0

def get_now_iso():
    return datetime.now(timezone.utc).isoformat()

def normalize_phone(phone: str) -> str:
    """Samakan format nomor: 0812... / +62812... -> 62812..."""
    p = "".join(ch for ch in phone if ch.isdigit())
    if p.startswith("0"):
        p = "62" + p[1:]
    elif p.startswith("8"):
        p = "62" + p
    return p

_HASHER_SANDI = PasswordHasher()  # Argon2id — tahan serangan GPU

def hash_password(password: str) -> str:
    """Argon2id untuk password baru."""
    return _HASHER_SANDI.hash(password)

def verify_password(password: str, stored: Optional[str]) -> bool:
    """Verifikasi Argon2id baru atau PBKDF2 lama (kompatibel selama migrasi)."""
    if not stored:
        return False
    if stored.startswith("$argon2"):
        try:
            return _HASHER_SANDI.verify(stored, password)
        except (VerifyMismatchError, InvalidHashError, ValueError):
            return False
    try:
        _, iterations, salt, expected = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, AttributeError):
        return False

def generate_tracking_code() -> str:
    """Nomor tracking singkat yang dilihat konsumen, cth. TRX-7F3K9Q"""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # tanpa I, O, 0, 1
    return TRACKING_PREFIX + "".join(secrets.choice(alphabet) for _ in range(6))

def create_access_token(user_id: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode(
        {"sub": user_id, "role": role, "exp": expire},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )

def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    if credentials is None:
        raise HTTPException(401, "Belum login")
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Sesi habis, silakan login ulang")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Token tidak valid")

    result = supabase.from_("profiles").select("*").eq("id", payload["sub"]).execute()
    if not result.data:
        raise HTTPException(401, "User tidak ditemukan")
    return result.data[0]

def require_role(*roles: str):
    def checker(user: dict = Depends(get_current_user)):
        if user["role"] not in roles:
            raise HTTPException(403, "Kamu tidak punya akses ke bagian ini")
        return user
    return checker


# Field yang TIDAK boleh keluar ke teknisi. Aturan bisnis: teknisi fokus
# pada pekerjaan, tidak perlu melihat harga jual maupun komisi. disembunyikan
# di backend (bukan cuma di frontend) supaya tidak bisa dibaca lewat DevTools.
_FIELD_TEKAN_HIDDEN = ("harga", "tech_commission", "diskon")


def _sembunyikan_biaya(baris: list[dict], user: dict) -> list[dict]:
    """Hapus field harga/komisi kalau yang meminta adalah teknisi."""
    if user.get("role") != "technician":
        return baris
    return [{k: v for k, v in row.items() if k not in _FIELD_TEKAN_HIDDEN} for row in baris]


# Peran yang boleh melihat kontak pelanggan. Mitra drop point perlu nomor
# WhatsApp untuk mengabari waktu shoes-nya sudah siap diambil. Teknisi tidak
# perlu, jadi daftar telepon pelanggan tidak ikut ke perangkatnya.
_PERAN_BISA_LIHAT_KONTAK = ("admin", "drop_point")


def _sertakan_kontak(baris: list[dict], user: dict) -> list[dict]:
    """
    Tambahkan nama + nomor WhatsApp pelanggan ke setiap transaksi.

    Hanya untuk admin dan drop point. Konsumen tidak butuh (itu datanya sendiri)
    dan teknisi tidak boleh -- supaya daftar telepon pelanggan tidak bocor ke
    perangkat teknisi.
    """
    if user.get("role") not in _PERAN_BISA_LIHAT_KONTAK or not baris:
        return baris

    owner_ids = {r.get("user_id") for r in baris if r.get("user_id")}
    if not owner_ids:
        return baris

    pelanggan = (
        supabase.from_("profiles")
        .select("id, full_name, phone")
        .in_("id", list(owner_ids))
        .execute().data or []
    )
    peta = {p["id"]: p for p in pelanggan}

    return [
        {**r, "customer": peta.get(r.get("user_id"))} for r in baris
    ]


def _siapkan_transaksi(baris: list[dict], user: dict) -> list[dict]:
    """Terapkan seluruh aturan bentuk respons sesuai peran pemohon."""
    return _sertakan_kontak(_sembunyikan_biaya(baris, user), user)


# Kolom master layanan yang ikut di-embed ke transaksi. Dipakai di semua select
# transaksi supaya kolomnya tidak bolong-bolong di satu endpoint tapi ada di
# endpoint lain -- dulu kindalah teknis untuk memakai kolom baru, dan itu mudah
# terlewat karena PostgREST diam-diam mengembalikan null untuk kolom yang
# tidak disebut.
_KOLOM_SHOE = "id, merk, model, kelompok, jenis_treatment, keterangan_treatment, harga_min, harga_max, estimasi_hari"


# ==========================================
# PAGINASI & PENCARIAN
# ==========================================
#
# Format: response tetap berupa JSON list (supaya tidak membongkar konsumen
# lama), jumlah total dikirim lewat header X-Total-Count / X-Total-Pages.
#
# Endpoint tanpa parameter `page` tetap mengembalikan seluruh baris seperti
# sebelumnya -- jadi katalog publik, booking, dan halaman status tidak ikut
# berubah. Hanya halaman admin yang lewat paginasi.

PER_PAGE_DEFAULT = 25
PER_PAGE_MAKS = 200

HEADER_TOTAL = "X-Total-Count"
HEADER_HALAMAN = "X-Total-Pages"


def _normalisasi_page(page: Optional[int], per_page: Optional[int]) -> tuple[int, int]:
    """(page, per_page) -> selalu nilai yang aman dipanggilkan ke range()."""
    p = max(1, page or 1)
    n = per_page if per_page else PER_PAGE_DEFAULT
    n = min(max(1, n), PER_PAGE_MAKS)
    return p, n


def _jumlah_baris(bangun_query: Callable[[], object]) -> int:
    """Total baris hasil filter. Query dibangun ulang supaya tidak ikut
    terpengaruh limit dari pemakaian sebelumnya."""
    try:
        return bangun_query().limit(1).execute().count or 0
    except Exception:  # noqa: BLE001
        return 0


def _halaman_berpaginan(
    bangun_query: Callable[[], object],
    page: Optional[int],
    per_page: Optional[int],
    response: Response,
) -> list[dict]:
    """
    Jalankan query berpaginasi dan tulis header total ke response.

    Clamp halaman ke total_pages itu WAJIB: PostgREST melempar PGRST103
    ("Requested range not satisfiable") kalau offset melewati jumlah baris.
    Kasus nyata: admin sedang di halaman 5, lalu teknisi menghapus transaksi
    sehingga tersisa 12 baris. Tanpa clamp, dashboard admin ikut error.
    """
    p, n = _normalisasi_page(page, per_page)
    total = _jumlah_baris(bangun_query)
    total_halaman = max(1, math.ceil(total / n)) if total else 1
    p = min(p, total_halaman)

    offset = (p - 1) * n
    result = bangun_query().range(offset, offset + n - 1).execute()

    response.headers[HEADER_TOTAL] = str(total)
    response.headers[HEADER_HALAMAN] = str(total_halaman)
    response.headers["X-Page"] = str(p)
    response.headers["X-Per-Page"] = str(n)
    return result.data or []


def _pola_ilike(kolom: str, teks: str) -> str:
    """
    Bangun pola ilike yang aman dari input pengguna.

    Nilainya dibungkus tanda kutip ganda. Tanpa itu, pencarian "a, b" atau
    "60 (besar)" merusak logic tree PostgREST dan seluruh permintaan gagal
    dengan PGRST100.
    """
    aman = (teks or "").replace("\\", "").replace('"', "").strip()
    return f'{kolom}.ilike."%{aman}%"'


def _cari_teks(teks: Optional[str]) -> Optional[str]:
    """Normalisasi input pencarian: None kalau kosong atau terlalu pendek."""
    t = (teks or "").strip()
    return t if len(t) >= 2 else None


# ==========================================
# PERIODE LAPORAN (BULAN / TAHUN)
# ==========================================
# Semua angka laporan omzet dihitung dari `selesai_at`, bukan `created_at`.
# Alasannya praktis: pemilik mau tahu "bulan ini dapat berapa", dan cucian
# yang masuk tanggal 31 lalu selesai tanggal 2 itu hasil bulan 2, bukan bulan 1.
#
# Zona waktu: waktu Indonesia (WIB, UTC+7). Bucket harian/bulanan dihitung
# di zona ini, kalau tidak transaksi lewat tengah malam akan masuk tanggal
# yang salah. Bandingkan dengan `now(timezone.utc)` langsung akan menggeser
# grafik satu hari ke belakang.


ZONA_WIB = timezone(timedelta(hours=7))

# Nilai `periode` yang diterima. Sengaja daftar tetap, bukan bebas: nama ini
# ikut dikirim ke frontend untuk menentukan label sumbu grafik.
PERIODE_VALID = ("bulan", "tahun", "semua")


def _parse_periode(periode: str, bulan: Optional[int], tahun: Optional[int]) -> dict:
    """
    Ubah parameter periode menjadi rentang waktu WIB yang konkret.

    - `bulan=10&tahun=2026` -> 1 Oktober 00:00 s.d. 1 November 00:00 WIB
    - `tahun=2026`           -> 1 Januari s.d. 1 Januari tahun depan WIB
    - `semua`                -> None, artinya tidak ada batas bawah

    Rentang ditulis sebagai [mulai, selesai) supaya PostgREST cukup memakai .gte()
    dan .lt() tanpa kasus khusus untuk detik terakhir.

    Awal bulan/tahun dihitung dengan aritmetika timedelta, bukan `dateutil`,
    supaya tidak ada dependency tambahan untuk satu operasi sederhana.
    """
    if periode not in PERIODE_VALID:
        raise HTTPException(400, f"Periode harus salah satu dari: {', '.join(PERIODE_VALID)}")

    if periode == "semua":
        return {"periode": "semua", "mulai": None, "selesai": None,
                "label": "Semua waktu", "granularitas": "bulan"}

    if tahun is None:
        raise HTTPException(400, "Parameter `tahun` wajib diisi")

    # Batasi tahun supaya tidak bisa membuat rentang 10.000 tahun yang
    # membuat PostgREST menerima batas waktu di luar range PostgreSQL.
    if tahun < 2000 or tahun > 2100:
        raise HTTPException(400, "Tahun harus antara 2000 dan 2100")

    if periode == "tahun":
        mulai = datetime(tahun, 1, 1, tzinfo=ZONA_WIB)
        selesai = datetime(tahun + 1, 1, 1, tzinfo=ZONA_WIB)
        return {
            "periode": "tahun",
            "mulai": mulai,
            "selesai": selesai,
            "label": f"Tahun {tahun}",
            "granularitas": "bulan",
        }

    if bulan is None:
        raise HTTPException(400, "Parameter `bulan` wajib diisi saat periode=bulan")
    if bulan < 1 or bulan > 12:
        raise HTTPException(400, "Bulan harus antara 1 sampai 12")

    mulai = datetime(tahun, bulan, 1, tzinfo=ZONA_WIB)
    # Awal bulan depan. Desember harus naik ke Januari tahun depan.
    selesai = (
        datetime(tahun + 1, 1, 1, tzinfo=ZONA_WIB)
        if bulan == 12
        else datetime(tahun, bulan + 1, 1, tzinfo=ZONA_WIB)
    )

    nama_bulan = [
        "Januari", "Februari", "Maret", "April", "Mei", "Juni",
        "Juli", "Agustus", "September", "Oktober", "November", "Desember",
    ][bulan - 1]

    return {
        "periode": "bulan",
        "mulai": mulai,
        "selesai": selesai,
        "label": f"{nama_bulan} {tahun}",
        "granularitas": "hari",
    }


def _saring_selesai(
    query,
    mulai: Optional[datetime],
    selesai: Optional[datetime],
    kolom: str = "selesai_at",
):
    """
    Terapkan filter periode pada kolom tanggal selesai.

    Hanya baris yang benar-benar punya `selesai_at` yang ikut. Transaksi yang
    masih di tahap awal (masih diproses) tidak punya tanggal selesai, jadi
    menghitungnya sebagai "omzet bulan ini" akan berbohong. Untuk laporan hal
    itu memang yang benar: yang dihitung adalah pekerjaan yang sudah rampung.

    Kolomnya bisa diganti lewat `kolom` supaya endpoint yang memang menghitung
    dari tanggal masuk (mis. antrean kerja) bisa memakai created_at.
    """
    if mulai is not None:
        query = query.gte(kolom, mulai.isoformat())
    if selesai is not None:
        query = query.lt(kolom, selesai.isoformat())
    return query


def _rentang(
    rows: list[dict],
    mulai: Optional[datetime],
    selesai: Optional[datetime],
    kolom: str,
) -> list[dict]:
    """Ambil baris yang timestamp-nya masuk interval [mulai, selesai)."""
    out: list[dict] = []
    for row in rows:
        value = row.get(kolom)
        if not value:
            continue
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if mulai is not None and dt < mulai:
            continue
        if selesai is not None and dt >= selesai:
            continue
        out.append(row)
    return out


def _periode_sebelumnya(
    mulai: Optional[datetime], selesai: Optional[datetime]
) -> Optional[tuple[datetime, datetime]]:
    """Periode yang sama durasinya, tepat sebelum mulai."""
    if mulai is None or selesai is None:
        return None
    durasi = selesai - mulai
    return (mulai - durasi, mulai)


def _hitung_dashboard(supabase, p: dict) -> dict:
    """
    Menghitung semua data untuk dashboard dalam SATU panggilan Supabase.
    Mengembalikan seluruh komponen: ringkasan, teknisi, stok, grafik, dll.
    """
    # Ambil transaksi berdasarkan filter periode
    query = supabase.from_("transactions").select(
        "harga, tech_commission, tech_id, status, shoe_id, created_at, selesai_at, "
        "catatan_konsumen, defect_notes, photo_after"
    )
    query = _saring_selesai(query, p["mulai"], p["selesai"], "selesai_at")
    trx = query.execute().data or []

    # Ambil data pendukung sekaligus
    shoe_ids = [r["shoe_id"] for r in trx if r.get("shoe_id")]
    shoes = {}
    if shoe_ids:
        shoes_data = (
            supabase.from_("shoes").select("id, merk, model").in_("id", shoe_ids).execute().data or []
        )
        shoes = {s["id"]: s for s in shoes_data}

    teknisi = (
        supabase.from_("profiles")
        .select("id, full_name")
        .eq("role", "technician")
        .execute().data or []
    )
    teknisi_map = {t["id"]: t["full_name"] for t in teknisi}

    # Agregasi
    per_status: dict = {}
    by_tech: dict = {}
    pendapatan = 0
    komisi_total = 0

    for row in trx:
        status = row.get("status") or ""
        if status in per_status:
            per_status[status] += 1
        pendapatan += row.get("harga") or 0
        komisi_total += row.get("tech_commission") or 0

        tech_id = row.get("tech_id")
        if tech_id and tech_id in teknisi_map:
            agg = by_tech.setdefault(tech_id, {"jumlah": 0, "komisi": 0})
            agg["jumlah"] += 1
            agg["komisi"] += row.get("tech_commission") or 0

    # Per layanan
    per_layanan: dict = {}
    per_layanan_omzet: dict = {}
    for row in trx:
        s = shoes.get(row.get("shoe_id"))
        if not s:
            continue
        lbl = " ".join(x for x in [s.get("merk"), s.get("model")] if x) or "Tanpa nama"
        per_layanan[lbl] = per_layanan.get(lbl, 0) + 1
        per_layanan_omzet[lbl] = per_layanan_omzet.get(lbl, 0) + (row.get("harga") or 0)

    # Pembanding sebelumnya
    pembanding = _periode_sebelumnya(p["mulai"], p["selesai"])
    lalu = _rentang(trx, pembanding[0], pembanding[1], "selesai_at") if pembanding else []

    # Status masih jalan (seluruh riwayat, bukan periode)
    belum_data = (
        supabase.from_("transactions")
        .select("id", count="exact")
        .in_("status", ["Diterima", "Diproses", "Diperiksa"])
        .execute()
    )
    masih_jalan = belum_data.count or 0 if belum_data.data is not None else 0

    # Tren harian/bulanan
    granularitas = p["granularitas"]
    grafik = analytics._buat_bucket(
        p["mulai"], p["selesai"], granularitas, analytics.per_bucket  # placeholder
    )  # nanti diisi

    # Komisi per teknisi untuk card
    per_teknisi_list = []
    for t in teknisi:
        c = by_tech.get(t["id"], {"jumlah": 0, "komisi": 0})
        per_teknisi_list.append({
            "id": t["id"],
            "full_name": t["full_name"],
            "jumlah_pekerjaan": c["jumlah"],
            "tech_commission": c["komisi"],
        })

    # Grafik (diproses di analytics.py)
    # Simpel: ambil dari analytics build_summary
    from analytics import build_summary as abuild
    # ... nanti lanjut

    return {
        "ringkasan": {},  # nanti diisi analytics fallback
        "saran": [],
        "per_teknisi": per_teknisi_list,
        "per_status": per_status,
        "masih_jalan": masih_jalan,
        "total_pendapatan": pendapatan,
        "total_komisi": komisi_total,
        "sisa_untuk_outlet": pendapatan - komisi_total,
        "per_layanan": per_layanan,
        "per_layanan_omzet": per_layanan_omzet,
        "tren": [],  # data grafik
        "peluang": [],  # data untuk grafik
    }


def _kelompok_bersih(kelompok: Optional[str]) -> Optional[str]:
    """
    Bersihkan nama kelompok katalog sebelum dipakai sebagai filter.

    Nilai ini masuk ke `.eq()` lalu ke query string PostgREST. Tanpa Validate,
    spasi dan koma di "Bag, Hat & Helmet" bisa merusak sintaks query dan
    seluruh permintaan gagal. Endpoint `/api/sepatu` ini publik, jadi jangan
    percaya begitu saja inputnya.
    """
    if kelompok is None:
        return None
    bersih = kelompok.strip()
    if not bersih:
        return None
    if len(bersih) > 60:  # sama dengan batas CHECK constraint di database
        raise HTTPException(400, "Nama kelompok terlalu panjang")
    if not all(ch.isalnum() or ch in " &/,.-" for ch in bersih):
        raise HTTPException(400, "Nama kelompok tidak valid")
    return bersih


def _kategori_bersih(kategori: Optional[str]) -> Optional[str]:
    """
    Normalisasi kategori pengeluaran: trim + rapikan spasi + lowercase.

    Tanpa lowercase, "Listrik" dan "listrik" jadi dua kategori berbeda dan
    rekap mingguan terpecah. Spasi berlebih juga dirapikan supaya "biaya  air"
    tidak lolos sebagai kategori baru.
    """
    if kategori is None:
        return None
    bersih = " ".join(kategori.strip().lower().split())
    if not bersih:
        return None
    if len(bersih) > 40:  # sama dengan batas CHECK constraint di database
        raise HTTPException(400, "Nama kategori terlalu panjang (maks 40 karakter)")
    if not all(ch.isalnum() or ch in " &/.,()-" for ch in bersih):
        raise HTTPException(400, "Nama kategori tidak valid")
    return bersih


def _tanggal_bersih(tanggal: Optional[str], wajib: bool = False) -> Optional[str]:
    """
    Validasi tanggal pengeluaran (YYYY-MM-DD). Tanggal di masa depan ditolak:
    hampir pasti salah ketik, dan merusak rekap mingguan.
    """
    if tanggal is None or not str(tanggal).strip():
        if wajib:
            raise HTTPException(400, "Tanggal tidak valid")
        return None
    try:
        d = date.fromisoformat(str(tanggal).strip()[:10])
    except ValueError:
        raise HTTPException(400, "Format tanggal harus YYYY-MM-DD")
    if d > datetime.now(ZONA_WIB).date():
        raise HTTPException(400, "Tanggal pengeluaran tidak boleh di masa depan")
    return d.isoformat()


# ==========================================
# ROOT & HEALTH
# ==========================================

# Metode bayar yang diterima outlet, dari .env ACCEPTED_PAYMENTS. Alias umum
# dipetakan supaya "qr" dan "qris" tidak jadi dua nilai berbeda diam-diam.
_ALIAS_BAYAR = {"qr": "qris", "cash": "tunai", "transfer bank": "transfer"}
ACCEPTED_PAYMENTS = sorted({
    _ALIAS_BAYAR.get(p, p)
    for p in (x.strip().lower() for x in os.getenv("ACCEPTED_PAYMENTS", "tunai").split(","))
    if p
})

@app.get("/", tags=["Root"])
def read_root():
    return {"message": f"Selamat datang di {BUSINESS_NAME} API", "status": "aktif", "version": "0.1.0"}

@app.get("/api/config", tags=["Config"])
def public_config():
    """
    Konfigurasi publik untuk frontend: info bisnis dan metode bayar.

    Tanpa endpoint ini, nomor WA admin dan daftar metode bayar di-hardcode di
    beberapa komponen frontend -- dan sempat tidak sinkron dengan .env
    (UI menampilkan 3 metode padahal .env hanya mengizinkan 2).
    """
    return {
        "business_name": BUSINESS_NAME,
        "business_phone": os.getenv("BUSINESS_PHONE", ""),
        "business_hours": os.getenv("BUSINESS_HOURS", "09.00-20.00 WIB"),
        "accepted_payments": ACCEPTED_PAYMENTS,
    }

@app.get("/health", tags=["Health"])
def health_check():
    try:
        supabase.from_("profiles").select("count").execute()
        db_status = "terkoneksi"
    except Exception as e:
        db_status = f"error: {str(e)[:50]}"
    return {"status": "OK", "message": f"Service running. Supabase DB status: {db_status}"}


# ==========================================
# AUTH
# ==========================================

def _create_user_with_password(full_name: str, phone: str, password: str, role: str):
    normalized = normalize_phone(phone)
    existing = supabase.from_("profiles").select("id").eq("phone", normalized).execute()
    if existing.data:
        raise HTTPException(400, "Nomor WhatsApp sudah terdaftar")

    result = supabase.from_("profiles").insert({
        "full_name": full_name,
        "phone": normalized,
        "password_hash": hash_password(password),
        "role": role,
        "is_verified": role == "admin",
        "created_at": get_now_iso(),
    }).execute()
    if not result.data:
        raise HTTPException(500, "Gagal membuat user")

    user = result.data[0]
    return {
        "token": create_access_token(user["id"], user["role"]),
        "user": {
            "id": user["id"],
            "full_name": user["full_name"],
            "phone": user["phone"],
            "role": user["role"],
            "is_verified": user.get("is_verified", False),
        },
    }


# ==========================================
# STATS (Dashboard Admin)
# ==========================================


def catat_audit(
    aksi: str,
    target_tipe: Optional[str] = None,
    target_id: Optional[str] = None,
    detail: Optional[dict] = None,
    user: Optional[dict] = None,
) -> None:
    """Catat aksi sensitif. Gagal menulis TIDAK boleh menggagalkan endpoint."""
    try:
        supabase.from_("audit_log").insert(
            {
                "actor_id": user.get("id") if user else None,
                "actor_nama": user.get("full_name") if user else None,
                "aksi": aksi,
                "target_tipe": target_tipe,
                "target_id": str(target_id) if target_id else None,
                "detail": detail or {},
                "created_at": get_now_iso(),
            }
        ).execute()
    except Exception as exc:
        print(f"[AUDIT] Gagal mencatat {aksi}: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# ROUTER — dipasang paling akhir supaya semua dependensi bersama di atas
# sudah tersedia untuk di-import oleh modul-modul router.
# ---------------------------------------------------------------------------
from routers import (  # noqa: E402
    audit,
    auth,
    stats,
    sepatu,
    transaksi,
    internal,
    payments,
    users,
    drop_points,
    stock,
    expenses,
)

for _router in (
    audit.router,
    auth.router,
    stats.router,
    sepatu.router,
    transaksi.router,
    internal.router,
    payments.router,
    users.router,
    drop_points.router,
    stock.router,
    expenses.router,
):
    app.include_router(_router)
