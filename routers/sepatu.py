"""Router sepatu — diekstrak dari main.py.

Semua dependensi bersama tetap tinggal di main.py; router dipasang
dari bawah main.py setelah modul itu selesai dimuat.
"""

from fastapi import APIRouter

router = APIRouter()

from main import (  # noqa: E402
    Depends,
    HTTPException,
    List,
    Optional,
    Response,
    ShoeCreate,
    ShoeResponse,
    ShoeUpdate,
    _cari_teks,
    _halaman_berpaginan,
    _kelompok_bersih,
    _pola_ilike,
    get_now_iso,
    require_role,
    status,
    supabase,
)

def _rapikan_harga_master(data: dict, gabung: bool = False) -> dict:
    """
    Samakan `harga_cuci` dan `harga_min` supaya booking tidak pernah ambigu.

    `harga_cuci` kolom yang lama dan masih dibaca endpoint lain. Kalau layanan
    punya rentang, `harga_cuci` harus ikut jadi harga terendah -- kalau tidak,
    satu layanan bisa tampil sebagai "Rp150.000" di katalog tapi dicatat
    "Rp80.000" di transaksi.

    `gabung=True` dipakai saat update: baris yang tidak dikirim frontend ikut
    ikut diperhitungkan, jadi UPDATE parsial tidak merusak konsistensi.
    """
    if gabung:
        if data.get("harga_max") is not None and data.get("harga_min") is None:
            # Admin hanya mengisi batas atas -> harga awal = harga_cuci lama.
            data["harga_min"] = data.get("harga_cuci")
        if data.get("harga_min") is not None:
            data["harga_cuci"] = data["harga_min"]
        return data

    if data.get("harga_max") is not None:
        if data.get("harga_min") is None:
            data["harga_min"] = data["harga_cuci"]
        data["harga_cuci"] = data["harga_min"]
    return data


@router.post("/api/sepatu", response_model=ShoeResponse, status_code=status.HTTP_201_CREATED, tags=["Sepatu"])
def create_sepatu(sepatu: ShoeCreate, _: dict = Depends(require_role("admin"))):
    data = _rapikan_harga_master(sepatu.model_dump())
    data["created_at"] = get_now_iso()
    try:
        result = supabase.from_("shoes").insert(data).execute()
        if not result.data:
            raise HTTPException(500, "Gagal menambah layanan")
        return result.data[0]
    except Exception as e:
        raise HTTPException(500, f"Database error: {str(e)}")

@router.get("/api/sepatu", response_model=List[ShoeResponse], tags=["Sepatu"])
def list_sepatu(
    response: Response,
    aktif_only: bool = True,
    cari_status: Optional[bool] = None,
    q: Optional[str] = None,
    kelompok: Optional[str] = None,
    page: Optional[int] = None,
    per_page: Optional[int] = None,
):
    """
    Master layanan / price list. Tetap publik: daftar harga memang perlu dibaca
    siapa saja, dan endpoint ini sudah terbuka sejak awal.

    `kelompok` menyaring per grup katalog ("Cuci Sepatu", "Bag, Hat & Helmet",
    "Repaint & Reglue") supaya tab di katalog publik cukup menarik satu grup
    per request, bukan semua baris lalu dipilah di browser.

    Dua filter status yang berbeda, sering tertukar:

    - `aktif_only=true` (default) -> hanya yang aktif. Ini yang dipakai katalog
      publik supaya layanan yang dinonaktifkan tidak bocor ke pelanggan.
    - `cari_status=false` -> hanya yang NONAKTIF. Ini yang dipakai panel admin
      supaya ia bisa menemukan layanan yang perlu dihidupkan kembali.
      Kalau `cari_status` diisi, dia mengALAHKAN `aktif_only` -- jadi
      `?cari_status=false` saja sudah cukup untuk mencari yang nonaktif.

    Panel admin yang mau melihat semuanya mengirim keduanya: `aktif_only=false`
    (supaya tidak ada filter) dan `cari_status` tidak diisi sama sekali.
    Kalau `aktif_only` tidak dikirim, default True diam-diam menyembunyikan
    layanan nonaktif -- persis yang tidak diinginkan di panel admin.
    """
    cari = _cari_teks(q)
    grup = _kelompok_bersih(kelompok)

    def bangun() -> object:
        query = supabase.from_("shoes").select("*", count="exact")
        # `cari_status` menang kalau keduanya diisi. Kalau difilter terpisah
        # (aktif_only -> status=true, lalu cari_status -> status=false),
        # PostgREST menyANDkan keduanya dan hasilnya selalu kosong --
        # caller yang sudah menjelaskan status yang dicari tidak perlu juga
        # mengirim `aktif_only`.
        if cari_status is not None:
            query = query.eq("status", cari_status)
        elif aktif_only:
            query = query.eq("status", True)
        if grup:
            query = query.eq("kelompok", grup)
        # `merk` sekarang berisi nama layanan, jadi cari juga di keterangan:
        # orang mengetik "white" atau "sol" dan berharapnya ketemu.
        if cari:
            query = query.or_(
                ",".join(
                    [
                        _pola_ilike("merk", cari),
                        _pola_ilike("model", cari),
                        _pola_ilike("jenis_treatment", cari),
                        _pola_ilike("keterangan_treatment", cari),
                    ]
                )
            )
        # kelompok dulu supaya grup katalog tidak tercampur antar tab, baru
        # merk untuk mengurutkan isi grup. Baris tanpa kelompok (data lama)
        # otomatis ada di paling akhir karena PostgREST Sort NULLS LAST.
        return query.order("kelompok").order("merk")

    if page is None:
        return bangun().execute().data or []
    return _halaman_berpaginan(bangun, page, per_page, response)

@router.get("/api/sepatu/{sepatu_id}", response_model=ShoeResponse, tags=["Sepatu"])
def get_sepatu(sepatu_id: str):
    result = supabase.from_("shoes").select("*").eq("id", sepatu_id).execute()
    if not result.data:
        raise HTTPException(404, "Sepatu tidak ditemukan")
    return result.data[0]

@router.put("/api/sepatu/{sepatu_id}", response_model=ShoeResponse, tags=["Sepatu"])
def update_sepatu(sepatu_id: str, sepatu: ShoeUpdate, _: dict = Depends(require_role("admin"))):
    data = _rapikan_harga_master(sepatu.model_dump(exclude_unset=True), gabung=True)
    if not data:
        raise HTTPException(400, "Tidak ada data yang diupdate")
    result = supabase.from_("shoes").update(data).eq("id", sepatu_id).execute()
    if not result.data:
        raise HTTPException(404, "Sepatu tidak ditemukan")
    return result.data[0]

@router.delete("/api/sepatu/{sepatu_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["Sepatu"])
def delete_sepatu(sepatu_id: str, _: dict = Depends(require_role("admin"))):
    """
    Hapus master sepatu.

    Tidak bisa dihapus kalau masih dipakai transaksi: PostgreSQL menolak dengan
    error foreign key (23503). Itu harus ditulis eksplisit, kalau tidak admin
    melihat "berhasil" padahal datanya masih ada -- atau malah dapat 500 dengan
    pesan bahasa Postgres yang tidak ada artinya buat kasir.
    """
    existing = supabase.from_("shoes").select("id,merk").eq("id", sepatu_id).execute()
    if not existing.data:
        raise HTTPException(404, "Sepatu tidak ditemukan")

    dipakai = supabase.from_("transactions").select("id").eq("shoe_id", sepatu_id).execute().data or []
    if dipakai:
        raise HTTPException(
            409,
            f"Master ini masih dipakai {len(dipakai)} transaksi sehingga tidak bisa dihapus. "
            "Nonaktifkan saja supaya hilang dari katalog tanpa merusak riwayat.",
        )

    supabase.from_("shoes").delete().eq("id", sepatu_id).execute()
    return


# ==========================================
# CRUD TRANSAKSI (TRANSACTIONS) - Core Business
# ==========================================
