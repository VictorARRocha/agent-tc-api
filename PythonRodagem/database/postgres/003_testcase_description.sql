ALTER TABLE public.agent_tc_occurrences
ADD COLUMN IF NOT EXISTS testcase_description TEXT;

INSERT INTO public.agent_tc_schema_migrations(version, applied_at)
VALUES ('003_testcase_description', now())
ON CONFLICT (version) DO NOTHING;
