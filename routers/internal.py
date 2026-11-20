"""Router internal — diekstrak dari main.py.

Semua dependensi bersama tetap tinggal di main.py; router dipasang
dari bawah main.py setelah modul itu selesai dimuat.
"""

from fastapi import APIRouter

router = APIRouter()

from main import (  # noqa: E402
    HTTPException,
    Header,
    Optional,
    PHOTO_BUCKET,
    Request,
    datetime,
    hmac,
    limiter,
    os,
    timedelta,
    timezone,
    supabase,
)

CLEANUP_TOKEN = os.getenv("CLEANUP_TOKEN", "")
PHOTO_TTL_DAYS = int(os.getenv("PHOTO_TTL_DAYS", "7"))

# Batas aman per run, supaya satu kali panggilan tidak menembak batas
# 1000 objek milik method `remove()` milik Supabase Storage.
MAX_TRANSAKSI_PER_RUN = 500


def _path_dari_url_public(url: str) -> Optional[str]:
    """Ambil path objek dari URL publik Supabase Storage.

    Format yang disimpan kolom foto:
      https://<proj>.supabase.co/storage/v1/object/public/<bucket>/<path>

    Yang dibutuhkan untuk menghapus hanya <path>-nya. URL lama yang tidak
    cocok format ini mengembalikan None dan sengaja dilewati -- lebih baik
    satu file tidak terhapus daripada path salah menghapus file orang lain.
    """
    if not url:
        return None
    penanda = f"/object/public/{PHOTO_BUCKET}/"
    posisi = url.find(penanda)
    if posisi == -1:
        return None
    path = url[posisi + len(penanda):]
    # Path kosong atau berisi titik-slash berarti URL aneh -- jangan sentuh.
    if not path or path.startswith("/") or ".." in path:
        return None
    return path


@router.post("/internal/cleanup-fotos", tags=["Internal"])
@limiter.limit("5/hour")
def cleanup_fotos(request: Request, x_cleanup_token: Optional[str] = Header(default=None)):
    """Hapus foto transaksi yang sudah melewati masa retensi.

    Dipanggil cron GitHub Actions, bukan oleh user. Karena itu autentikasinya
    BUKAN JWT (tidak ada user yang login), melainkan token statis lewat header
    `X-Cleanup-Token` yang dibandingkan dengan env CLEANUP_TOKEN.

    Pakai hmac.compare_digest, bukan ==: perbandingan biasa bisa membocorkan
    panjang token lewat waktu respons (timing attack).
    """
    if not CLEANUP_TOKEN:
        raise HTTPException(
            503,
            "CLEANUP_TOKEN belum diisi di .env backend. Isi dulu sebelum cron diaktifkan.",
        )
    if not x_cleanup_token or not hmac.compare_digest(x_cleanup_token, CLEANUP_TOKEN):
        # Pesan sengaja umum: tidak membocorkan apakah token-nya salah
        # format atau server-nya memang belum dikonfigurasi.
        raise HTTPException(401, "Token cleanup tidak valid")

    batas = (datetime.now(timezone.utc) - timedelta(days=PHOTO_TTL_DAYS)).isoformat()

    # Kolom foto yang tidak NULL. Pakai atau_() karena satu transaksi bisa
    # punya lebih dari satu jenis foto.
    hasil = (
        supabase.from_("transactions")
        .select("id, kode, photo_before, photo_after, photo_defect")
        .lte("updated_at", batas)
        .or_(
            "photo_before.not.is.null,"
            "photo_after.not.is.null,"
            "photo_defect.not.is.null"
        )
        .limit(MAX_TRANSAKSI_PER_RUN)
        .execute()
    )

    rows = hasil.data or []
    path_per_transaksi: dict[str, list[str]] = {}
    kolom_per_transaksi: dict[str, list[str]] = {}

    for row in rows:
        paths: list[str] = []
        kolom_terisi: list[str] = []
        for kolom in ("photo_before", "photo_after", "photo_defect"):
            path = _path_dari_url_public(row.get(kolom))
            if path:
                paths.append(path)
                kolom_terisi.append(kolom)
        if paths:
            path_per_transaksi[row["id"]] = paths
            kolom_per_transaksi[row["id"]] = kolom_terisi

    semua_path = [p for paths in path_per_transaksi.values() for p in paths]

    dihapus: list[str] = []
    gagal: list[str] = []

    # Batch per 500: batas riil Supabase adalah 1000 objek per panggilan.
    for i in range(0, len(semua_path), 500):
        batch = semua_path[i:i + 500]
        try:
            res = supabase.storage.from_(PHOTO_BUCKET).remove(batch)
            # Supabase-py versi lama mengembalikan objek dengan .error,
            # versi baru melempar exception. Keduanya ditangani.
            if hasattr(res, "error") and res.error:
                raise Exception(res.error.message)
            dihapus.extend(batch)
        except Exception as e:
            gagal.extend(batch)
            # Log kecil supaya kegagalan terlihat di output cron GitHub Actions.
            print(f"[cleanup] gagal hapus batch {len(batch)} file: {e}")

    # Kosongkan kolom hanya untuk transaksi yang file-nya benar-benar hilang
    # dari storage. Kalau dihapus duluan kolomnya, file yang gagal dihapus
    # jadi yatim: tidak ada jejak di database tapi masih makan kuota 1 GB.
    path_dihapus = set(dihapus)
    for transaksi_id, paths in path_per_transaksi.items():
        if not any(p in path_dihapus for p in paths):
            continue
        try:
            supabase.from_("transactions").update(
                {kolom: None for kolom in kolom_per_transaksi[transaksi_id]}
            ).eq("id", transaksi_id).execute()
        except Exception as e:
            print(f"[cleanup] gagal kosongkan kolom transaksi {transaksi_id}: {e}")

    return {
        "retensi_hari": PHOTO_TTL_DAYS,
        "cutoff": batas,
        "transaksi_diperiksa": len(rows),
        "file_dihapus": len(dihapus),
        "file_gagal": len(gagal),
    }
