"""Router expenses — diekstrak dari main.py.

Semua dependensi bersama tetap tinggal di main.py; router dipasang
dari bawah main.py setelah modul itu selesai dimuat.
"""

from fastapi import APIRouter

router = APIRouter()

from main import (  # noqa: E402
    Depends,
    ExpenseCreate,
    ExpenseResponse,
    ExpenseUpdate,
    HTTPException,
    List,
    Optional,
    Response,
    ZONA_WIB,
    _cari_teks,
    _halaman_berpaginan,
    _kategori_bersih,
    _tanggal_bersih,
    date,
    datetime,
    get_now_iso,
    require_role,
    status,
    timedelta,
    supabase,
    catat_audit,
)

@router.get("/api/expenses/rekap-mingguan", tags=["Expenses"])
def rekap_mingguan_expenses(_: dict = Depends(require_role("admin"))):
    """
    Total pengeluaran per minggu (Senin-Minggu, zona WIB), 8 minggu terakhir.

    Minggu dimulai Senin supaya rekap sejalan dengan pekan kerja outlet.
    Ini jawaban atas pertanyaan rutin pemilik: "minggu ini habis berapa?"
    """
    sekarang = datetime.now(ZONA_WIB).date()
    senin_ini = sekarang - timedelta(days=sekarang.weekday())
    batas = senin_ini - timedelta(weeks=7)

    baris = (
        supabase.from_("expenses")
        .select("kategori, jumlah, tanggal")
        .gte("tanggal", batas.isoformat())
        .execute().data or []
    )

    # Siapkan 8 slot minggu (termasuk yang kosong) supaya riwayat tidak
    # melompat-lompat saat ada minggu tanpa pengeluaran.
    slot: dict[str, dict] = {}
    for i in range(8):
        senin = batas + timedelta(weeks=i)
        slot[senin.isoformat()] = {"senin": senin.isoformat(), "total": 0, "per_kategori": {}}

    for r in baris:
        try:
            tgl = date.fromisoformat(str(r.get("tanggal"))[:10])
        except ValueError:
            continue
        senin = tgl - timedelta(days=tgl.weekday())
        sel = slot.get(senin.isoformat())
        if sel is None:
            continue  # di luar jendela 8 minggu (tanggal ganjil di data lama)
        jumlah = r.get("jumlah") or 0
        sel["total"] += jumlah
        kat = r.get("kategori") or "lainnya"
        sel["per_kategori"][kat] = sel["per_kategori"].get(kat, 0) + jumlah

    return {
        "minggu_ini": slot[senin_ini.isoformat()],
        "riwayat": [slot[(batas + timedelta(weeks=i)).isoformat()] for i in range(8)],
    }


@router.post("/api/expenses", response_model=ExpenseResponse, status_code=status.HTTP_201_CREATED, tags=["Expenses"])
def create_expense(item: ExpenseCreate, _: dict = Depends(require_role("admin"))):
    kategori = _kategori_bersih(item.kategori)
    if not kategori:
        raise HTTPException(400, "Kategori wajib diisi")
    data = {
        "kategori": kategori,
        "jumlah": item.jumlah,
        # Default hari ini di zona WIB -- jangan NOW() dari server yang UTC,
        # karena input lewat tengah malam WIB bisa masuk tanggal kemarin.
        "tanggal": _tanggal_bersih(item.tanggal) or datetime.now(ZONA_WIB).date().isoformat(),
        "keterangan": (item.keterangan or "").strip() or None,
        "created_at": get_now_iso(),
    }
    result = supabase.from_("expenses").insert(data).execute()
    if not result.data:
        raise HTTPException(500, "Gagal mencatat pengeluaran")
    return result.data[0]


@router.get("/api/expenses", response_model=List[ExpenseResponse], tags=["Expenses"])
def list_expenses(
    response: Response,
    _: dict = Depends(require_role("admin")),
    kategori: Optional[str] = None,
    dari: Optional[str] = None,
    sampai: Optional[str] = None,
    q: Optional[str] = None,
    urut: str = "terbaru",
    page: Optional[int] = None,
    per_page: Optional[int] = None,
):
    """
    Riwayat pengeluaran. Filter tanggal `dari`/`sampai` keduanya INKLUSIF
    (kolomnya DATE, bukan timestamp -- batas eksklusif di sini justru
    membuang pengeluaran tepat di tanggal `sampai`).
    """
    kat = _kategori_bersih(kategori)
    tgl_dari = _tanggal_bersih(dari)
    tgl_sampai = _tanggal_bersih(sampai)
    cari = _cari_teks(q)

    def bangun() -> object:
        query = supabase.from_("expenses").select("*", count="exact")
        if kat:
            query = query.eq("kategori", kat)
        if tgl_dari:
            query = query.gte("tanggal", tgl_dari)
        if tgl_sampai:
            query = query.lte("tanggal", tgl_sampai)
        if cari:
            query = query.ilike("keterangan", f"%{cari}%")

        if urut == "terlama":
            return query.order("tanggal").order("created_at")
        if urut == "nilai_tinggi":
            return query.order("jumlah", desc=True)
        if urut == "nilai_rendah":
            return query.order("jumlah")
        return query.order("tanggal", desc=True).order("created_at", desc=True)

    if page is None:
        return bangun().execute().data or []
    return _halaman_berpaginan(bangun, page, per_page, response)


@router.put("/api/expenses/{expense_id}", response_model=ExpenseResponse, tags=["Expenses"])
def update_expense(expense_id: str, item: ExpenseUpdate, _: dict = Depends(require_role("admin"))):
    data = item.model_dump(exclude_unset=True)
    if "kategori" in data:
        data["kategori"] = _kategori_bersih(data["kategori"])
        if not data["kategori"]:
            raise HTTPException(400, "Kategori wajib diisi")
    if "tanggal" in data:
        data["tanggal"] = _tanggal_bersih(data["tanggal"], wajib=True)
    if "keterangan" in data:
        data["keterangan"] = (data["keterangan"] or "").strip() or None
    if not data:
        raise HTTPException(400, "Tidak ada data yang diupdate")
    result = supabase.from_("expenses").update(data).eq("id", expense_id).execute()
    if not result.data:
        raise HTTPException(404, "Catatan pengeluaran tidak ditemukan")
    return result.data[0]


@router.delete("/api/expenses/{expense_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["Expenses"])
def delete_expense(expense_id: str, _: dict = Depends(require_role("admin"))):
    """Hapus catatan salah input. Hard delete: tidak ada tabel lain yang
    mereferensikan expenses, jadi tidak ada risiko FK seperti pada sepatu."""
    result = supabase.from_("expenses").delete().eq("id", expense_id).execute()
    if not result.data:
        raise HTTPException(404, "Catatan pengeluaran tidak ditemukan")
    catat_audit("hapus_expense", "expenses", expense_id, {}, _)
