-- Branch do TC escolhida na importacao de rodagem (a D01 nao rodou a rodagem: o svn info dela nao serve).
ALTER TABLE public.agent_tc_import_requests ADD COLUMN IF NOT EXISTS branch_url TEXT;

INSERT INTO public.agent_tc_schema_migrations(version, applied_at)
VALUES ('014_import_branch', now())
ON CONFLICT (version) DO NOTHING;
