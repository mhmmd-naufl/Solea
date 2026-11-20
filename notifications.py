"""Notifikasi WhatsApp best-effort.

Belum terkonfigurasi = tidak mengirim apa pun dan TIDAK menggagalkan request.
Cocok untuk Fonnte, Wablas, Twilio, atau gateway lain yang menerima JSON.

Contoh .env untuk Fonnte:
    WA_GATEWAY_URL=https://api.fonnte.com/send
    WA_GATEWAY_TOKEN=token-dari-dashboard
    WA_GATEWAY_HEADER=Authorization
    WA_GATEWAY_PREFIX=
    WA_GATEWAY_PAYLOAD={"target": __PHONE__, "message": __MESSAGE__}
"""

import json
import os

import httpx


def phone_digits(value: str | None) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def pesan_booking(kode: str, nama_usaha: str) -> str:
    return (
        f"Halo, terima kasih sudah booking di {nama_usaha}. "
        f"Nomor booking kamu: {kode}. "
        f"Pantau progresnya di /status?kode={kode}."
    )


def pesan_status(kode: str, status: str, nama_usaha: str) -> str:
    return (
        f"Halo, status barang dengan nomor {kode} sekarang: {status}. "
        f"Detailnya bisa dilihat di /status?kode={kode}. "
        f"— {nama_usaha}"
    )


def kirim_wa(phone: str | None, message: str) -> bool:
    """Kirim pesan. Kembalikan False bila tidak aktif atau gagal."""
    url = os.getenv("WA_GATEWAY_URL", "").strip()
    token = os.getenv("WA_GATEWAY_TOKEN", "").strip()
    digits = phone_digits(phone)
    if not url or not token or not digits:
        return False

    template = os.getenv(
        "WA_GATEWAY_PAYLOAD",
        '{"target": __PHONE__, "message": __MESSAGE__}',
    )
    try:
        payload = json.loads(
            template.replace("__PHONE__", json.dumps(digits))
            .replace("__MESSAGE__", json.dumps(message))
        )
        headers = {"Content-Type": "application/json"}
        headers[os.getenv("WA_GATEWAY_HEADER", "Authorization")] = (
            os.getenv("WA_GATEWAY_PREFIX", "Bearer ") + token
        ).strip()
        with httpx.Client(timeout=5.0) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
        return True
    except Exception as exc:
        # Notifikasi tidak boleh membuat booking/ganti status gagal.
        print(f"[WA] Gagal mengirim notifikasi: {type(exc).__name__}: {exc}")
        return False
