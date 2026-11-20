"""Adapter pembayaran yang tidak terikat gateway tertentu.

Isi PAYMENT_API_URL + PAYMENT_API_KEY di .env untuk mengaktifkan. Gateway
diharapkan menerima JSON:
    {"order_id": <kode tracking>, "amount": <rupiah>, "method": "qris"}
dan mengembalikan:
    {"reference": ..., "checkout_url": ..., "qr_string": ..., "status": ...}

Nama field bisa disesuaikan lewat provider masing-masing; adapter ini sengaja
minimal supaya bisa dipakai untuk QRIS, VA, atau gateway lain.
"""

import os

import httpx


def provider_aktif() -> bool:
    return bool(os.getenv("PAYMENT_API_URL", "").strip())


def buat_pembayaran(kode: str, nominal: int, metode: str = "qris") -> dict | None:
    """Buat payment intent. None berarti gateway tidak aktif atau gagal."""
    url = os.getenv("PAYMENT_API_URL", "").strip()
    key = os.getenv("PAYMENT_API_KEY", "").strip()
    if not url:
        return None

    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                url,
                json={"order_id": kode, "amount": nominal, "method": metode},
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
        return {
            "reference": data.get("reference") or data.get("id") or kode,
            "checkout_url": data.get("checkout_url") or data.get("url"),
            "qr_string": data.get("qr_string"),
            "status": data.get("status") or "pending",
        }
    except Exception as exc:
        print(f"[PAYMENT] Gagal membuat intent: {type(exc).__name__}: {exc}")
        return None
