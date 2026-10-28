-- Pre-definicoes de rodagem Jenkins salvas pelo dashboard (compartilhadas entre usuarios).
CREATE TABLE IF NOT EXISTS public.agent_tc_run_presets (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  mode TEXT NOT NULL,
  config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_by TEXT,
  updated_by TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_tc_run_presets_mode ON public.agent_tc_run_presets(mode);

INSERT INTO public.agent_tc_schema_migrations(version, applied_at)
VALUES ('005_run_presets', now())
ON CONFLICT (version) DO NOTHING;
