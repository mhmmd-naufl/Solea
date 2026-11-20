"""
Atur akun produksi sesuai konfigurasi environment (bukan data merek tertentu).

Variabel yang dipakai (semua opsional, lihat .env.example):
  ADMIN_PHONE         - nomor admin yang dipertahankan
  ADMIN_HAPUS_PHONE   - nomor admin lama yang ingin dihapus (kosong = lewati)
  DP_PHONE            - nomor akun drop point mitra
  DP_NAMA             - nama akun drop point mitra
  DP_PASSWORD_DEFAULT - password awal akun mitra (wajib diganti setelah login)
  BUSINESS_ADDRESS    - alamat untuk drop point "Outlet Utama"

Idempoten: dijalankan ulang tidak menduplikasi apa pun.
"""
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

ADMIN_SISA_PHONE = os.getenv("ADMIN_PHONE", "6280000000000").strip()
ADMIN_HAPUS_PHONE = os.getenv("ADMIN_HAPUS_PHONE", "").strip()
DP_PHONE = os.getenv("DP_PHONE", "6280000000000").strip()
DP_NAMA = os.getenv("DP_NAMA", "Mitra Drop Point").strip()
DP_PASSWORD_DEFAULT = os.getenv("DP_PASSWORD_DEFAULT", "Password123")

# Import dari main supaya hashing identik dengan yang dibaca endpoint login.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from main import hash_password, verify_password  # noqa: E402

url = os.getenv("SUPABASE_URL", "").strip()
key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")
if not url or not key:
    sys.exit("ERROR: SUPABASE_URL dan SUPABASE_SERVICE_ROLE_KEY wajib diisi di .env")
c = create_client(url, key)

# --- 1. Hapus admin lama hanya kalau nomornya memang dikonfigurasi ---
if not ADMIN_HAPUS_PHONE:
    print("ADMIN_HAPUS_PHONE kosong — tidak ada akun yang dihapus.")
else:
    hapus = c.from_("profiles").select("id, full_name").eq("phone", ADMIN_HAPUS_PHONE).execute().data or []
    for row in hapus:
        c.from_("profiles").delete().eq("id", row["id"]).execute()
        print("Admin dihapus: %s (%s)" % (row["full_name"], ADMIN_HAPUS_PHONE))
    if not hapus:
        print("Tidak ada admin %s untuk dihapus (sudah bersih)." % ADMIN_HAPUS_PHONE)

sisa = c.from_("profiles").select("id, full_name, phone").eq("role", "admin").execute().data or []
print("Admin tersisa: %d -> %s" % (
    len(sisa), ", ".join("%s (%s)" % (a["full_name"], a["phone"]) for a in sisa)))

# --- 2. Akun drop point mitra ---
ada = c.from_("profiles").select("id, full_name, role").eq("phone", DP_PHONE).execute().data or []
if ada:
    print("Akun drop point sudah ada: %s (%s) — tidak diubah." % (ada[0]["full_name"], DP_PHONE))
else:
    kasih = hash_password(DP_PASSWORD_DEFAULT)
    ok_login = verify_password(DP_PASSWORD_DEFAULT, kasih)
    if not ok_login:
        sys.exit("ABORT: hash password gagal verifikasi silang — tidak menulis ke database.")
    c.from_("profiles").insert({
        "full_name": DP_NAMA,
        "phone": DP_PHONE,
        "password_hash": kasih,
        "role": "drop_point",
        "is_verified": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }).execute()
    print("Akun dibuat: %s | %s | password default %s" % (DP_NAMA, DP_PHONE, DP_PASSWORD_DEFAULT))

# --- 3. Outlet utama sebagai titik penjemputan ---
# Alamat mengikuti BUSINESS_ADDRESS di .env supaya hanya ada satu sumber kebenaran.
OUTLET_NAMA = "Outlet Utama"
OUTLET_ALAMAT = (
    os.getenv("BUSINESS_ADDRESS", "").strip()
    or "Alamat outlet utama belum diisi di BUSINESS_ADDRESS"
)
ada_outlet = c.from_("drop_points").select("id, alamat").eq("nama", OUTLET_NAMA).execute().data or []
if ada_outlet:
    print("Drop point '%s' sudah ada — tidak diubah." % OUTLET_NAMA)
else:
    c.from_("drop_points").insert({
        "nama": OUTLET_NAMA,
        "alamat": OUTLET_ALAMAT,
        "wa_contact": ADMIN_SISA_PHONE,
        "aktif": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }).execute()
    print("Drop point ditambahkan: %s | %s" % (OUTLET_NAMA, OUTLET_ALAMAT))

# --- 4. Verifikasi akhir ---
prof = c.from_("profiles").select("full_name, phone, role").execute().data or []
dp_points = c.from_("drop_points").select("nama, alamat").execute().data or []
print("\nAKHIR:")
for p in prof:
    print("  %-10s | %-26r | %s" % (p["role"], p["full_name"], p["phone"]))
for d in dp_points:
    print("  drop_point | %-26r | %s" % (d["nama"], d["alamat"]))
