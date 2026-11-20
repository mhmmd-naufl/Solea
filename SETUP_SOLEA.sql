-- =============================================================
-- SETUP_SOLEA.SQL — Jalankan SEBUAH KALI untuk database baru
-- Copy paste seluruh isi ke Supabase SQL Editor lalu Run.
-- Aman diulang (semua blok pakai IF NOT EXISTS / IF EXISTS).
-- =============================================================

-- =============================================================
-- [1] SKEMA BASE — semua tabel utama + trigger updated_at
-- (dari schema.sql)
-- =============================================================

CREATE TABLE IF NOT EXISTS profiles (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    full_name    TEXT        NOT NULL,
    phone        TEXT        NOT NULL UNIQUE,
    password_hash TEXT,
    role         TEXT        NOT NULL DEFAULT 'customer'
                 CHECK (role IN ('admin', 'customer', 'technician', 'drop_point')),
    is_verified  BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_profiles_role  ON profiles (role);
CREATE INDEX IF NOT EXISTS idx_profiles_phone ON profiles (phone);

CREATE TABLE IF NOT EXISTS shoes (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merk                  TEXT        NOT NULL,
    model                 TEXT,
    harga_cuci            INTEGER     NOT NULL CHECK (harga_cuci >= 0),
    harga_min             INTEGER              CHECK (harga_min  IS NULL OR harga_min  >= 0),
    harga_max             INTEGER              CHECK (harga_max  IS NULL OR harga_max  >= 0),
    kelompok              TEXT        CHECK (
                                   kelompok IS NULL OR
                                   kelompok ~ '^[A-Za-z][A-Za-z0-9 &/,.-]{0,60}$'
                               ),
    jenis_treatment       TEXT        CHECK (
                                   jenis_treatment IS NULL OR
                                   jenis_treatment ~ '^[A-Za-z][A-Za-z0-9 &/.,()-]{0,40}$'
                               ),
    keterangan_treatment  TEXT,
    estimasi_hari         INTEGER,
    status                BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT shoes_harga_rentang_check CHECK (
        harga_min IS NULL OR harga_max IS NULL OR harga_max >= harga_min
    ),
    CONSTRAINT shoes_estimasi_hari_check CHECK (
        estimasi_hari IS NULL OR (estimasi_hari >= 1 AND estimasi_hari <= 60)
    )
);

CREATE INDEX IF NOT EXISTS idx_shoes_status   ON shoes (status);
CREATE INDEX IF NOT EXISTS idx_shoes_merk     ON shoes (merk);
CREATE INDEX IF NOT EXISTS idx_shoes_kelompok ON shoes (kelompok);

CREATE TABLE IF NOT EXISTS drop_points (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nama       TEXT        NOT NULL,
    alamat     TEXT,
    wa_contact TEXT,
    aktif      BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_drop_points_aktif ON drop_points (aktif);

CREATE TABLE IF NOT EXISTS transactions (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kode              TEXT        UNIQUE,
    user_id           UUID        NOT NULL REFERENCES profiles (id)    ON DELETE RESTRICT,
    shoe_id           UUID        NOT NULL REFERENCES shoes (id)        ON DELETE RESTRICT,
    tech_id           UUID                  REFERENCES profiles (id)    ON DELETE SET NULL,
    drop_point_id     UUID                  REFERENCES drop_points (id) ON DELETE SET NULL,
    harga            INTEGER     NOT NULL CHECK (harga >= 0),
    tech_commission  INTEGER     NOT NULL DEFAULT 0 CHECK (tech_commission >= 0),
    status           TEXT        NOT NULL DEFAULT 'Diterima'
                     CHECK (status IN ('Diterima', 'Diproses', 'Diperiksa', 'Selesai', 'Siap diambil')),
    catatan_konsumen TEXT,
    defect_notes     TEXT,
    jenis_sepatu     TEXT,
    nama_sepatu      TEXT,
    diskon           INTEGER CHECK ((diskon IS NULL) OR (diskon >= 0 AND diskon <= harga)),
    payment_method      TEXT,
    payment_reference   TEXT,
    payment_status      TEXT NOT NULL DEFAULT 'unpaid'
                         CHECK (payment_status IN ('unpaid', 'pending', 'paid', 'failed')),
    payment_checkout_url TEXT,
    photo_before     TEXT,
    photo_after      TEXT,
    photo_defect     TEXT,
    grup_id          UUID,
    selesai_at       TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_trx_status       ON transactions (status);
CREATE INDEX IF NOT EXISTS idx_trx_user         ON transactions (user_id);
CREATE INDEX IF NOT EXISTS idx_trx_tech         ON transactions (tech_id);
CREATE INDEX IF NOT EXISTS idx_trx_drop_point   ON transactions (drop_point_id);
CREATE INDEX IF NOT EXISTS idx_trx_created_at   ON transactions (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trx_selesai_at   ON transactions (selesai_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_trx_kode   ON transactions (kode) WHERE kode IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_trx_grup         ON transactions (grup_id) WHERE grup_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS stock (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nama_item     TEXT        NOT NULL,
    tipe          TEXT        NOT NULL CHECK (tipe IN ('alat', 'bahan')),
    jumlah        INTEGER     NOT NULL DEFAULT 0 CHECK (jumlah >= 0),
    satuan        TEXT        NOT NULL CHECK (satuan IN ('pcs', 'liter', 'kg', 'pack')),
    batas_minimum INTEGER     NOT NULL DEFAULT 5,
    supplier      TEXT,
    harga_beli    NUMERIC(12,2) NOT NULL DEFAULT 0,
    tanggal_masuk DATE,
    last_updated  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_stock_tipe  ON stock (tipe);
CREATE INDEX IF NOT EXISTS idx_stock_nama  ON stock (nama_item);

CREATE TABLE IF NOT EXISTS expenses (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kategori    TEXT NOT NULL,
    jumlah      INTEGER NOT NULL CHECK (jumlah > 0),
    tanggal     DATE NOT NULL DEFAULT CURRENT_DATE,
    keterangan  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ
);
ALTER TABLE expenses DROP CONSTRAINT IF EXISTS expenses_kategori_check;
ALTER TABLE expenses
    ADD CONSTRAINT expenses_kategori_check CHECK (
        kategori ~ '^[a-z][a-z0-9 &/.,()-]{1,40}$'
    );

-- Trigger auto-update updated_at
CREATE OR REPLACE FUNCTION touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_transactions_updated_at ON transactions;
CREATE TRIGGER trg_transactions_updated_at
    BEFORE UPDATE ON transactions
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

DROP TRIGGER IF EXISTS trg_expenses_updated_at ON expenses;
CREATE TRIGGER trg_expenses_updated_at
    BEFORE UPDATE ON expenses
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- =============================================================
-- [2] AUDIT LOG — jejak aksi sensitif admin/staff
-- =============================================================
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
COMMENT ON TABLE public.audit_log IS 'Catatan aksi sensitif: reset password, ubah harga, hapus data, dll.';

-- =============================================================
-- [3] VALIDASI FORMAT — constraint tambahan (aman diulang)
-- =============================================================

-- Format payment_method (text ringkas: tunai, transfer, qris, ...)
ALTER TABLE transactions DROP CONSTRAINT IF EXISTS transactions_payment_method_check;
ALTER TABLE transactions
    ADD CONSTRAINT transactions_payment_method_check CHECK (
        payment_method IS NULL OR payment_method ~ '^[a-z_]{2,20}$'
    );

-- Harga max tidak boleh lebih kecil dari harga min
ALTER TABLE shoes DROP CONSTRAINT IF EXISTS shoes_harga_rentang_check;
ALTER TABLE shoes
    ADD CONSTRAINT shoes_harga_rentang_check CHECK (
        harga_min IS NULL OR harga_max IS NULL OR harga_max >= harga_min
    );

-- Estimasi hari
ALTER TABLE shoes DROP CONSTRAINT IF EXISTS shoes_estimasi_hari_check;
ALTER TABLE shoes
    ADD CONSTRAINT shoes_estimasi_hari_check CHECK (
        estimasi_hari IS NULL OR (estimasi_hari >= 1 AND estimasi_hari <= 60)
    );

-- Nama layanan terbuka (non-enums)
ALTER TABLE shoes DROP CONSTRAINT IF EXISTS shoes_jenis_treatment_check;
ALTER TABLE shoes
    ADD CONSTRAINT shoes_jenis_treatment_check CHECK (
        jenis_treatment IS NULL OR
        jenis_treatment ~ '^[A-Za-z][A-Za-z0-9 &/.,()-]{0,40}$'
    );

-- Kelompok layanan
ALTER TABLE shoes DROP CONSTRAINT IF EXISTS shoes_kelompok_check;
ALTER TABLE shoes
    ADD CONSTRAINT shoes_kelompok_check CHECK (
        kelompok IS NULL OR kelompok ~ '^[A-Za-z][A-Za-z0-9 &/,.-]{0,60}$'
    );

-- =============================================================
-- [4] RLS — Kunci akses. Tanpa policy apa pun, default DENY.
-- Backend lewat service-role key, frontend tidak pernah akses DB.
-- =============================================================

DO $$
DECLARE pol record;
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

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'profiles', 'shoes', 'drop_points',
        'transactions', 'stock', 'expenses', 'audit_log'
    ] LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    END LOOP;
END $$;

-- =============================================================
-- GRANT PRIVILEGE ke service_role & anon
-- ------------------------------------------------------------
-- Supabase membuat tabel lewat PostgREST (service_role). Tanpa GRANT
-- eksplisit, service_role hanya punya default GRANT ke anon.
-- Backend memakai service_role key -> WAJIB punya SELECT/INSERT/UPDATE/DELETE
-- di semua tabel aplikasi.
-- =============================================================
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'profiles', 'shoes', 'drop_points',
        'transactions', 'stock', 'expenses', 'audit_log'
    ] LOOP
        EXECUTE format('GRANT ALL PRIVILEGES ON public.%I TO service_role', t);
        EXECUTE format('GRANT ALL PRIVILEGES ON public.%I TO anon', t);
        EXECUTE format('GRANT ALL PRIVILEGES ON public.%I TO authenticated', t);
    END LOOP;
END $$;

-- Verifikasi RLS aktif di semua tabel aplikasi
SELECT tablename, rowsecurity AS rls_aktif
FROM pg_tables
WHERE schemaname = 'public'
  AND tablename IN (
      'profiles', 'shoes', 'drop_points',
      'transactions', 'stock', 'expenses', 'audit_log'
  )
ORDER BY tablename;
