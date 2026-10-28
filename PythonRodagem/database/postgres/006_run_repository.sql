-- Origem SVN da rodagem (URL/raiz/revisao da copia de trabalho na VM), usada pela regravacao.
ALTER TABLE public.agent_tc_runs ADD COLUMN IF NOT EXISTS repository_url TEXT;
ALTER TABLE public.agent_tc_runs ADD COLUMN IF NOT EXISTS repository_root TEXT;
ALTER TABLE public.agent_tc_runs ADD COLUMN IF NOT EXISTS repository_revision TEXT;

INSERT INTO public.agent_tc_schema_migrations(version, applied_at)
VALUES ('006_run_repository', now())
ON CONFLICT (version) DO NOTHING;
