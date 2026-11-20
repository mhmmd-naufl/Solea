"""Seeder Supabase via httpx langsung (tanpa SDK Python supabase).

Dipakai karena mesin ini memblokir pydantic_core (WDAC). Mengirim data
langsung ke endpoint PostgREST Supabase dengan httpx.

Cara pakai:
    .venv\\Scripts\\python.exe seed_direct.py

Butuh di .env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY (atau SUPABASE_SERVICE_KEY).
"""
from __future__ import annotations

import json
import os
import sys
import uuid

import httpx
from dotenv import load_dotenv

_BASE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_BASE, ".env"))
load_dotenv(os.path.join(_BASE, "privat", ".env"), override=False)

URL = (os.getenv("SUPABASE_URL") or "").rstrip("/")
KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY") or ""
if not URL or not KEY:
    sys.exit("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY tidak ada di .env")

BASE = f"{URL}/rest/v1"
HDR = {
    "apikey": KEY,
    "Authorization": f"Bearer {KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation, resolution=ignore-duplicates",
}


def get(table: str, query: str = ""):
    r = httpx.get(f"{BASE}/{table}", headers={**HDR, "Prefer": "count=exact"}, params={"limit": 500} if not query else {})
    return r.json()


def upsert_shoes(items: list[dict]) -> None:
    """Upsert baris shoes berdasarkan (merk, model)."""
    # Ambil yang sudah ada
    r = httpx.get(f"{BASE}/shoes", headers=HDR, params={"select": "id,merk,model,harga_cuci,harga_min,harga_max,estimasi_hari,status", "limit": 1000})
    rows = r.json()
    peta = {((x.get("merk") or "").strip().lower(), (x.get("model") or "").strip().lower()): x for x in rows}

    inserted = updated = 0
    for it in items:
        key = ((it.get("merk") or "").strip().lower(), (it.get("model") or "").strip().lower())
        lama = peta.get(key)
        if lama:
            payload = {k: v for k, v in it.items() if v is not None}
            payload.pop("id", None)
            r = httpx.patch(f"{BASE}/shoes?id=eq.{lama['id']}", headers=HDR, json=payload)
            if r.status_code in (200, 204):
                updated += 1
                print(f"  [UPDATE] {it['merk']} / {it.get('model','')} -> {it['harga_cuci']}")
        else:
            payload = dict(it)
            payload["id"] = str(uuid.uuid4())
            r = httpx.post(f"{BASE}/shoes", headers=HDR, json=payload)
            if r.status_code in (200, 201, 204):
                inserted += 1
                print(f"  [INSERT] {it['merk']} / {it.get('model','')} -> {it['harga_cuci']}")
            else:
                print(f"  GAGAL insert {it['merk']}: {r.status_code} {r.text[:200]}")
    print(f"\nShoes: {inserted} dibuat, {updated} diperbarui")


def create_admin(phone: str = "6280000000000", nama: str = "Admin", password: str = "admin123") -> None:
    # Hash argon2 sederhana (PBKDF2-SHA256) — backend menerima format lama
    import hashlib, secrets as _secrets
    salt = _secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000)
    phash = f"pbkdf2_sha256$200000${salt}${digest.hex()}"

    # Cek apakah sudah ada
    r = httpx.get(f"{BASE}/profiles", headers=HDR, params={"select": "id,phone", "limit": 1000})
    phones = [x["phone"] for x in r.json()]
    if phone in phones:
        print(f"  Admin {phone} sudah ada, di-update password.")
        r = httpx.patch(
            f"{BASE}/profiles?phone=eq.{phone}",
            headers=HDR,
            json={"full_name": nama, "role": "admin", "is_verified": True, "password_hash": phash},
        )
        print(f"  Update: {r.status_code}")
    else:
        r = httpx.post(f"{BASE}/profiles", headers=HDR, json={
            "id": str(uuid.uuid4()),
            "full_name": nama,
            "phone": phone,
            "role": "admin",
            "is_verified": True,
            "password_hash": phash,
        })
        print(f"  Admin {phone}: {r.status_code} — password: {password}")


def seed_stock() -> None:
    stock = [
        ("Sabun Netral", "bahan", 12, "liter", 5, 45000),
        ("Pemutih Kental", "bahan", 4, "liter", 3, 38000),
        ("Disinfectan", "bahan", 2, "liter", 3, 60000),
        ("Sikat Kasur", "alat", 18, "pcs", 10, 15000),
        ("Kain Microfiber", "bahan", 25, "pcs", 15, 8000),
    ]
    r = httpx.get(f"{BASE}/stock", headers=HDR, params={"select": "id,nama_item", "limit": 500})
    ada = {x["nama_item"].lower() for x in r.json()}
    for nama, tipe, jumlah, satuan, minimum, harga_beli in stock:
        if nama.lower() in ada:
            continue
        httpx.post(f"{BASE}/stock", headers=HDR, json={
            "id": str(uuid.uuid4()),
            "nama_item": nama, "tipe": tipe, "jumlah": jumlah,
            "satuan": satuan, "batas_minimum": minimum, "harga_beli": harga_beli,
        })
    print(f"Stock: {len(stock)} item (skip yang sudah ada)")


def seed_drop_points() -> None:
    dps = [
        ("Outlet Utama", "Alamat outlet utama", "6280000000000"),
        ("Mitra Drop Point", "Alamat drop point mitra", "6280000000000"),
    ]
    r = httpx.get(f"{BASE}/drop_points", headers=HDR, params={"select": "id,nama", "limit": 500})
    ada = {x["nama"].lower() for x in r.json()}
    for nama, alamat, wa in dps:
        if nama.lower() in ada:
            continue
        httpx.post(f"{BASE}/drop_points", headers=HDR, json={
            "id": str(uuid.uuid4()),
            "nama": nama, "alamat": alamat, "wa_contact": wa,
        })
    print(f"Drop points: {len(dps)} (skip yang sudah ada)")


# ---------------------------------------------------------------
# DAFTAR HARGA (salin dari seed_pricelist.py)
# ---------------------------------------------------------------
PRICE_LIST = [
    {"merk": "Fast Cleaning", "model": "Paket Ringan", "harga_cuci": 15000, "harga_min": 15000, "harga_max": 15000, "kelompok": "Cuci Sepatu", "jenis_treatment": "Fast Cleaning", "keterangan_treatment": "Upper, mid-sol, dan leces. Paket ringan."},
    {"merk": "Fast Cleaning", "model": "Paket Berat", "harga_cuci": 20000, "harga_min": 20000, "harga_max": 20000, "kelompok": "Cuci Sepatu", "jenis_treatment": "Fast Cleaning", "keterangan_treatment": "Upper, mid-sol, dan leces. Paket berat."},
    {"merk": "Deep Cleaning", "model": "Non White", "harga_cuci": 25000, "harga_min": 25000, "harga_max": 25000, "kelompok": "Cuci Sepatu", "jenis_treatment": "Deep Cleaning", "keterangan_treatment": "Upper, midsole, outsole, insole, dan leces. Sepatu bukan putih."},
    {"merk": "Deep Cleaning", "model": "White", "harga_cuci": 30000, "harga_min": 30000, "harga_max": 30000, "kelompok": "Cuci Sepatu", "jenis_treatment": "Deep Cleaning", "keterangan_treatment": "Upper, midsole, outsole, insole, dan leces. Sepatu putih."},
    {"merk": "Deep Cleaning", "model": "Whitening", "harga_cuci": 35000, "harga_min": 35000, "harga_max": 35000, "kelompok": "Cuci Sepatu", "jenis_treatment": "Deep Cleaning", "keterangan_treatment": "Deep cleaning plus pemutih untuk warna putih."},
    {"merk": "Suede Treatment", "model": "Standar", "harga_cuci": 30000, "harga_min": 30000, "harga_max": 30000, "kelompok": "Cuci Sepatu", "jenis_treatment": "Suede Treatment", "keterangan_treatment": "Perawatan khusus bahan suede."},
    {"merk": "Kids Shoes", "model": "Standar", "harga_cuci": 15000, "harga_min": 15000, "harga_max": 15000, "kelompok": "Cuci Sepatu", "jenis_treatment": "Kids Shoes", "keterangan_treatment": "Cuci sepatu anak."},
    {"merk": "Unyellowing", "model": "Midsole", "harga_cuci": 30000, "harga_min": 30000, "harga_max": 30000, "kelompok": "Cuci Sepatu", "jenis_treatment": "Unyellowing", "keterangan_treatment": "Mengembalikan warna midsole yang menguning."},
    {"merk": "Slippers", "model": "Standar", "harga_cuci": 15000, "harga_min": 15000, "harga_max": 20000, "kelompok": "Cuci Sepatu", "jenis_treatment": "Slippers", "keterangan_treatment": "Cuci sandal dan slipper."},
    {"merk": "Bag Cleaning", "model": "Very Small", "harga_cuci": 15000, "harga_min": 15000, "harga_max": 15000, "kelompok": "Bag, Hat & Helmet", "jenis_treatment": "Bag Cleaning", "keterangan_treatment": "Tas sangat kecil. Termasuk dompet."},
    {"merk": "Bag Cleaning", "model": "Small", "harga_cuci": 20000, "harga_min": 20000, "harga_max": 20000, "kelompok": "Bag, Hat & Helmet", "jenis_treatment": "Bag Cleaning", "keterangan_treatment": "Tas kecil. Termasuk dompet."},
    {"merk": "Bag Cleaning", "model": "Medium", "harga_cuci": 30000, "harga_min": 30000, "harga_max": 30000, "kelompok": "Bag, Hat & Helmet", "jenis_treatment": "Bag Cleaning", "keterangan_treatment": "Tas ukuran sedang."},
    {"merk": "Bag Cleaning", "model": "Big", "harga_cuci": 40000, "harga_min": 40000, "harga_max": 40000, "kelompok": "Bag, Hat & Helmet", "jenis_treatment": "Bag Cleaning", "keterangan_treatment": "Tas besar."},
    {"merk": "Hat Cleaning", "model": "Standar", "harga_cuci": 15000, "harga_min": 15000, "harga_max": 20000, "kelompok": "Bag, Hat & Helmet", "jenis_treatment": "Hat Cleaning", "keterangan_treatment": "Cuci topi."},
    {"merk": "Helmet Cleaning", "model": "Anak-anak", "harga_cuci": 15000, "harga_min": 15000, "harga_max": 15000, "kelompok": "Bag, Hat & Helmet", "jenis_treatment": "Helmet Cleaning", "keterangan_treatment": "Cuci helm ukuran anak."},
    {"merk": "Helmet Cleaning", "model": "Dewasa", "harga_cuci": 30000, "harga_min": 30000, "harga_max": 30000, "kelompok": "Bag, Hat & Helmet", "jenis_treatment": "Helmet Cleaning", "keterangan_treatment": "Cuci helm dewasa."},
    {"merk": "Helmet Cleaning", "model": "Fullface", "harga_cuci": 40000, "harga_min": 40000, "harga_max": 50000, "kelompok": "Bag, Hat & Helmet", "jenis_treatment": "Helmet Cleaning", "keterangan_treatment": "Cuci helm fullface."},
    {"merk": "Shoes Repaint", "model": "Upper Canvas/Mesh", "harga_cuci": 80000, "harga_min": 80000, "harga_max": 150000, "kelompok": "Repaint & Reglue", "jenis_treatment": "Shoes Repaint", "keterangan_treatment": "Cat ulang upper canvas/mesh."},
    {"merk": "Shoes Repaint", "model": "Upper Suede", "harga_cuci": 100000, "harga_min": 100000, "harga_max": 200000, "kelompok": "Repaint & Reglue", "jenis_treatment": "Shoes Repaint", "keterangan_treatment": "Cat ulang upper suede."},
    {"merk": "Shoes Repaint", "model": "Upper Leather", "harga_cuci": 110000, "harga_min": 110000, "harga_max": 200000, "kelompok": "Repaint & Reglue", "jenis_treatment": "Shoes Repaint", "keterangan_treatment": "Cat ulang upper leather."},
    {"merk": "Shoes Repaint", "model": "Midsole", "harga_cuci": 50000, "harga_min": 50000, "harga_max": 100000, "kelompok": "Repaint & Reglue", "jenis_treatment": "Shoes Repaint", "keterangan_treatment": "Cat ulang midsole."},
    {"merk": "Shoes Reglue", "model": "Kerusakan di Bawah 50%", "harga_cuci": 50000, "harga_min": 50000, "harga_max": 60000, "kelompok": "Repaint & Reglue", "jenis_treatment": "Shoes Reglue", "keterangan_treatment": "Perekat ulang < 50% area sol."},
    {"merk": "Shoes Reglue", "model": "Full Press", "harga_cuci": 70000, "harga_min": 70000, "harga_max": 120000, "kelompok": "Repaint & Reglue", "jenis_treatment": "Shoes Reglue", "keterangan_treatment": "Perekat ulang full press, > 50%."},
    {"merk": "Jahit Sol", "model": "Standar", "harga_cuci": 25000, "harga_min": 25000, "harga_max": 50000, "kelompok": "Repaint & Reglue", "jenis_treatment": "Jahit Sol", "keterangan_treatment": "Jahit sol sepatu yang robek."},
]


def main() -> None:
    print("Menyambung ke Supabase ...")
    upsert_shoes(PRICE_LIST)
    create_admin()
    seed_stock()
    seed_drop_points()
    print("\nSelesai. Cek dashboard: 24 layanan + 1 admin + stock + drop point.")


if __name__ == "__main__":
    main()
