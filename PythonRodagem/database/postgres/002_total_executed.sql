ALTER TABLE public.agent_tc_runs
ADD COLUMN IF NOT EXISTS total_executed INTEGER;

INSERT INTO public.agent_tc_schema_migrations(version, applied_at)
VALUES ('002_total_executed', now())
ON CONFLICT (version) DO NOTHING;
