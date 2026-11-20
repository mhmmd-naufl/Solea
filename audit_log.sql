-- =====================================================================
-- audit_log.sql — Jejak aksi sensitif admin/staff
-- =====================================================================
-- Jalankan sekali di Supabase SQL Editor. Aman diulang (IF NOT EXISTS).
-- Tabel ini juga sudah tercantum di schema.sql dan rls.sql.
-- =====================================================================

CREATE TABLE IF NOT EXISTS public.audit_log (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_id    UUID,
    actor_nama  TEXT,
    aksi        TEXT NOT NULL,
    target_tipe TEXT,
    target_id   TEXT,
    detail      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_created_at ON public.audit_log (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_actor      ON public.audit_log (actor_id);

COMMENT ON TABLE public.audit_log IS
    'Catatan aksi sensitif: reset password, ubah harga, hapus data, dll.';
