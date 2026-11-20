"""Router drop_points — diekstrak dari main.py.

Semua dependensi bersama tetap tinggal di main.py; router dipasang
dari bawah main.py setelah modul itu selesai dimuat.
"""

from fastapi import APIRouter

router = APIRouter()

from main import (  # noqa: E402
    Depends,
    DropPointCreate,
    DropPointResponse,
    DropPointUpdate,
    HTTPException,
    List,
    Optional,
    Response,
    _cari_teks,
    _halaman_berpaginan,
    _pola_ilike,
    get_now_iso,
    require_role,
    status,
    supabase,
)

@router.post("/api/drop-points", response_model=DropPointResponse, status_code=status.HTTP_201_CREATED, tags=["Drop Points"])
def create_drop_point(dp: DropPointCreate, _: dict = Depends(require_role("admin"))):
    data = dp.model_dump()
    data["created_at"] = get_now_iso()
    result = supabase.from_("drop_points").insert(data).execute()
    if not result.data:
        raise HTTPException(500, "Gagal membuat drop point")
    return result.data[0]

@router.get("/api/drop-points", response_model=List[DropPointResponse], tags=["Drop Points"])
def list_drop_points(
    response: Response,
    aktif_only: bool = True,
    q: Optional[str] = None,
    page: Optional[int] = None,
    per_page: Optional[int] = None,
):
    """
    Daftar mitra / outlet.

    Tetap publik: alamat outlet memang sengaja ditampilkan di halaman depan
    supaya pelanggan tahu di mana titip. Halaman booking juga memanggil endpoint
    ini tanpa login, jadi membikinnya butuh token akan membuat halaman depan
    diam-diam jatuh ke data cadangan.
    """
    cari = _cari_teks(q)

    def bangun() -> object:
        query = supabase.from_("drop_points").select("*", count="exact")
        if aktif_only:
            query = query.eq("aktif", True)
        if cari:
            query = query.or_(
                ",".join([_pola_ilike("nama", cari), _pola_ilike("alamat", cari)])
            )
        return query.order("nama")

    if page is None:
        return bangun().execute().data or []
    return _halaman_berpaginan(bangun, page, per_page, response)

@router.get("/api/drop-points/{dp_id}", response_model=DropPointResponse, tags=["Drop Points"])
def get_drop_point(dp_id: str):
    result = supabase.from_("drop_points").select("*").eq("id", dp_id).execute()
    if not result.data:
        raise HTTPException(404, "Drop point tidak ditemukan")
    return result.data[0]

@router.put("/api/drop-points/{dp_id}", response_model=DropPointResponse, tags=["Drop Points"])
def update_drop_point(dp_id: str, dp: DropPointUpdate, _: dict = Depends(require_role("admin"))):
    data = dp.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(400, "Tidak ada data yang diupdate")
    result = supabase.from_("drop_points").update(data).eq("id", dp_id).execute()
    if not result.data:
        raise HTTPException(404, "Drop point tidak ditemukan")
    return result.data[0]


# ==========================================
# CRUD STOCK (ALAT & BAHAN)
# ==========================================
