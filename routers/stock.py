"""Router stock — diekstrak dari main.py.

Semua dependensi bersama tetap tinggal di main.py; router dipasang
dari bawah main.py setelah modul itu selesai dimuat.
"""

from fastapi import APIRouter

router = APIRouter()

from main import (  # noqa: E402
    Depends,
    FastAPI,
    HTTPException,
    List,
    Optional,
    Response,
    StockCreate,
    StockResponse,
    StockUpdate,
    _cari_teks,
    _halaman_berpaginan,
    get_now_iso,
    require_role,
    status,
    supabase,
)

@router.post("/api/stock", response_model=StockResponse, status_code=status.HTTP_201_CREATED, tags=["Stock"])
def create_stock(item: StockCreate, _: dict = Depends(require_role("admin"))):
    data = item.model_dump()
    data["last_updated"] = get_now_iso()
    result = supabase.from_("stock").insert(data).execute()
    if not result.data:
        raise HTTPException(500, "Gagal menambah stock")
    return result.data[0]

@router.get("/api/stock", response_model=List[StockResponse], tags=["Stock"])
def list_stock(
    response: Response,
    _: dict = Depends(require_role("admin", "technician")),
    tipe: Optional[str] = None,
    low_stock: bool = False,
    q: Optional[str] = None,
    urut: str = "nama",
    page: Optional[int] = None,
    per_page: Optional[int] = None,
):
    """
    Daftar stok bahan.

    low_stock difilter lewat daftar id, bukan di Python. Kalau filtering
    post-hoc, total dan paginasi jadi tidak sinkron -- backend melaporkan
    "hanya 2 item" padahal di database ada 8, dan halaman 2 jadi kosong
    padahal masih ada isi.
    """
    cari = _cari_teks(q)

    id_kritis: Optional[list[str]] = None
    if low_stock:
        semua = (
            supabase.from_("stock").select("id,jumlah,batas_minimum").execute().data or []
        )
        id_kritis = [
            s["id"] for s in semua if (s.get("jumlah") or 0) <= (s.get("batas_minimum") or 0)
        ]

    def bangun() -> object:
        query = supabase.from_("stock").select("*", count="exact")
        if tipe:
            query = query.eq("tipe", tipe)
        if id_kritis is not None:
            if not id_kritis:
                # Tidak ada yang kritis. Tetap harus mengembalikan query yang
                # sah supaya count=0, bukan daftar kosong tanpa total.
                query = query.in_("id", ["00000000-0000-0000-0000-000000000000"])
            else:
                query = query.in_("id", id_kritis)
        if cari:
            query = query.ilike("nama_item", f"%{cari}%")

        if urut == "jumlah_terbanyak":
            return query.order("jumlah", desc=True)
        if urut == "jumlah_tersedikit":
            return query.order("jumlah")
        return query.order("nama_item")

    if page is None:
        return bangun().execute().data or []
    return _halaman_berpaginan(bangun, page, per_page, response)

@router.get("/api/stock/{stock_id}", response_model=StockResponse, tags=["Stock"])
def get_stock(stock_id: str, _: dict = Depends(require_role("admin", "technician"))):
    result = supabase.from_("stock").select("*").eq("id", stock_id).execute()
    if not result.data:
        raise HTTPException(404, "Item stock tidak ditemukan")
    return result.data[0]

@router.put("/api/stock/{stock_id}", response_model=StockResponse, tags=["Stock"])
def update_stock(stock_id: str, item: StockUpdate, _: dict = Depends(require_role("admin"))):
    data = item.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(400, "Tidak ada data yang diupdate")
    data["last_updated"] = get_now_iso()
    result = supabase.from_("stock").update(data).eq("id", stock_id).execute()
    if not result.data:
        raise HTTPException(404, "Item stock tidak ditemukan")
    return result.data[0]

@router.post("/api/stock/{stock_id}/kurangi", response_model=StockResponse, tags=["Stock"])
def kurangi_stock(stock_id: str, jumlah: int, _: dict = Depends(require_role("admin", "technician"))):
    """Kurangi stok (dipakai teknisi saat memakai bahan)."""
    if jumlah <= 0:
        raise HTTPException(400, "Jumlah harus > 0")
    
    # Ambil stok saat ini
    current = supabase.from_("stock").select("jumlah").eq("id", stock_id).execute()
    if not current.data:
        raise HTTPException(404, "Item stock tidak ditemukan")
    
    stok_sekarang = current.data[0]["jumlah"]
    if stok_sekarang < jumlah:
        raise HTTPException(400, f"Stok tidak cukup. Tersedia: {stok_sekarang}")
    
    baru = stok_sekarang - jumlah
    result = supabase.from_("stock").update({
        "jumlah": baru,
        "last_updated": get_now_iso()
    }).eq("id", stock_id).execute()

    return result.data[0]


# ==========================================
# EXPENSES (PENGELUARAN OPERASIONAL)
# ==========================================
# Data finansial: semua endpoint di sini admin-only, konsisten dengan aturan
# privasi harga/komisi -- teknisi dan drop point tidak perlu tahu pengeluaran.
#
# PENTING: /rekap-mingguan didefinisikan SEBELUM /{expense_id}. Kalau urutannya
# terbalik, FastAPI menganggap "rekap-mingguan" sebuah id dan endpoint rekap
# tidak pernah tercapai.
