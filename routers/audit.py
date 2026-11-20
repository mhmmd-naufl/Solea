"""Router audit — membaca jejak aksi sensitif (admin only)."""

from fastapi import APIRouter

router = APIRouter()

from main import Depends, require_role, supabase  # noqa: E402


@router.get("/api/audit-log", tags=["Audit"])
def list_audit(limit: int = 50, _: dict = Depends(require_role("admin"))):
    """Ambil catatan audit terbaru. `limit` dibatasi 1..200."""
    batas = max(1, min(limit, 200))
    hasil = (
        supabase.from_("audit_log")
        .select("*")
        .order("created_at", desc=True)
        .limit(batas)
        .execute()
    )
    return hasil.data or []
