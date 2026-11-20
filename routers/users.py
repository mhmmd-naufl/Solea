"""Router users — diekstrak dari main.py.

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
    ProfileCreate,
    ProfileResponse,
    ProfileUpdate,
    Response,
    _cari_teks,
    _create_user_with_password,
    _halaman_berpaginan,
    _pola_ilike,
    get_current_user,
    get_now_iso,
    normalize_phone,
    require_role,
    secrets,
    status,
    supabase,
    catat_audit,
)

@router.post("/api/users", response_model=ProfileResponse, status_code=status.HTTP_201_CREATED, tags=["Users"])
def create_user(user: ProfileCreate, _: dict = Depends(require_role("admin"))):
    data = user.model_dump()
    data["phone"] = normalize_phone(data["phone"])
    data["created_at"] = get_now_iso()
    data["is_verified"] = user.role in ("admin", "technician", "drop_point")
    try:
        result = supabase.from_("profiles").insert(data).execute()
        if not result.data:
            raise HTTPException(500, "Gagal membuat user")
        catat_audit("buat_user", "profiles", result.data[0]["id"], {"role": data.get("role")}, _)
        return result.data[0]
    except Exception as e:
        if "duplicate key" in str(e).lower():
            raise HTTPException(400, "Nomor telepon sudah terdaftar")
        raise HTTPException(500, f"Database error: {str(e)}")

def _password_acak(panjang: int = 8) -> str:
    # Tanpa karakter yang mirip (I, l, O, 0, 1) supaya mudah dibaca di WhatsApp.
    alfabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alfabet) for _ in range(panjang))

# Versi dengan password — dipakai admin saat membuat teknisi/drop point
@router.post("/api/users/with-password", status_code=status.HTTP_201_CREATED, tags=["Users"])
def create_user_with_password(payload: ProfileCreate, _: dict = Depends(require_role("admin"))):
    password = _password_acak()
    dibuat = _create_user_with_password(
        full_name=payload.full_name,
        phone=payload.phone,
        password=password,
        role=payload.role,
    )
    catat_audit(
        "buat_user_sandi",
        "profiles",
        dibuat.get("user", {}).get("id"),
        {"role": payload.role},
        _,
    )
    return {
        "detail": "User dibuat dengan sandi sementara acak.",
        "phone": normalize_phone(payload.phone),
        "password": password,
    }

@router.get("/api/users", response_model=List[ProfileResponse], tags=["Users"])
def list_users(
    response: Response,
    _: dict = Depends(require_role("admin")),
    role: Optional[str] = None,
    q: Optional[str] = None,
    urut: str = "terbaru",
    page: Optional[int] = None,
    per_page: Optional[int] = None,
):
    """Daftar user. `q` mencari di nama dan nomor WhatsApp."""
    cari = _cari_teks(q)

    def bangun() -> object:
        query = supabase.from_("profiles").select("*", count="exact")
        if role:
            query = query.eq("role", role)
        if cari:
            query = query.or_(
                ",".join([_pola_ilike("full_name", cari), _pola_ilike("phone", cari)])
            )
        if urut == "nama":
            return query.order("full_name")
        if urut == "nama_z":
            return query.order("full_name", desc=True)
        return query.order("created_at", desc=True)

    if page is None:
        return bangun().execute().data or []
    return _halaman_berpaginan(bangun, page, per_page, response)

@router.get("/api/users/{user_id}", response_model=ProfileResponse, tags=["Users"])
def get_user(user_id: str, _: dict = Depends(require_role("admin"))):
    result = supabase.from_("profiles").select("*").eq("id", user_id).execute()
    if not result.data:
        raise HTTPException(404, "User tidak ditemukan")
    return result.data[0]

@router.put("/api/users/{user_id}", response_model=ProfileResponse, tags=["Users"])
def update_user(user_id: str, user: ProfileUpdate, _: dict = Depends(require_role("admin"))):
    data = user.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(400, "Tidak ada data yang diupdate")

    # Cegah admin terakhir kehilangan akses: jangan biarkan admin mengubah
    # role dirinya sendiri jadi bukan admin.
    if data.get("role") and data["role"] != "admin":
        target = supabase.from_("profiles").select("id, role").eq("id", user_id).execute()
        if target.data and target.data[0]["role"] == "admin":
            admins = supabase.from_("profiles").select("id").eq("role", "admin").execute().data or []
            if len(admins) <= 1:
                raise HTTPException(400, "Tidak bisa menurunkan role admin terakhir")

    result = supabase.from_("profiles").update(data).eq("id", user_id).execute()
    if not result.data:
        raise HTTPException(404, "User tidak ditemukan")
    catat_audit("ubah_user", "profiles", user_id, {"field": sorted(data)}, _)
    return result.data[0]


@router.delete("/api/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["Users"])
def delete_user(user_id: str, user: dict = Depends(get_current_user)):
    """
    Admin-only. Hapus akun (profil) secara permanen.

    Aturannya sengaja ketat karena riwayat harus tetap bisa dipertanggung-
    jawabkan:

    - Tidak bisa menghapus akun sendiri: dengan ini satu-satunya jalan masuk
      sebagai admin bisa dua kali klik hilang.
    - Tidak bisa menghapus akun yang masih tertulis pada sebuah transaksi,
      baik sebagai pembeli maupun sebagai pengerja. Kolom user_id memang
      ON DELETE RESTRICT di DB, tapi tech_id akan di-set NULL diam-diam --
      dan itu menghapus jejak siapa yang mengerjakan serta dasar perhitungan
      komisi. Keduanya ditolak di sini dengan pesan yang bisa dibaca, bukan
      diserahkan ke error FK yang tidak ramah.
    """
    if user["role"] != "admin":
        raise HTTPException(403, "Hanya admin yang boleh menghapus akun")
    if user_id == user["id"]:
        raise HTTPException(400, "Tidak bisa menghapus akun sendiri")

    target = (
        supabase.from_("profiles")
        .select("full_name")
        .eq("id", user_id)
        .execute()
    )
    if not target.data:
        raise HTTPException(404, "User tidak ditemukan")
    nama = (target.data[0].get("full_name") or "User").strip()

    alasan = []
    sebagai_pembeli = (
        supabase.from_("transactions")
        .select("id", count="exact")
        .eq("user_id", user_id)
        .execute()
    )
    if (sebagai_pembeli.count or 0) > 0:
        alasan.append(f"{sebagai_pembeli.count} transaksi atas nama pembeli ini")

    sebagai_pengerja = (
        supabase.from_("transactions")
        .select("id", count="exact")
        .eq("tech_id", user_id)
        .execute()
    )
    if (sebagai_pengerja.count or 0) > 0:
        alasan.append(f"{sebagai_pengerja.count} transaksi masih tertulis dikerjakan di sini")

    if alasan:
        raise HTTPException(
            400,
            f"Akun {nama} tidak bisa dihapus: {', '.join(alasan)}. "
            "Riwayat tidak boleh kehilangan pemiliknya -- biarkan akunnya tetap ada "
            "(atau ganti nomor WhatsApp-nya), bukan dihapus.",
        )

    supabase.from_("profiles").delete().eq("id", user_id).execute()
    catat_audit("hapus_user", "profiles", user_id, {"nama": nama}, user)


# ==========================================
# CRUD DROP POINTS
# ==========================================
