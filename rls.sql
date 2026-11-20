-- =====================================================================
-- rls.sql — Kunci akses tabel di Supabase (JALANKAN SEBELUM DEPLOY PUBLIK)
-- =====================================================================
-- Jalankan file ini sekali di Supabase SQL Editor, atau ulang kapan saja.
--
-- Prinsipnya:
--   * Backend memakai SUPABASE_SERVICE_ROLE_KEY. Service-role MELEWATI RLS,
--     jadi seluruh endpoint tetap berfungsi seperti biasa.
--   * Frontend TIDAK mengakses tabel langsung (semua lewat /api). Karena itu
--     tidak ada policy yang dibutuhkan untuk anon/authenticated.
--   * RLS tanpa policy = DENY by default untuk anon dan authenticated.
--   * Semua policy lama dihapus supaya tidak ada jalur tak sengaja terbuka.
--
-- Catatan: foto disimpan di Supabase Storage dan diakses lewat URL public,
-- ini tidak dipengaruhi RLS tabel di bawah.
-- =====================================================================

-- 1. Hapus semua policy lama pada tabel aplikasi.
DO $$
DECLARE
    pol record;
BEGIN
    FOR pol IN
        SELECT schemaname, tablename, policyname
        FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename IN (
              'profiles', 'shoes', 'drop_points',
              'transactions', 'stock', 'expenses', 'audit_log'
          )
    LOOP
        EXECUTE format(
            'DROP POLICY %I ON %I.%I',
            pol.policyname, pol.schemaname, pol.tablename
        );
    END LOOP;
END $$;

-- 2. Aktifkan RLS di seluruh tabel (expenses memang sudah aktif sebelumnya).
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'profiles', 'shoes', 'drop_points',
        'transactions', 'stock', 'expenses', 'audit_log'
    ] LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    END LOOP;
END $$;

-- 3. Verifikasi: keenam tabel harus bernilai true.
SELECT tablename, rowsecurity AS rls_aktif
FROM pg_tables
WHERE schemaname = 'public'
  AND tablename IN (
      'profiles', 'shoes', 'drop_points',
      'transactions', 'stock', 'expenses', 'audit_log'
  )
ORDER BY tablename;
