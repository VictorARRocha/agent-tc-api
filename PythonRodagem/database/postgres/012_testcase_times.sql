-- Tempo de cada caso de teste (aba Performance sem a planilha do C:\Tempos TC).
-- Medicoes: uma por caso e inicio de rodagem (lidas do "Tempo CT.csv" que o TC grava no S:).
CREATE TABLE IF NOT EXISTS public.agent_tc_testcase_times (
  id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL,
  measured_at TIMESTAMPTZ NOT NULL,
  seconds INTEGER NOT NULL,
  run_id TEXT,
  created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_tc_testcase_times_node ON public.agent_tc_testcase_times(node_id, measured_at);
CREATE INDEX IF NOT EXISTS idx_agent_tc_testcase_times_run ON public.agent_tc_testcase_times(run_id);

-- Tempo base de cada caso (id = codigo do caso). origin: planilha | primeira_medicao | substituida.
CREATE TABLE IF NOT EXISTS public.agent_tc_testcase_time_bases (
  id TEXT PRIMARY KEY,
  base_seconds INTEGER NOT NULL,
  origin TEXT NOT NULL,
  previous_seconds INTEGER,
  changed_by TEXT,
  changed_at TIMESTAMPTZ NOT NULL
);

INSERT INTO public.agent_tc_schema_migrations(version, applied_at)
VALUES ('012_testcase_times', now())
ON CONFLICT (version) DO NOTHING;
