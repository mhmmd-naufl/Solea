-- =====================================================================
-- payments.sql — Kolom pembayaran untuk database yang sudah ada
-- =====================================================================
-- Aman diulang. Untuk database baru, kolom ini sudah ada di schema.sql.
-- =====================================================================

ALTER TABLE public.transactions ADD COLUMN IF NOT EXISTS payment_method TEXT;
ALTER TABLE public.transactions ADD COLUMN IF NOT EXISTS payment_reference TEXT;
ALTER TABLE public.transactions ADD COLUMN IF NOT EXISTS payment_checkout_url TEXT;
ALTER TABLE public.transactions
    ADD COLUMN IF NOT EXISTS payment_status TEXT NOT NULL DEFAULT 'unpaid';

ALTER TABLE public.transactions
    DROP CONSTRAINT IF EXISTS transactions_payment_status_check;
ALTER TABLE public.transactions
    ADD CONSTRAINT transactions_payment_status_check
    CHECK (payment_status IN ('unpaid', 'pending', 'paid', 'failed'));

CREATE INDEX IF NOT EXISTS idx_trx_payment_ref
    ON public.transactions (payment_reference)
    WHERE payment_reference IS NOT NULL;
