-- Lista de casos de teste na aba do modulo.
-- Descricao de cada caso/grupo no .mds (preenchida na proxima rodagem de cada modulo).
ALTER TABLE public.agent_tc_testcase_hierarchy
ADD COLUMN IF NOT EXISTS description TEXT;

-- Casos marcados como desativados no dashboard (so visual: nome riscado na lista).
CREATE TABLE IF NOT EXISTS public.agent_tc_testcase_disabled (
  id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL,
  module_slug TEXT,
  reason TEXT,
  disabled_by TEXT,
  disabled_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_tc_testcase_disabled_module ON public.agent_tc_testcase_disabled(module_slug);

INSERT INTO public.agent_tc_schema_migrations(version, applied_at)
VALUES ('011_testcase_list', now())
ON CONFLICT (version) DO NOTHING;
