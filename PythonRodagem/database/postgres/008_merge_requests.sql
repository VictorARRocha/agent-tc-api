-- Merge entre branches do projeto do TC pedido pelo dashboard e executado pelo MergeBridge (D01).
CREATE TABLE IF NOT EXISTS public.agent_tc_merge_requests (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  requested_by TEXT,
  source_url TEXT NOT NULL,
  source_name TEXT,
  target_url TEXT NOT NULL,
  target_name TEXT,
  target_kind TEXT,
  commit_message TEXT,
  preview_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  result_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  svn_revision TEXT,
  error_message TEXT,
  confirmed_by TEXT,
  confirmed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_agent_tc_merge_requests_status ON public.agent_tc_merge_requests(status);
CREATE INDEX IF NOT EXISTS idx_agent_tc_merge_requests_created ON public.agent_tc_merge_requests(created_at);

-- Lista de branches do repositorio do TC, enviada periodicamente pelo MergeBridge (uma linha so).
CREATE TABLE IF NOT EXISTS public.agent_tc_svn_branch_cache (
  id TEXT PRIMARY KEY,
  branches_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL
);

INSERT INTO public.agent_tc_schema_migrations(version, applied_at)
VALUES ('008_merge_requests', now())
ON CONFLICT (version) DO NOTHING;
