CREATE TABLE IF NOT EXISTS public.agent_tc_schema_migrations (
  version TEXT PRIMARY KEY,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.agent_tc_modules (
  id TEXT PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  system TEXT NOT NULL,
  codes_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  active BOOLEAN NOT NULL DEFAULT true,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS public.agent_tc_runs (
  id TEXT PRIMARY KEY,
  system TEXT NOT NULL,
  version TEXT NOT NULL,
  vm_name TEXT NOT NULL,
  module_id TEXT NOT NULL REFERENCES public.agent_tc_modules(id),
  started_at TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ,
  logs_path TEXT,
  status TEXT NOT NULL,
  total_archives INTEGER NOT NULL DEFAULT 0,
  total_occurrences INTEGER NOT NULL DEFAULT 0,
  total_executed INTEGER,
  total_ai_groups INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_tc_runs_module_started ON public.agent_tc_runs(module_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_tc_runs_vm_started ON public.agent_tc_runs(vm_name, started_at DESC);

CREATE TABLE IF NOT EXISTS public.agent_tc_ingestion_batches (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES public.agent_tc_runs(id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  status TEXT NOT NULL,
  summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL,
  completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS public.agent_tc_testcase_hierarchy (
  id TEXT PRIMARY KEY,
  system TEXT NOT NULL,
  module_id TEXT NOT NULL REFERENCES public.agent_tc_modules(id),
  module_code TEXT NOT NULL,
  module_name TEXT NOT NULL,
  node_id TEXT NOT NULL,
  parent_node_id TEXT,
  node_name TEXT NOT NULL,
  node_type TEXT NOT NULL,
  full_path_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  full_path_names_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  full_path_label TEXT,
  script_name TEXT,
  procedure_name TEXT,
  mds_path TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  UNIQUE(system, node_id)
);

CREATE INDEX IF NOT EXISTS idx_agent_tc_hierarchy_module ON public.agent_tc_testcase_hierarchy(module_id);
CREATE INDEX IF NOT EXISTS idx_agent_tc_hierarchy_parent ON public.agent_tc_testcase_hierarchy(parent_node_id);
CREATE INDEX IF NOT EXISTS idx_agent_tc_hierarchy_node ON public.agent_tc_testcase_hierarchy(node_id);

CREATE TABLE IF NOT EXISTS public.agent_tc_occurrences (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES public.agent_tc_runs(id) ON DELETE CASCADE,
  module_id TEXT NOT NULL REFERENCES public.agent_tc_modules(id),
  testcase_node_id TEXT NOT NULL,
  testcase_name TEXT NOT NULL,
  testcase_description TEXT,
  group_node_id TEXT,
  group_name TEXT,
  source_archive_name TEXT,
  source_archive_size_bytes BIGINT,
  occurrence_type TEXT NOT NULL,
  status TEXT NOT NULL,
  error_message TEXT,
  log_summary TEXT,
  technical_signature TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_tc_occurrences_run ON public.agent_tc_occurrences(run_id);
CREATE INDEX IF NOT EXISTS idx_agent_tc_occurrences_module ON public.agent_tc_occurrences(module_id);
CREATE INDEX IF NOT EXISTS idx_agent_tc_occurrences_type ON public.agent_tc_occurrences(occurrence_type);
CREATE INDEX IF NOT EXISTS idx_agent_tc_occurrences_testcase ON public.agent_tc_occurrences(testcase_node_id);

CREATE TABLE IF NOT EXISTS public.agent_tc_evidence_files (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES public.agent_tc_runs(id) ON DELETE CASCADE,
  occurrence_id TEXT REFERENCES public.agent_tc_occurrences(id) ON DELETE CASCADE,
  module_id TEXT NOT NULL REFERENCES public.agent_tc_modules(id),
  file_role TEXT NOT NULL,
  file_type TEXT NOT NULL,
  original_name TEXT NOT NULL,
  local_path TEXT,
  storage_provider TEXT NOT NULL,
  storage_bucket TEXT,
  storage_path TEXT,
  public_url TEXT,
  signed_url TEXT,
  signed_url_expires_at TIMESTAMPTZ,
  mime_type TEXT,
  extension TEXT,
  size_bytes BIGINT,
  sha256 TEXT,
  upload_status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_tc_evidence_run ON public.agent_tc_evidence_files(run_id);
CREATE INDEX IF NOT EXISTS idx_agent_tc_evidence_occurrence ON public.agent_tc_evidence_files(occurrence_id);
CREATE INDEX IF NOT EXISTS idx_agent_tc_evidence_role ON public.agent_tc_evidence_files(file_role);
CREATE INDEX IF NOT EXISTS idx_agent_tc_evidence_storage_path ON public.agent_tc_evidence_files(storage_path);

CREATE TABLE IF NOT EXISTS public.agent_tc_report_differences (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES public.agent_tc_runs(id) ON DELETE CASCADE,
  occurrence_id TEXT NOT NULL REFERENCES public.agent_tc_occurrences(id) ON DELETE CASCADE,
  module_id TEXT NOT NULL REFERENCES public.agent_tc_modules(id),
  testcase_node_id TEXT NOT NULL,
  base_evidence_id TEXT REFERENCES public.agent_tc_evidence_files(id),
  current_evidence_id TEXT REFERENCES public.agent_tc_evidence_files(id),
  base_file_name TEXT NOT NULL,
  current_file_name TEXT NOT NULL,
  base_lines INTEGER,
  current_lines INTEGER,
  changed_lines_estimate INTEGER,
  summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_tc_report_differences_run ON public.agent_tc_report_differences(run_id);
CREATE INDEX IF NOT EXISTS idx_agent_tc_report_differences_occurrence ON public.agent_tc_report_differences(occurrence_id);

CREATE TABLE IF NOT EXISTS public.agent_tc_ai_analysis_jobs (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES public.agent_tc_runs(id) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  model TEXT,
  request_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  response_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  status TEXT NOT NULL,
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS public.agent_tc_ai_groups (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES public.agent_tc_runs(id) ON DELETE CASCADE,
  module_id TEXT NOT NULL REFERENCES public.agent_tc_modules(id),
  ai_analysis_job_id TEXT REFERENCES public.agent_tc_ai_analysis_jobs(id),
  title TEXT NOT NULL,
  technical_signature TEXT NOT NULL,
  classification TEXT,
  confidence INTEGER,
  justification TEXT,
  status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_tc_ai_groups_run ON public.agent_tc_ai_groups(run_id);

CREATE TABLE IF NOT EXISTS public.agent_tc_ai_group_occurrences (
  group_id TEXT NOT NULL REFERENCES public.agent_tc_ai_groups(id) ON DELETE CASCADE,
  occurrence_id TEXT NOT NULL REFERENCES public.agent_tc_occurrences(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (group_id, occurrence_id)
);

CREATE INDEX IF NOT EXISTS idx_agent_tc_ai_group_occurrences_occurrence ON public.agent_tc_ai_group_occurrences(occurrence_id);

CREATE TABLE IF NOT EXISTS public.agent_tc_recommended_actions (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES public.agent_tc_runs(id) ON DELETE CASCADE,
  group_id TEXT REFERENCES public.agent_tc_ai_groups(id) ON DELETE CASCADE,
  occurrence_id TEXT REFERENCES public.agent_tc_occurrences(id) ON DELETE CASCADE,
  category TEXT NOT NULL,
  hypothesis TEXT,
  action TEXT NOT NULL,
  confidence INTEGER,
  priority TEXT,
  status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_tc_recommended_actions_run ON public.agent_tc_recommended_actions(run_id);

CREATE TABLE IF NOT EXISTS public.agent_tc_run_delays (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES public.agent_tc_runs(id) ON DELETE CASCADE,
  module_id TEXT NOT NULL REFERENCES public.agent_tc_modules(id),
  testcase_node_id TEXT NOT NULL,
  testcase_name TEXT,
  expected_seconds INTEGER NOT NULL,
  actual_seconds INTEGER NOT NULL,
  delay_seconds INTEGER NOT NULL,
  status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_tc_run_delays_run ON public.agent_tc_run_delays(run_id);

CREATE TABLE IF NOT EXISTS public.agent_tc_rerun_requests (
  id TEXT PRIMARY KEY,
  source_run_id TEXT REFERENCES public.agent_tc_runs(id) ON DELETE SET NULL,
  vm_name TEXT NOT NULL,
  version TEXT NOT NULL,
  module_id TEXT REFERENCES public.agent_tc_modules(id),
  test_cases TEXT NOT NULL,
  parallel TEXT,
  ct_desmarcar TEXT,
  branch TEXT,
  requested_by TEXT,
  request_type TEXT NOT NULL,
  configuration_mode TEXT NOT NULL,
  config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  status TEXT NOT NULL,
  jenkins_queue_url TEXT,
  jenkins_build_url TEXT,
  jenkins_build_number TEXT,
  execution_status TEXT,
  execution_result TEXT,
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_tc_rerun_requests_status ON public.agent_tc_rerun_requests(status);
CREATE INDEX IF NOT EXISTS idx_agent_tc_rerun_requests_created ON public.agent_tc_rerun_requests(created_at DESC);

GRANT SELECT ON TABLE public.agent_tc_schema_migrations TO anon, authenticated;
GRANT SELECT ON TABLE public.agent_tc_modules TO anon, authenticated;
GRANT SELECT ON TABLE public.agent_tc_runs TO anon, authenticated;
GRANT SELECT ON TABLE public.agent_tc_ingestion_batches TO anon, authenticated;
GRANT SELECT ON TABLE public.agent_tc_testcase_hierarchy TO anon, authenticated;
GRANT SELECT ON TABLE public.agent_tc_occurrences TO anon, authenticated;
GRANT SELECT ON TABLE public.agent_tc_evidence_files TO anon, authenticated;
GRANT SELECT ON TABLE public.agent_tc_report_differences TO anon, authenticated;
GRANT SELECT ON TABLE public.agent_tc_ai_analysis_jobs TO anon, authenticated;
GRANT SELECT ON TABLE public.agent_tc_ai_groups TO anon, authenticated;
GRANT SELECT ON TABLE public.agent_tc_ai_group_occurrences TO anon, authenticated;
GRANT SELECT ON TABLE public.agent_tc_recommended_actions TO anon, authenticated;
GRANT SELECT ON TABLE public.agent_tc_run_delays TO anon, authenticated;
GRANT SELECT ON TABLE public.agent_tc_rerun_requests TO anon, authenticated;

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.agent_tc_schema_migrations TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.agent_tc_modules TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.agent_tc_runs TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.agent_tc_ingestion_batches TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.agent_tc_testcase_hierarchy TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.agent_tc_occurrences TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.agent_tc_evidence_files TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.agent_tc_report_differences TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.agent_tc_ai_analysis_jobs TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.agent_tc_ai_groups TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.agent_tc_ai_group_occurrences TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.agent_tc_recommended_actions TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.agent_tc_run_delays TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.agent_tc_rerun_requests TO service_role;

DO $$
DECLARE
  table_name TEXT;
  table_names TEXT[] := ARRAY[
    'agent_tc_schema_migrations',
    'agent_tc_modules',
    'agent_tc_runs',
    'agent_tc_ingestion_batches',
    'agent_tc_testcase_hierarchy',
    'agent_tc_occurrences',
    'agent_tc_evidence_files',
    'agent_tc_report_differences',
    'agent_tc_ai_analysis_jobs',
    'agent_tc_ai_groups',
    'agent_tc_ai_group_occurrences',
    'agent_tc_recommended_actions',
    'agent_tc_run_delays',
    'agent_tc_rerun_requests'
  ];
BEGIN
  FOREACH table_name IN ARRAY table_names LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', table_name);

    IF NOT EXISTS (
      SELECT 1
      FROM pg_policies
      WHERE schemaname = 'public'
        AND tablename = table_name
        AND policyname = 'agent_tc_read'
    ) THEN
      EXECUTE format(
        'CREATE POLICY agent_tc_read ON public.%I FOR SELECT TO anon, authenticated USING (true)',
        table_name
      );
    END IF;

    IF NOT EXISTS (
      SELECT 1
      FROM pg_policies
      WHERE schemaname = 'public'
        AND tablename = table_name
        AND policyname = 'agent_tc_service_write'
    ) THEN
      EXECUTE format(
        'CREATE POLICY agent_tc_service_write ON public.%I FOR ALL TO service_role USING (true) WITH CHECK (true)',
        table_name
      );
    END IF;
  END LOOP;
END $$;

INSERT INTO public.agent_tc_schema_migrations(version, applied_at)
VALUES ('001_initial', now())
ON CONFLICT (version) DO NOTHING;
