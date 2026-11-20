"""Router transaksi — diekstrak dari main.py.

Semua dependensi bersama tetap tinggal di main.py; router dipasang
dari bawah main.py setelah modul itu selesai dimuat.
"""

from fastapi import APIRouter, BackgroundTasks

from config import BUSINESS_NAME
from notifications import kirim_wa, pesan_booking, pesan_status

router = APIRouter()

from main import (  # noqa: E402
    ACCEPTED_PAYMENTS,
    BaseModel,
    Depends,
    Field,
    HTTPException,
    List,
    Optional,
    PHOTO_BUCKET,
    limiter,
    Request,
    Response,
    TransactionCreate,
    TransactionResponse,
    TransactionStatusUpdate,
    _ALIAS_BAYAR,
    _KOLOM_SHOE,
    _cari_teks,
    _halaman_berpaginan,
    _sertakan_kontak,
    _siapkan_transaksi,
    _waktu_utc,
    generate_tracking_code,
    get_current_user,
    get_now_iso,
    komisi_untuk,
    status,
    supabase,
    catat_audit,
)


def _telepon_user(user_id: str | None) -> str | None:
    """Ambil nomor WA pemilik transaksi untuk notifikasi (best-effort)."""
    if not user_id:
        return None
    try:
        baris = supabase.from_("profiles").select("phone").eq("id", user_id).execute()
        return baris.data[0]["phone"] if baris.data else None
    except Exception:
        return None


@router.post("/api/transaksi", response_model=TransactionResponse, status_code=status.HTTP_201_CREATED, tags=["Transaksi"])
@limiter.limit("10/hour")
def create_transaksi(
    request: Request,
    transaksi: TransactionCreate,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    """
    Booking. Konsumen hanya boleh membuat atas namanya sendiri -- user_id
    diambil dari token, bukan dari payload, supaya tidak bisa memesan
    behalf orang lain. Admin/teknisi/drop point boleh behalf.
    """
    # payment_reference/status bukan input klien — diisi adapter pembayaran.
    data = transaksi.model_dump(exclude={"payment_reference", "payment_status"})
    if user["role"] == "customer":
        data["user_id"] = user["id"]
    else:
        # Admin/teknisi/drop_point boleh create untuk user lain
        if "user_id" not in data or data.get("user_id") is None:
            raise HTTPException(400, "user_id wajib diisi untuk role non-customer")

    # Harga selalu mengikuti master, jangan dikasih dari client.
    shoe = (
        supabase.from_("shoes")
        .select("id, harga_cuci, harga_min, status")
        .eq("id", data["shoe_id"])
        .execute()
    )
    if not shoe.data:
        raise HTTPException(400, "Layanan tidak ditemukan")
    if not shoe.data[0]["status"]:
        raise HTTPException(400, "Layanan ini sedang tidak tersedia")

    # Layanan ber-harga-rentang (mis. Repaint 80.000-150.000) dicatat dari
    # harga terendah. Harga akhir ditentukan admin di outlet lewat
    # PUT /api/transaksi/{id}/harga, jadi harga saat booking selalu yang
    # paling murah dan konsumen melihat catatan "mulai dari".
    master = shoe.data[0]
    data["harga"] = master.get("harga_min") or master["harga_cuci"]

    # Jenis + nama barang wajib: tanpa keduanya, teknisi saat menerima barang
    # cuma menemukan "Deep Cleaning" tanpa tahu pasangan mana dari rak yang
    # jadi miliknya, dan datanya tidak muncul di laporan per jenis. Nama kolom
    # tetap jenis_sepatu/nama_sepatu (sejarah), tapi isinya barang apa pun:
    # sepatu, tas, helm, topi -- label di frontend yang menyesuaikan.
    # Wajib untuk semua role, termasuk admin yang create behalf konsumen.
    jenis_sepatu = (data.get("jenis_sepatu") or "").strip()
    if not jenis_sepatu:
        raise HTTPException(
            400,
            "Jenis barang wajib dipilih sebelum order (cth. Sneakers).",
        )
    if len(jenis_sepatu) > 50:
        raise HTTPException(400, "Jenis barang maksimal 50 karakter.")
    data["jenis_sepatu"] = jenis_sepatu

    nama_sepatu = (data.get("nama_sepatu") or "").strip()
    if not nama_sepatu:
        raise HTTPException(
            400,
            "Nama barang wajib diisi sebelum order (cth. 'Nike Air Force 1').",
        )
    if len(nama_sepatu) > 100:
        raise HTTPException(400, "Nama barang maksimal 100 karakter.")
    data["nama_sepatu"] = nama_sepatu

    # Metode bayar: normalisasi alias lalu tolak yang tidak diterima outlet.
    # Dulu pilihan ini hanya hiasan UI -- tidak dikirim ke backend sama sekali,
    # jadi admin tidak pernah tahu konsumen mau bayar lewat apa.
    pm = (data.get("payment_method") or "").strip().lower() or None
    if pm:
        pm = _ALIAS_BAYAR.get(pm, pm)
        if ACCEPTED_PAYMENTS and pm not in ACCEPTED_PAYMENTS:
            raise HTTPException(
                400,
                f"Metode pembayaran '{pm}' tidak diterima. Pilihan: {', '.join(ACCEPTED_PAYMENTS)}",
            )
    data["payment_method"] = pm

    # grup_id: harus UUID valid kalau diisi. Frontend membuatnya per batch
    # booking multi-pasang; nilai ngawur ditolak supaya kolom ini tetap
    # bisa diandalkan untuk mengelompokkan.
    if data.get("grup_id"):
        try:
            data["grup_id"] = str(uuid.UUID(str(data["grup_id"])))
        except (ValueError, AttributeError):
            raise HTTPException(400, "grup_id harus berupa UUID")
    else:
        data["grup_id"] = None

    data["status"] = "Diterima"
    data["tech_commission"] = komisi_untuk(data["harga"], data.get("tech_id"))
    data["kode"] = generate_tracking_code()
    data["created_at"] = get_now_iso()

    try:
        result = supabase.from_("transactions").insert(data).execute()
        if not result.data:
            raise HTTPException(500, "Gagal membuat transaksi")

        no_wa = (
            user.get("phone")
            if user["role"] == "customer"
            else _telepon_user(data.get("user_id"))
        )
        background_tasks.add_task(
            kirim_wa, no_wa, pesan_booking(data["kode"], BUSINESS_NAME)
        )
        return result.data[0]
    except HTTPException:
        raise
    except Exception as e:
        # PGRST204 = kolom di payload belum ada di database (migrasi belum
        # dijalankan). Terjemahkan jadi pesan yang bisa ditindaklanjuti --
        # "Could not find the 'payment_method' column ... in the schema cache"
        # mentah tidak memberi konsumen/admin tahu harus berbuat apa.
        pesan = str(e)
        if "PGRST204" in pesan or "schema cache" in pesan:
            raise HTTPException(
                503,
                "Database belum dimigrasi (ada kolom baru yang belum dibuat). "
                "Jalankan migrate.sql di Supabase SQL Editor, lalu coba lagi.",
            )
        raise HTTPException(500, f"Database error: {pesan}")

def _bisa_lihat(user: dict, trx: dict) -> bool:
    """Cek apakah user berhak melihat transaksi ini."""
    if user["role"] in ("admin", "drop_point"):
        return True
    if user["role"] == "customer":
        return trx.get("user_id") == user["id"]
    if user["role"] == "technician":
        return trx.get("tech_id") == user["id"]
    return False

@router.get("/api/transaksi", response_model=List[TransactionResponse], tags=["Transaksi"])
def list_transaksi(
    response: Response,
    user: dict = Depends(get_current_user),
    status: Optional[str] = None,
    tech_id: Optional[str] = None,
    drop_point_id: Optional[str] = None,
    user_id: Optional[str] = None,
    q: Optional[str] = None,
    dari: Optional[str] = None,
    sampai: Optional[str] = None,
    dari_selesai: Optional[str] = None,
    sampai_selesai: Optional[str] = None,
    grup_id: Optional[str] = None,
    urut: str = "terbaru",
    limit: Optional[int] = None,
    page: Optional[int] = None,
    per_page: Optional[int] = None,
):
    """
    Daftar transaksi. Konsumen hanya melihat miliknya sendiri, teknisi hanya
    yang ditugaskan ke dirinya. Filter user_id/tech_id dari query diabaikan
    untuk non-admin supaya tidak bisa dipakai menembak data orang lain.

    `q` mencari di kolom kode (TRX-XXXXXX). Pencarian nama pelanggan harus lewat
    filter user_id, karena PostgREST tidak bisa dicari lewat relasi di or_.

    ADA DUA PASANGAN FILTER TANGGAL, dan bedanya penting:

      - `dari` / `sampai`            -> tanggal MASUK (created_at). Dipakai untuk
                                        melihat antrean: "yang masuk sejak tanggal
                                        berapa".
      - `dari_selesai` / `sampai_selesai` -> tanggal SELESAI (selesai_at), diisi
                                        teknisi. Dipakai untuk laporan: "yang
                                        selesai di bulan ini".

    Yang kedua sengaja dipisah, bukan digabung dengan `dari`. Kalau keduanya
    dipakai bersama-sama, hasilnya baris yang masuk DAN selesai di rentang itu --
    untuk laporan omzet yang salah, karena cucian yang masuk bulan lalu lalu
    selesai bulan ini harus ikut dihitung bulan ini.

    `urut`: "terbaru" (default), "terlama", "nilai_tinggi", "nilai_rendah".
    """
    if user["role"] not in ("admin", "drop_point"):
        tech_id = None
        user_id = None

    cari = _cari_teks(q)

    # Bentuk select: nama sepatu dan teknisi selalu ikut (bukan data sensitif,
    # dan tanpa itu tabel admin cuma menampilkan UUID yang tidak berguna).
    # Kontak pelanggan TIDAK ikut lewat embed; itu ditangani terpisah oleh
    # _sertakan_kontak supaya aturan privasi tetap di satu tempat.
    kolom = "*,shoe:shoes(" + _KOLOM_SHOE + "),tech:profiles!transactions_tech_id_fkey(id, full_name)"

    def bangun() -> object:
        query = supabase.from_("transactions").select(kolom, count="exact")

        # Kunci baris ke pemilik
        if user["role"] == "customer":
            query = query.eq("user_id", user["id"])
        elif user["role"] == "technician":
            query = query.eq("tech_id", user["id"])

        if status:
            query = query.eq("status", status)
        if tech_id == "null":
            # "Belum ditugaskan". PostgREST membedakan string "null" dari SQL
            # NULL, jadi eq("tech_id", "null") akan mencari literal 'null' dan
            # selalu kosong -- harus lewat is_(). Filter ini tetap aman untuk
            # non-admin karena tech_id sudah dipaksa None di atas.
            query = query.is_("tech_id", "null")
        elif tech_id:
            query = query.eq("tech_id", tech_id)
        if drop_point_id:
            query = query.eq("drop_point_id", drop_point_id)
        if user_id:
            query = query.eq("user_id", user_id)
        if grup_id:
            # Booking multi-pasang: semua transaksi satu sesi. Scope peran di
            # atas tetap berlaku, jadi konsumen hanya melihat grup miliknya.
            query = query.eq("grup_id", grup_id)
        if cari:
            query = query.ilike("kode", f"%{cari}%")
        if dari:
            query = query.gte("created_at", dari)
        if sampai:
            query = query.lte("created_at", sampai)
        # Tanggal selesai. `lt()` dipakai (bukan `lte`) supaya baris yang
        # selesai tepat pada 1 pukul 00:00 bulan berikutnya tidak ikut terhitung
        # di bulan yang sedang ditutup. Batas atas dikirim sebagai "tanggal
        # berikutnya", bukan tanggal terakhir.
        if dari_selesai:
            query = query.gte("selesai_at", dari_selesai)
        if sampai_selesai:
            query = query.lt("selesai_at", sampai_selesai)

        if urut == "terlama":
            return query.order("created_at")
        if urut == "nilai_tinggi":
            return query.order("harga", desc=True).order("created_at", desc=True)
        if urut == "nilai_rendah":
            return query.order("harga").order("created_at", desc=True)
        return query.order("created_at", desc=True)

    if page is None:
        query = bangun().limit(limit) if limit else bangun()
        return _siapkan_transaksi(query.execute().data or [], user)
    return _siapkan_transaksi(
        _halaman_berpaginan(bangun, page, per_page, response), user
    )

@router.get("/api/transaksi/tersedia", response_model=List[TransactionResponse], tags=["Transaksi"])
def get_tersedia_transaksi(
    user: dict = Depends(get_current_user),
):
    """
    Daftar transaksi dengan status Diterima yang belum dipilih teknisi (tech_id IS NULL).
    Urutan FIFO (First In First Out): yang dulu datenya dikerjakan dulu.
    Hanya lihat teknisi admin/mitra, teknisi lihat miliknya sendiri.
    """
    query = supabase.from_("transactions").select(
        "*,shoe:shoes(" + _KOLOM_SHOE + "),tech:profiles!transactions_tech_id_fkey(id, full_name)"
    )

    if user["role"] == "customer":
        query = query.eq("user_id", user["id"])
    elif user["role"] == "technician":
        # Teknisi hanya milikannya (tech_id IS NULL AND status = Diterima)
        query = query.is_("tech_id", "null").eq("status", "Diterima")
    else:
        # Admin/mitra: semua (tech_id IS NULL AND status = Diterima)
        query = query.is_("tech_id", "null").eq("status", "Diterima")

    query = query.order("created_at", desc=False)
    try:
        trx = query.execute().data or []
        return _siapkan_transaksi(trx, user)
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"ERROR in get_tersedia_transaksi: {e}")
        print(tb)
        raise HTTPException(500, f"Database error: {type(e).__name__}: {str(e)}")


@router.get("/api/transaksi/{transaksi_id}", response_model=TransactionResponse, tags=["Transaksi"])
def get_transaksi(transaksi_id: str, user: dict = Depends(get_current_user)):
    result = supabase.from_("transactions").select("*").eq("id", transaksi_id).execute()
    if not result.data:
        raise HTTPException(404, "Transaksi tidak ditemukan")
    if not _bisa_lihat(user, result.data[0]):
        raise HTTPException(403, "Kamu tidak punya akses ke transaksi ini")
    return _siapkan_transaksi([result.data[0]], user)[0]

@router.get("/api/transaksi/tracking/{kode}", tags=["Transaksi"])
@limiter.limit("30/minute")
def tracking_transaksi(request: Request, kode: str):
    """Cek status pakai nomor tracking (TRX-XXXXXX) — dipakai halaman publik."""
    result = (
        supabase.from_("transactions")
        .select(
            "*, shoes(" + _KOLOM_SHOE + "), drop_points(nama, alamat, wa_contact)"
        )
        .eq("kode", kode.strip().upper())
        .execute()
    )
    if not result.data:
        raise HTTPException(404, "Nomor booking tidak ditemukan")

    trx = result.data[0]
    return {
        "kode": trx.get("kode"),
        "status": trx["status"],
        "harga": trx["harga"],
        "created_at": _waktu_utc(trx["created_at"]),
        "updated_at": _waktu_utc(trx.get("updated_at")),
        # Tanggal selesai dicatat teknisi. Konsumen butuh ini untuk tahu kapan
        # cuciannya rampung, jadi sengaja ikut ke halaman publik.
        "selesai_at": _waktu_utc(trx.get("selesai_at")),
        "catatan_konsumen": trx.get("catatan_konsumen"),
        "payment_method": trx.get("payment_method"),
        # Ada untuk booking multi-pasang: halaman status memakainya untuk
        # menampilkan pasangan lain dalam booking yang sama.
        "grup_id": trx.get("grup_id"),
        "photo_before": trx.get("photo_before"),
        "photo_after": trx.get("photo_after"),
        "photo_defect": trx.get("photo_defect"),
        "shoes": trx.get("shoes"),
        "drop_point": trx.get("drop_points"),
    }


@router.get("/api/transaksi/grup/{grup_id}", tags=["Transaksi"])
def tracking_grup(grup_id: str):
    """
    Semua pasangan dalam satu booking multi-pasang (publik, data minimal).

    Sengaja hanya kode + status + nama layanan: cukup untuk halaman status
    menampilkan "booking ini berisi 3 pasang", tanpa membuka harga atau
    data lain milik sesi booking orang.
    """
    try:
        grup = str(uuid.UUID(grup_id))
    except (ValueError, AttributeError):
        raise HTTPException(400, "grup_id harus berupa UUID")

    result = (
        supabase.from_("transactions")
        .select("kode, status, shoes(merk, model)")
        .eq("grup_id", grup)
        .order("created_at")
        .execute()
    )
    if not result.data:
        raise HTTPException(404, "Booking grup tidak ditemukan")
    return [
        {"kode": t.get("kode"), "status": t.get("status"), "shoes": t.get("shoes")}
        for t in result.data
    ]

@router.put("/api/transaksi/{transaksi_id}/status", response_model=TransactionResponse, tags=["Transaksi"])
def update_transaksi_status(
    transaksi_id: str,
    update: TransactionStatusUpdate,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    """
    Ubah status. Hanya teknisi yang ditugaskan ke transaksi ini, atau admin.
    Konsumen dan drop point tidak boleh mengubah status.
    """
    # Cek transaksi ada
    existing = supabase.from_("transactions").select("*").eq("id", transaksi_id).execute()
    if not existing.data:
        raise HTTPException(404, "Transaksi tidak ditemukan")

    current = existing.data[0]

    if user["role"] == "admin":
        pass  # admin boleh semua
    elif user["role"] == "technician":
        # Teknisi hanya boleh transaksi yang ditugaskan ke dirinya.
        # Kalau belum ada teknisi (status Diterima), teknisi boleh ambil
        # sendiri dengan mengassign tech_id = id-nya.
        if current.get("tech_id") not in (None, user["id"]):
            raise HTTPException(403, "Transaksi ini bukan tugasmu")
        # Selalu paksa ke id sendiri, bukan hanya saat masih kosong:
        # kalau dibiarkan, teknisi bisa mengirim tech_id orang lain lewat
        # payload dan komisi jatuh ke pengerja yang salah.
        update.tech_id = user["id"]
    else:
        raise HTTPException(403, "Kamu tidak punya akses untuk mengubah status")

    data = update.model_dump(exclude_unset=True)
    data["updated_at"] = get_now_iso()

    # Komisi selalu mengikuti pengerja terkini. Jalur ini bisa mengisi
    # tech_id untuk pertama kali (teknisi ambil tugas dari halaman status),
    # jadi 40% harus dihitung di sini juga -- kalau tidak, transaksi yang
    # diambil lewat jalur ini tersimpan dengan komisi 0.
    if "tech_id" in data:
        data["tech_commission"] = komisi_untuk(
            current.get("harga") or 0, data.get("tech_id")
        )

    # Tanggal selesai dicatat di sini, bukan dari frontend, supaya tidak bisa
    # dipalsukan dan selalu konsisten dengan kapan status benar-benar berubah.
    #
    #   masuk final  -> isi sekali, lalu JANGAN diubah lagi saat status naik
    #                  dari Selesai ke Siap diambil (tanggal CuomoCI yang
    #                  tercatat di laporan harus tanggal pekerjaannya selesai)
    #   keluar final -> kosongkan lagi; pekerjaan diropan belum selesai
    #
    # Transaksi lama yang sudah berstatus Selesai sebelum kolom ini ada tidak
    # bisa ditebak ulang, jadi selesai_at hanya diisi kalau statusnya memang
    # SEDANG BERUBAH -- biarkan yang sudah lewat tetap NULL.
    #
    # Pengecekan `update.status != current["status"]` ini wajib, bukan sekadar
    # "sudah terisi atau belum". Tanpa itu, teknisi yang menyimpan catatan pada
    # pekerjaan berstatus Selesai (statusnya dikirim ulang tanpa berubah) akan
    # menulis selesai_at = hari ini, dan pekerjaan lama itu tiba-tiba masuk ke
    # omzet bulan ini.
    if update.status in ("Selesai", "Siap diambil"):
        if not current.get("selesai_at") and update.status != current.get("status"):
            data["selesai_at"] = get_now_iso()
    else:
        data["selesai_at"] = None

    # Foto "sesudah" wajib ada (baris ini ATAU yang sudah tersimpan sebelumnya)
    if update.status in ("Selesai", "Siap diambil"):
        has_after = data.get("photo_after") or current.get("photo_after")
        if not has_after:
            raise HTTPException(
                400,
                "Foto setelah (photo_after) wajib diunggah sebelum menandai Selesai/Siap diambil",
            )

    result = supabase.from_("transactions").update(data).eq("id", transaksi_id).execute()
    if not result.data:
        raise HTTPException(404, "Transaksi tidak ditemukan")

    if update.status != current.get("status"):
        background_tasks.add_task(
            kirim_wa,
            _telepon_user(current.get("user_id")),
            pesan_status(current["kode"], update.status, BUSINESS_NAME),
        )

    # WAJIB lewat _siapkan_transaksi: endpoint ini boleh dipanggil teknisi,
    # dan baris mentah dari database masih memuat harga serta tech_commission.
    # Tanpa ini, teknisi membaca commission setiap kali ia update status.
    return _siapkan_transaksi([result.data[0]], user)[0]


@router.post("/api/transaksi/{transaksi_id}/claim", response_model=TransactionResponse, tags=["Transaksi"])
def claim_transaksi(
    transaksi_id: str,
    user: dict = Depends(get_current_user),
):
    """
    Teknisi atau admin mengclaim (mengambil) pekerjaan ini.
    - Hanya teknisi yang ditugaskan ke transaksi, atau admin, boleh claim.
    - Kalau status Diterima dan belum ada teknisi, maka tech_id diisi dan
      status jadi Diproses.
    - Kalau status sudah bukan Diterima, error 403.
    - Mengembalikan transaksi lengkap setelah di-assign.
    """
    # Cek transaksi ada
    existing = supabase.from_("transactions").select("*").eq("id", transaksi_id).execute()
    if not existing.data:
        raise HTTPException(404, "Transaksi tidak ditemukan")

    trx = existing.data[0]

    # Hanya teknisi yang ditugasku atau admin boleh claim
    if user["role"] == "admin":
        pass  # admin boleh claim semua
    elif user["role"] == "technician":
        # Teknisi boleh claim hanya kalau belum diambil, atau kalau itu pekerjaannya sendiri.
        if trx.get("tech_id") not in (None, user["id"]):
            raise HTTPException(409, "Transaksi ini sudah diambil teknisi lain")
        if trx.get("status") != "Diterima":
            raise HTTPException(403, "Hanya transaksi status Diterima yang bisa diclaim")
    else:
        raise HTTPException(403, "Hanya teknisi atau admin yang bisa claim")

    # Assign teknisi dan ubah status ke Diproses. Komisi dihitung di sini
    # juga: claim adalah jalur utama teknisi mengambil pekerjaan, dan tanpa
    # baris tech_commission transaksi tersimpan dengan komisi 0 padahal
    # pengerjanya teknisi (aturan 40%). Admin tetap 0 (kerja sendiri).
    data = {
        "tech_id": user["id"],
        "status": "Diproses",
        "tech_commission": komisi_untuk(trx.get("harga") or 0, user["id"]),
        "updated_at": get_now_iso(),
    }

    result = supabase.from_("transactions").update(data).eq("id", transaksi_id).execute()
    if not result.data:
        raise HTTPException(404, "Transaksi tidak ditemukan")
    # WAJIB lewat _siapkan_transaksi
    return _siapkan_transaksi([result.data[0]], user)[0]


class TransactionPriceUpdate(BaseModel):
    """
    Finalisasi harga transaksi.

    Dipakai admin setelah memeriksa kondisi fisik shoes. Untuk layanan
    ber-harga-rentang (mis. Repaint 80.000-150.000), harga saat booking hanya
    harga terendah, jadi admin perlu menetapkan harga final di outlet.

    `diskon` (opsional) = potongan harga yang admin kasih ke pelanggan (mis.
    promo, member). Komisi teknisi dihitung dari harga BERSIH (harga - diskon),
    bukan harga kotor, supaya potongan promo tidak membuat potongan teknisi
    ikut besar. Diskon tidak boleh melampaui harga.
    """
    harga: int = Field(..., ge=0)
    diskon: Optional[int] = Field(None, ge=0)
    alasan: Optional[str] = None  # contoh: "Sol upper robek, area 5 cm"


@router.put("/api/transaksi/{transaksi_id}/harga", response_model=TransactionResponse, tags=["Transaksi"])
def update_transaksi_harga(
    transaksi_id: str,
    update: TransactionPriceUpdate,
    user: dict = Depends(get_current_user),
):
    """
    Admin-only: tetapkan harga final satu transaksi.

    Menghitung ulang tech_commission dari harga final, memakai aturan
    komisi-berdasarkan-peran: teknisi biasa kena potongan standard
    (default 40%), admin yang dinyatakan mengerjakan sendiri kena 0 --
    sehingga mengganti pengerja sebelum final harga tidak meninggalkan
    komisi yang tak sesuai.

    Ditolak kalau status sudah "Siap diambil": harga saat itu sudah dikunci
    karena pembeli sudah mengambil barang, dan perubahan diam-diam akan
    membuat laporan pendapatan tidak cocok dengan uang yang benar-benar masuk.
    """
    if user["role"] != "admin":
        raise HTTPException(403, "Hanya admin yang boleh menetapkan harga")

    existing = (
        supabase.from_("transactions")
        .select(
            "id, status, tech_id, defect_notes, "
            "shoe:shoes(merk, model, harga_min, harga_max)"
        )
        .eq("id", transaksi_id)
        .execute()
    )
    if not existing.data:
        raise HTTPException(404, "Transaksi tidak ditemukan")
    current = existing.data[0]

    if current.get("status") == "Siap diambil":
        raise HTTPException(400, "Harga tidak bisa diubah setelah Shoes siap diambil")

    # Peringatan rentang, bukan penolakan. Admin boleh tetap memakai harga di
    # luar rentang kalau memang barangnya beda dari deskripsi, tapi jangan
    # sampai terlewat tanpa sadar.
    masters = current.get("shoe")
    if isinstance(masters, list):
        masters = masters[0] if masters else None
    if isinstance(masters, dict):
        batas_min, batas_max = masters.get("harga_min"), masters.get("harga_max")
        if batas_max and not (batas_min <= update.harga <= batas_max):
            label = masters.get("merk", "layanan")
            print(
                f"[harga] {transaksi_id} di luar rentang {label}: "
                f"{update.harga} (daftar {batas_min}-{batas_max})"
            )

    # Diskon: NULL input = biarkan nilai lama / NULL (belum diatur). Kalau
    # diisi, tidak boleh membuat harga bersih jadi negatif, dan komisi
    # dihitung ulang dari harga bersih -- bukan harga kotor.
    bersih = update.harga
    data = {
        "harga": update.harga,
        "updated_at": get_now_iso(),
    }
    if update.diskon is not None:
        if update.diskon > update.harga:
            raise HTTPException(
                400,
                f"Diskon Rp{update.diskon:,} tidak boleh lebih besar dari harga Rp{update.harga:,}".replace(",", "."),
            )
        data["diskon"] = update.diskon
        bersih = update.harga - update.diskon

    data["tech_commission"] = komisi_untuk(bersih, current.get("tech_id"))
    # Catatan alasan disimpan di defect_notes, bukan kolom baru, supaya
    # tidak perlu migrasi. Prefiks supaya jelas itu keputusan admin, bukan
    # catatan cacat dari teknisi.
    if update.alasan:
        lama = current.get("defect_notes") or ""
        data["defect_notes"] = (lama + f"\n[Harga final: {update.alasan}]").strip()

    result = (
        supabase.from_("transactions")
        .update(data)
        .eq("id", transaksi_id)
        .select("*, shoe:shoes(" + _KOLOM_SHOE + "), tech:profiles!transactions_tech_id_fkey(id, full_name)")
        .execute()
    )
    if not result.data:
        raise HTTPException(404, "Transaksi tidak ditemukan")
    catat_audit(
        "ubah_harga",
        "transactions",
        transaksi_id,
        {"dari": current.get("harga"), "ke": data.get("harga")},
        user,
    )
    return _siapkan_transaksi([result.data[0]], user)[0]


# ==========================================
# CRUD PROFILES (USERS)
# ==========================================



# ----------------------------------------------------------------
# ADMIN: Update transaksi (misal ganti teknisi)
# ----------------------------------------------------------------
class TransaksiUpdate(BaseModel):
    tech_id: Optional[str] = None
    # Ganti layanan/varian (kasus "konsumen salah milih treatment di web"):
    # id master shoes yang benar. Harga di-reset dari master baru, komisi
    # ikut dihitung ulang -- bukan angka yang dikirim klien.
    shoe_id: Optional[str] = None

@router.put("/api/transaksi/{transaksi_id}", response_model=TransactionResponse, tags=["Transaksi"])
def update_transaksi(
    transaksi_id: str,
    update: TransaksiUpdate,
    user: dict = Depends(get_current_user),
):
    """
    Admin-only. Ganti field transaksi:

    - tech_id    : id teknisi, atau id admin untuk opsi "admin mengerjakan
                   sendiri" (komisi 0 -- tanpa potongan 40%).
    - tech_id="" : ikat tanpa pengerja (komisi juga nol).
    - shoe_id    : ganti layanan/varian ke master yang benar (kasus konsumen
                   salah memilih di web). Harga di-reset dari layanan baru
                   -- rentang dicatat dari harga terendah, finalnya lagi
                   lewat /harga -- dan komisi dihitung ulang.

    Semua bergantian dihitung ulang di server; peran dan harga tidak pernah
    percaya klien. Ganti layanan ditolak kalau status sudah "Siap diambil":
    uang sudah masuk, perubahan diam-diam akan membuat laporan pendapatan
    tidak cocok dengan realita -- aturan yang sama dengan ganti harga final.
    """
    if user["role"] != "admin":
        raise HTTPException(403, "Hanya admin yang bisa mengubah transaksi")

    existing = supabase.from_("transactions").select("*").eq("id", transaksi_id).execute()
    if not existing.data:
        raise HTTPException(404, "Transaksi tidak ditemukan")

    data = {}
    if update.tech_id is not None and update.tech_id != "":
        tech = (
            supabase.from_("profiles")
            .select("id, role")
            .eq("id", update.tech_id)
            .execute()
        )
        if not tech.data:
            raise HTTPException(400, "Teknisi tidak ditemukan")
        # Hanya technician/admin yang boleh jadi pengerja. Tanpa ini,
        # id drop_point/konsumen bisa ditempel ke transaksi dan komisi
        # dihitung untuk orang yang tidak pernah mengerjakan apa pun.
        if tech.data[0]["role"] not in ("technician", "admin"):
            raise HTTPException(400, "Pengerja harus teknisi atau admin")
        data["tech_id"] = update.tech_id
    elif update.tech_id == "":
        data["tech_id"] = None

    if update.shoe_id is not None and update.shoe_id != "":
        if existing.data[0].get("status") == "Siap diambil":
            raise HTTPException(
                400,
                "Transaksi sudah Siap diambil -- harga sudah dikunci karena "
                "barang sudah diambil. Pembayaran tidak bisa diganti diam-diam.",
            )
        layanan = (
            supabase.from_("shoes")
            .select("id, harga_cuci, harga_min, status")
            .eq("id", update.shoe_id)
            .execute()
        )
        if not layanan.data:
            raise HTTPException(400, "Layanan tidak ditemukan")
        master = layanan.data[0]
        if not master["status"]:
            raise HTTPException(400, "Layanan ini sedang tidak tersedia")
        data["shoe_id"] = update.shoe_id
        # Aturan harga yang sama dengan booking: layanan ber-rentang dicatat
        # dari harga terendah sampai admin menetapkan harga final.
        data["harga"] = master.get("harga_min") or master["harga_cuci"]
    elif update.shoe_id == "":
        raise HTTPException(400, "Layanan tidak boleh kosong")

    if not data:
        raise HTTPException(400, "Tidak ada field yang diupdate")

    # Komisi selalu digandingkan dengan kondisi terkini:
    # - ganti pengerja  -> hitung ulang komisi dari harga yang ada;
    # - ganti layanan   -> harga dasarnya ikut master baru dulu, baru
    #                     komisinya dihitung dari harga itu;
    # - dua-duanya      -> layanan baru menentukan dasar harga, pengerja
    #                     baru menentukan siapa berhak atas komisinya.
    if "tech_id" in data or "shoe_id" in data:
        harga_dasar = data.get("harga") or (existing.data[0].get("harga") or 0)
        pengerja_kini = data.get("tech_id", existing.data[0].get("tech_id"))
        # Diskon direset saat ganti layanan: promo dikaitkan layanan lama,
        # dipertahankan di layanan baru akan membuat harga bersih salah.
        if "shoe_id" in data:
            data["diskon"] = None
            harga_dasar -= existing.data[0].get("diskon") or 0
        data["tech_commission"] = komisi_untuk(harga_dasar, pengerja_kini)

    data["updated_at"] = get_now_iso()
    result = supabase.from_("transactions").update(data).eq("id", transaksi_id).execute()
    if not result.data:
        raise HTTPException(404, "Transaksi tidak ditemukan")
    return _siapkan_transaksi([result.data[0]], user)[0]



# ----------------------------------------------------------------
# PHOTO UPLOAD (Supabase Storage)
# ----------------------------------------------------------------
from fastapi import UploadFile, File, Form
import uuid


@router.post("/api/transaksi/{transaksi_id}/photo", tags=["Photo"])
async def upload_photo(
    transaksi_id: str,
    jenis: str = Form(...),  # "before" | "after" | "defect"
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """Upload foto transaksi ke Supabase Storage."""
    # Validasi transaksi
    existing = supabase.from_("transactions").select("*").eq("id", transaksi_id).execute()
    if not existing.data:
        raise HTTPException(404, "Transaksi tidak ditemukan")
    
    # Validasi jenis
    if jenis not in ("before", "after", "defect"):
        raise HTTPException(400, "Jenis foto harus: before, after, atau defect")
    
    # Validasi file
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "File harus berupa gambar")
    
    # Validasi ukuran (max 5MB)
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(400, "Ukuran file maksimal 5MB")
    
    # Generate filename
    ext = file.filename.split(".")[-1].lower() if "." in file.filename else "jpg"
    filename = f"{transaksi_id}/{jenis}_{uuid.uuid4().hex[:8]}.{ext}"
    
    # Upload ke Supabase Storage
    try:
        result = supabase.storage.from_(PHOTO_BUCKET).upload(
            filename,
            content,
            {"content-type": file.content_type, "upsert": "true"}
        )
        if hasattr(result, 'error') and result.error:
            raise Exception(result.error.message)
    except Exception as e:
        raise HTTPException(500, f"Gagal upload: {str(e)}")
    
    # Get public URL
    public_url = supabase.storage.from_(PHOTO_BUCKET).get_public_url(filename)
    
    # Update transaksi dengan URL foto
    field_map = {"before": "photo_before", "after": "photo_after", "defect": "photo_defect"}
    supabase.from_("transactions").update({
        field_map[jenis]: public_url,
        "updated_at": get_now_iso()
    }).eq("id", transaksi_id).execute()
    
    return {"url": public_url, "jenis": jenis, "filename": filename}


@router.get("/api/transaksi/{transaksi_id}/photo/{jenis}", tags=["Photo"])
async def get_photo_url(
    transaksi_id: str,
    jenis: str,  # "before" | "after" | "defect"
    user: dict = Depends(get_current_user),
):
    """Get signed URL untuk akses foto private (jika bucket private)."""
    if jenis not in ("before", "after", "defect"):
        raise HTTPException(400, "Jenis foto tidak valid")
    
    existing = supabase.from_("transactions").select("*").eq("id", transaksi_id).execute()
    if not existing.data:
        raise HTTPException(404, "Transaksi tidak ditemukan")
    
    trx = existing.data[0]
    field_map = {"before": "photo_before", "after": "photo_after", "defect": "photo_defect"}
    url = trx.get(field_map[jenis])
    if not url:
        raise HTTPException(404, "Foto tidak ditemukan")
    
    # Generate signed URL (valid 1 jam)
    signed = supabase.storage.from_(PHOTO_BUCKET).create_signed_url(
        f"{transaksi_id}/{jenis}_*.*", 3600
    )
    if hasattr(signed, 'error') and signed.error:
        return {"url": url}
    return {"url": signed.signedURL}


# Endpoint lama untuk kompatibilitas
@router.post("/api/transaksi/{transaksi_id}/photo-before", tags=["Photo"])
async def upload_photo_before(
    transaksi_id: str,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    return await upload_photo(transaksi_id, "before", file, user)


@router.post("/api/transaksi/{transaksi_id}/photo-after", tags=["Photo"])
async def upload_photo_after(
    transaksi_id: str,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    return await upload_photo(transaksi_id, "after", file, user)


@router.post("/api/transaksi/{transaksi_id}/photo-defect", tags=["Photo"])
async def upload_photo_defect(
    transaksi_id: str,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    return await upload_photo(transaksi_id, "defect", file, user)


# ----------------------------------------------------------------
# CLEANUP FOTO OTOMATIS (Storage TTL)
# ----------------------------------------------------------------
# Supabase free tier TIDAK punya fitur hapus-otomatis, jadi penghapusannya
# dijalankan dari luar lewat GitHub Actions (lihat
# .github/workflows/cleanup-photos.yml) yang memanggil endpoint ini tiap hari.
#
# Retensi dihitung dari `updated_at`, bukan dari waktu upload foto. Artinya
# begitu ada aktivitas apa pun pada transaksi (status diganti, harga diedit),
# hitungan mundurnya mulai dari nol lagi. Konsekuensinya foto bisa bertahan
# LEBIH dari PHOTO_TTL_DAYS -- dan itu memang yang kita mau: foto jangan
# sampai hilang di tengah masa komplain. Tidak ada kolom baru yang dibuat
# hanya untuk fitur ini.
