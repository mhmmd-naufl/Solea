"""Router payments — titik integrasi gateway pembayaran (QRIS/VA/dll).

Tanpa PAYMENT_API_URL, endpoint /intent mengembalikan 503 dengan pesan jelas.
Webhook dilindungi PAYMENT_WEBHOOK_TOKEN.
"""

import hmac
import os

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from payments import buat_pembayaran, provider_aktif

router = APIRouter()

from main import get_current_user, supabase  # noqa: E402


class PaymentIntent(BaseModel):
    transaksi_id: str
    metode: str = "qris"


class PaymentWebhook(BaseModel):
    reference: str
    status: str


@router.post("/api/payments/intent", tags=["Payments"])
def buat_intent(payload: PaymentIntent, user: dict = Depends(get_current_user)):
    if not provider_aktif():
        raise HTTPException(
            503,
            "Gateway pembayaran belum dikonfigurasi. Isi PAYMENT_API_URL di .env backend.",
        )

    trx = (
        supabase.from_("transactions")
        .select("id, user_id, kode, harga")
        .eq("id", payload.transaksi_id)
        .execute()
    )
    if not trx.data:
        raise HTTPException(404, "Transaksi tidak ditemukan")
    baris = trx.data[0]
    if user["role"] == "customer" and baris["user_id"] != user["id"]:
        raise HTTPException(403, "Transaksi ini bukan milikmu")

    hasil = buat_pembayaran(baris["kode"], baris["harga"] or 0, payload.metode)
    if not hasil:
        raise HTTPException(502, "Gateway pembayaran gagal membuat tagihan")

    supabase.from_("transactions").update(
        {
            "payment_reference": hasil["reference"],
            "payment_status": "pending",
            "payment_checkout_url": hasil["checkout_url"],
        }
    ).eq("id", baris["id"]).execute()
    return hasil


@router.post("/api/payments/webhook", tags=["Payments"])
def terima_webhook(
    payload: PaymentWebhook,
    x_payment_token: str | None = Header(default=None),
):
    token = os.getenv("PAYMENT_WEBHOOK_TOKEN", "")
    if not token or not x_payment_token or not hmac.compare_digest(token, x_payment_token):
        raise HTTPException(401, "Token webhook tidak valid")
    if payload.status not in ("pending", "paid", "failed"):
        raise HTTPException(400, "Status pembayaran tidak dikenal")

    hasil = (
        supabase.from_("transactions")
        .update({"payment_status": payload.status})
        .eq("payment_reference", payload.reference)
        .execute()
    )
    if not hasil.data:
        raise HTTPException(404, "Referensi pembayaran tidak ditemukan")
    return {"status": "ok"}
