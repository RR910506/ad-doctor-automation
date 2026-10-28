-- Run once in a private Supabase project's SQL editor.
CREATE TABLE IF NOT EXISTS public.ad_doctor_records (
    key text PRIMARY KEY,
    kind text NOT NULL,
    data jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ad_doctor_records_kind_updated
    ON public.ad_doctor_records(kind, updated_at DESC);
ALTER TABLE public.ad_doctor_records ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.ad_doctor_records FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.ad_doctor_records TO service_role;
-- No anonymous policies. The service-role key stays in server secret settings.
