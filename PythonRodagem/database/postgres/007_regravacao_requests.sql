-- Pedidos de regravacao de bases (Stores\Files) feitos pelo dashboard e executados pelo RegravacaoBridge.
CREATE TABLE IF NOT EXISTS public.agent_tc_regravacao_requests (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES public.agent_tc_runs(id) ON DELETE CASCADE,
  status TEXT NOT NULL,
  requested_by TEXT,
  repository_url TEXT NOT NULL,
  repository_root TEXT,
  commit_message TEXT,
  items_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  result_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  svn_revision TEXT,
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ
);

ALTER TABLE public.agent_tc_regravacao_requests ADD COLUMN IF NOT EXISTS commit_message TEXT;

CREATE INDEX IF NOT EXISTS idx_agent_tc_regravacao_requests_status ON public.agent_tc_regravacao_requests(status);
CREATE INDEX IF NOT EXISTS idx_agent_tc_regravacao_requests_run ON public.agent_tc_regravacao_requests(run_id);

INSERT INTO public.agent_tc_schema_migrations(version, applied_at)
VALUES ('007_regravacao_requests', now())
ON CONFLICT (version) DO NOTHING;
