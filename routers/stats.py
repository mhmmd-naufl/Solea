"""Router stats — diekstrak dari main.py.

Semua dependensi bersama tetap tinggal di main.py; router dipasang
dari bawah main.py setelah modul itu selesai dimuat.
"""

from fastapi import APIRouter

router = APIRouter()

from main import (  # noqa: E402
    BaseModel,
    Depends,
    Field,
    List,
    Optional,
    StatsResponse,
    _parse_periode,
    _saring_selesai,
    analytics,
    date,
    require_role,
    status,
    supabase,
)

STATUS_AWAL = ("Diterima", "Diproses", "Diperiksa")


@router.get("/api/stats/admin", response_model=StatsResponse, tags=["Stats"])
def admin_stats(
    periode: str = "semua",
    bulan: Optional[int] = None,
    tahun: Optional[int] = None,
    _: dict = Depends(require_role("admin")),
):
    """
    Statistik untuk dashboard admin, dibatasi satu periode.

    Parameter `periode` menentukan resolusi grafik sekaligus labelnya:
      - `bulan&bulan=10&tahun=2026` -> satu bulan, grafik per HARI
      - `tahun&tahun=2026`         -> satu tahun, grafik per BULAN
      - `semua`                     -> seluruh riwayat, grafik per BULAN

    Default-nya `semua`, jadi endpoint ini tetap jalan dipanggil tanpa
    parameter apa pun. Kalau periodenya bukan `semua`, `tahun` (dan `bulan`
    untuk periode bulan) wajib diisi -- kalau tidak, backend menolak dengan
    400, bukan menebak periode diam-diam.

    Angka omzet, Shoes washed, dan komisi teknisi dihitung dari transaksi yang
    SUDAH SELESAI (`selesai_at` ada dan berada di dalam periode). Transaksi yang
    masih di tahap awal sengaja tidak dihitung -- nilainya belum jadi
    pendapatan, dan menghitungnya membuat dashboard terlihat lebih untung
    daripada kenyataannya. Jumlahnya tetap dilaporkan di `masih_jalan`.

    Filter periode diterapkan di PostgREST, bukan di Python. Kalau difilter
    setelah data ditarik semua, angka untuk satu bulan tetap benar tapi
    permintaan ke database tetap sebesar seluruh riwayat.
    """
    p = _parse_periode(periode, bulan, tahun)

    query = supabase.from_("transactions").select(
        "harga, tech_commission, tech_id, status"
    )
    query = _saring_selesai(query, p["mulai"], p["selesai"])
    trx = query.execute().data or []

    techs = (
        supabase.from_("profiles")
        .select("id, full_name")
        .eq("role", "technician")
        .execute().data or []
    )

    per_status: dict = {}
    by_tech: dict = {}
    pendapatan = 0
    komisi_total = 0

    for row in trx:
        status = row.get("status") or ""
        per_status[status] = per_status.get(status, 0) + 1
        pendapatan += row.get("harga") or 0
        komisi_total += row.get("tech_commission") or 0

        tech_id = row.get("tech_id")
        if tech_id:
            agg = by_tech.setdefault(tech_id, {"jumlah": 0, "komisi": 0})
            agg["jumlah"] += 1
            agg["komisi"] += row.get("tech_commission") or 0

    # Berapa yang masih dikerjakan, di luar periode. Query terpisah karena
    # `selesai_at` masih NULL untuk semua baris ini, jadi satu filter `in_`
    # sudah cukup tanpa perlu menyaring tanggal.
    belum = (
        supabase.from_("transactions")
        .select("id", count="exact")
        .in_("status", list(STATUS_AWAL))
        .execute()
    )
    # PostgREST mengembalikan `count` terpisah dari `data`. Kalau barisnya
    # kosong tapi count ada, itu sahih (0 baris) -- jadi andalkan `count`,
    # bukan `data`.
    masih_jalan = belum.count or 0

    # Pengeluaran operasional di periode yang sama. Kolomnya DATE, jadi
    # dibandingkan sebagai tanggal: `mulai` inklusif, `selesai` eksklusif --
    # sama persis dengan konvensi selesai_at supaya tidak ada hari batas
    # yang dihitung dua kali atau tidak sama sekali.
    exp_query = supabase.from_("expenses").select("jumlah")
    if p["mulai"]:
        exp_query = exp_query.gte("tanggal", p["mulai"].date().isoformat())
    if p["selesai"]:
        exp_query = exp_query.lt("tanggal", p["selesai"].date().isoformat())
    total_pengeluaran = sum(e.get("jumlah") or 0 for e in (exp_query.execute().data or []))

    # Legacy compatibility: transaksi yang statusnya sudah final tapi belum
    # punya `selesai_at` tetap harus dihitung sebagai pekerjaan selesai untuk
    # dashboard. Tanpa fallback ini, data riwayat tampak "hilang" meski sudah
    # ada di database.
    if trx:
        for row in trx:
            if row.get("status") in ("Selesai", "Siap diambil") and not row.get("selesai_at"):
                row["selesai_at"] = row.get("created_at")

    return {
        "total_transaksi": len(trx),
        "shoes_washed": sum(per_status.values()),
        "total_pendapatan": pendapatan,
        "total_teknisi": len(techs),
        "per_teknisi": [
            {
                "id": t["id"],
                "full_name": t["full_name"],
                "jumlah_pekerjaan": by_tech.get(t["id"], {}).get("jumlah", 0),
                "tech_commission": by_tech.get(t["id"], {}).get("komisi", 0),
            }
            for t in techs
        ],
        "per_status": per_status,
        "masih_jalan": masih_jalan,
        "total_komisi": komisi_total,
        "sisa_untuk_outlet": pendapatan - komisi_total,
        "total_pengeluaran": total_pengeluaran,
        "sisa_bersih": pendapatan - komisi_total - total_pengeluaran,
        "periode": {
            "periode": p["periode"],
            "label": p["label"],
            "granularitas": p["granularitas"],
            "mulai": p["mulai"].isoformat() if p["mulai"] else None,
            "selesai": p["selesai"].isoformat() if p["selesai"] else None,
        },
    }


class AnalyticsResponse(BaseModel):
    """
    Bentuk respons analytics.

    `ringkasan` dan `saran` adalah satu-satunya bagian yang boleh berasal dari
    AI. `fakta` SELALU dihitung lokal di analytics.gather_facts, apa pun yang
    terjadi pada pemanggilan OpenRouter -- grafik dan angka dashboard digambar
    dari `fakta`, jadi tidak pernah berubah-ubah karena alasan bahasa.
    """
    ringkasan: str
    saran: List[str] = Field(default_factory=list)
    sumber: str = Field(..., description="'ai', 'fallback', atau 'error'")
    model: Optional[str] = None
    catatan: Optional[str] = None
    fakta: dict


@router.get("/api/dashboard", tags=["Dashboard"])
def dashboard_summary(
    periode: str = "semua",
    bulan: Optional[int] = None,
    tahun: Optional[int] = None,
    _: dict = Depends(require_role("admin")),
):
    """Satu endpoint gabungan untuk ringkasan, tren, dan daftar yang perlu perhatian."""
    p = _parse_periode(periode, bulan, tahun)
    try:
        facts = analytics.gather_facts(
            supabase,
            mulai=p["mulai"],
            selesai=p["selesai"],
            granularitas=p["granularitas"],
            label_periode=p["label"],
        )
    except Exception as exc:  # noqa: BLE001
        # Supabase terkadang memutus koneksi sementara; jangan membuat ASGI
        # crash saat dashboard masih butuh menampilkan angka kosong yang jelas.
        return {
            "ringkasan": {
                "total_transaksi": 0,
                "shoes_washed": 0,
                "total_pendapatan": 0,
                "total_teknisi": 0,
                "per_teknisi": [],
                "per_status": {},
                "masih_jalan": 0,
                "total_komisi": 0,
                "sisa_untuk_outlet": 0,
                "periode": {
                    "periode": p["periode"],
                    "label": p["label"],
                    "granularitas": p["granularitas"],
                    "mulai": p["mulai"].isoformat() if p["mulai"] else None,
                    "selesai": p["selesai"].isoformat() if p["selesai"] else None,
                },
            },
            "per_teknisi": [],
            "per_status": {},
            "tren": [],
            "stok_menipis": [],
            "tertahan": [],
            "catatan": f"Gagal menghitung dashboard: {type(exc).__name__}.",
        }

    ringkasan = {
        "total_transaksi": facts.get("transaksi_periode", 0),
        "shoes_washed": facts.get("transaksi_periode", 0),
        "total_pendapatan": facts.get("omzet_periode", 0),
        "total_teknisi": facts.get("jumlah_teknisi", 0),
        "per_teknisi": [
            {
                "id": t.get("id"),
                "full_name": t.get("full_name"),
                "jumlah_pekerjaan": 0,
                "tech_commission": 0,
            }
            for t in supabase.from_("profiles").select("id, full_name").eq("role", "technician").execute().data or []
        ],
        "per_status": facts.get("per_status", {}),
        "masih_jalan": facts.get("transaksi_masih_jalan", 0),
        "total_komisi": facts.get("komisi_periode", 0),
        "sisa_untuk_outlet": facts.get("laba_outlet_periode", 0),
        "periode": {
            "periode": p["periode"],
            "label": p["label"],
            "granularitas": p["granularitas"],
            "mulai": p["mulai"].isoformat() if p["mulai"] else None,
            "selesai": p["selesai"].isoformat() if p["selesai"] else None,
        },
    }
    return {
        "ringkasan": ringkasan,
        "per_teknisi": ringkasan["per_teknisi"],
        "per_status": facts.get("per_status", {}),
        "tren": facts.get("grafik", []),
        "stok_menipis": facts.get("stok_kritis", []),
        "tertahan": facts.get("pekerjaan_tertahan", []),
    }


@router.get("/api/analytics/summary", response_model=AnalyticsResponse, tags=["Analytics"])
def analytics_summary(
    periode: str = "semua",
    bulan: Optional[int] = None,
    tahun: Optional[int] = None,
    pakai_ai: bool = True,
    _: dict = Depends(require_role("admin")),
):
    """
    Ringkasan + saran (AI atau fallback), disertai fakta untuk grafik.

    Admin-only dengan sengaja: fakta di sini termasuk beban kerja per teknisi,
    jadi tidak boleh bocor ke teknisi, drop point, atau konsumen.

    Parameter periode sama seperti /api/stats/admin (lihat _parse_periode).
    Granularitas grafik ikut mengikuti: satu bulan -> per hari, satu tahun ->
    per bulan.

    Kalau OpenRouter sedang tidak tersedia, endpoint ini tetap mengembalikan
    ringkasan dan saran yang dihitung dari data langsung (sumber='fallback').
    Frontend menampilkan catatan penyebabnya, jadi tidak pernah menampilkan
    string kosong.

    `pakai_ai=0` melewati pemanggilan AI sepenuhnya: respons cepat (hanya
    query database) dan tetap membawa `fakta` untuk grafik. Frontend memakai
    ini lebih dulu supaya grafik tidak ikut menunggu model bahasa.
    """
    p = _parse_periode(periode, bulan, tahun)
    try:
        return analytics.build_summary(
            supabase,
            mulai=p["mulai"],
            selesai=p["selesai"],
            granularitas=p["granularitas"],
            label_periode=p["label"],
            pakai_ai=pakai_ai,
        )
    except Exception as exc:  # noqa: BLE001
        # Analytics tidak boleh menjatuhkan dashboard. Kembalikan facts kosong
        # beserta pesan, supaya UI masih bisa menampilkan sesuatu yang jelas.
        return {
            "ringkasan": (
                "Ringkasan otomatis belum bisa dihitung saat ini. "
                "Data dashboard di bawah tetap bisa dibaca normal."
            ),
            "saran": [],
            "sumber": "error",
            "model": None,
            "catatan": f"Gagal menghitung analytics: {type(exc).__name__}.",
            "fakta": {},
        }


# ==========================================
# CRUD SEPATU (SHOES) - Master Data & Price List
# ==========================================
