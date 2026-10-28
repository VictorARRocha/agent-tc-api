-- Pedidos de importacao de rodagem (pasta do log compactado) feitos pelo dashboard e executados pelo RegravacaoBridge.
CREATE TABLE IF NOT EXISTS public.agent_tc_import_requests (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  requested_by TEXT,
  run_folder TEXT NOT NULL,
  vm TEXT,
  exit_code INTEGER,
  output TEXT,
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_agent_tc_import_requests_status ON public.agent_tc_import_requests(status);

INSERT INTO public.agent_tc_schema_migrations(version, applied_at)
VALUES ('013_import_requests', now())
ON CONFLICT (version) DO NOTHING;
