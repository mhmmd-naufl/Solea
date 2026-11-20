"""Router auth — diekstrak dari main.py.

Semua dependensi bersama tetap tinggal di main.py; router dipasang
dari bawah main.py setelah modul itu selesai dimuat.
"""

from fastapi import APIRouter

router = APIRouter()

from main import (  # noqa: E402
    BaseModel,
    Depends,
    Field,
    HTTPException,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    limiter,
    Request,
    UserPublic,
    _create_user_with_password,
    create_access_token,
    get_current_user,
    hash_password,
    normalize_phone,
    require_role,
    status,
    verify_password,
    supabase,
    catat_audit,
)

@router.post("/api/auth/register", response_model=LoginResponse, status_code=status.HTTP_201_CREATED, tags=["Auth"])
@limiter.limit("3/hour")
def register(request: Request, payload: RegisterRequest):
    """Registrasi mandiri — selalu jadi role 'customer'."""
    return _create_user_with_password(
        full_name=payload.full_name.strip(),
        phone=payload.phone,
        password=payload.password,
        role="customer",
    )

@router.post("/api/auth/login", response_model=LoginResponse, tags=["Auth"])
@limiter.limit("5/minute")
def login(request: Request, payload: LoginRequest):
    phone = normalize_phone(payload.phone)
    result = supabase.from_("profiles").select("*").eq("phone", phone).execute()
    if not result.data:
        # Cari juga dengan format 0...
        result = supabase.from_("profiles").select("*").eq("phone", payload.phone.strip()).execute()
    if not result.data:
        raise HTTPException(401, "Nomor WhatsApp tidak terdaftar")

    user = result.data[0]
    if not verify_password(payload.password, user.get("password_hash")):
        raise HTTPException(401, "Password salah")

    token = create_access_token(user["id"], user["role"])
    return {
        "token": token,
        "user": {
            "id": user["id"],
            "full_name": user["full_name"],
            "phone": user["phone"],
            "role": user["role"],
            "is_verified": user.get("is_verified", False),
        },
    }

@router.get("/api/auth/me", response_model=UserPublic, tags=["Auth"])
def me(user: dict = Depends(get_current_user)):
    return {
        "id": user["id"],
        "full_name": user["full_name"],
        "phone": user["phone"],
        "role": user["role"],
        "is_verified": user.get("is_verified", False),
    }


class PasswordChange(BaseModel):
    password_lama: str = Field(..., min_length=1)
    password_baru: str = Field(..., min_length=6)


@router.post("/api/auth/change-password", tags=["Auth"])
@limiter.limit("5/minute")
def change_password(request: Request, payload: PasswordChange, user: dict = Depends(get_current_user)):
    """Ganti sandi sendiri. Wajib menyertakan sandi lama."""
    # Sengaja 400, bukan 401: 401 membuat api.js men-logout user otomatis.
    if not verify_password(payload.password_lama, user.get("password_hash")):
        raise HTTPException(400, "Sandi saat ini salah")
    if payload.password_baru == payload.password_lama:
        raise HTTPException(400, "Sandi baru harus berbeda dari sandi saat ini")
    supabase.from_("profiles").update(
        {"password_hash": hash_password(payload.password_baru)}
    ).eq("id", user["id"]).execute()
    catat_audit("ganti_password", "profiles", user["id"], {}, user)
    return {"detail": "Sandi berhasil diganti"}

class PasswordReset(BaseModel):
    """Password baru untuk pengguna lain. Panjang minimum sama dengan register."""
    password: str = Field(..., min_length=6)


@router.post("/api/users/{user_id}/reset-password", response_model=UserPublic, tags=["Users"])
def admin_reset_password(
    user_id: str,
    payload: PasswordReset,
    _: dict = Depends(require_role("admin")),
):
    """
    Admin mengganti password pengguna lain.

    Ini jalur pemulihan akses yang paling sederhana dan gratis: konsumen yang
    lupa password cukup menelepon admin, tanpa alur email atau reset-link yang
    butuh layanan tambahan. Password lama tidak dibaca, tidak divalidasi, dan
    tidak pernah dikirim balik -- hanya hash barunya yang ditulis.

    Beda dengan /api/auth/set-password (self-service), endpoint ini sengaja
    admin-only: tanpa itu siapa pun yang tahu UUID orang lain bisa mengambil
    alih akunnya.
    """
    result = (
        supabase.from_("profiles")
        .update({"password_hash": hash_password(payload.password)})
        .eq("id", user_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(404, "Pengguna tidak ditemukan")

    u = result.data[0]
    catat_audit("reset_password", "profiles", user_id, {}, _)
    return {
        "id": u["id"],
        "full_name": u["full_name"],
        "phone": u["phone"],
        "role": u["role"],
        "is_verified": u.get("is_verified", False),
    }
