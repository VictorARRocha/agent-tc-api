-- Agent TC canonical database schema backup
-- Reviewed: 2026-09-14
-- Includes: database/postgres/001_initial.sql + database/postgres/002_total_executed.sql + database/postgres/003_testcase_description.sql + database/postgres/004_local_auth.sql

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



-- Migration: 002_total_executed

ALTER TABLE public.agent_tc_runs
ADD COLUMN IF NOT EXISTS total_executed INTEGER;

INSERT INTO public.agent_tc_schema_migrations(version, applied_at)
VALUES ('002_total_executed', now())
ON CONFLICT (version) DO NOTHING;



-- Migration: 003_testcase_description

ALTER TABLE public.agent_tc_occurrences
ADD COLUMN IF NOT EXISTS testcase_description TEXT;

INSERT INTO public.agent_tc_schema_migrations(version, applied_at)
VALUES ('003_testcase_description', now())
ON CONFLICT (version) DO NOTHING;


-- Migration: 004_local_auth

CREATE TABLE IF NOT EXISTS public.agent_tc_app_users (
  id TEXT PRIMARY KEY,
  auth_user_id TEXT,
  username TEXT NOT NULL,
  username_normalized TEXT NOT NULL,
  first_name TEXT,
  last_name TEXT,
  email TEXT,
  role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'disabled')),
  password_hash TEXT,
  password_updated_at TIMESTAMPTZ,
  must_change_password BOOLEAN NOT NULL DEFAULT false,
  approved_at TIMESTAMPTZ,
  approved_by TEXT REFERENCES public.agent_tc_app_users(id) ON DELETE SET NULL,
  rejected_at TIMESTAMPTZ,
  rejected_by TEXT REFERENCES public.agent_tc_app_users(id) ON DELETE SET NULL,
  rejection_reason TEXT,
  disabled_at TIMESTAMPTZ,
  disabled_by TEXT REFERENCES public.agent_tc_app_users(id) ON DELETE SET NULL,
  last_login_at TIMESTAMPTZ,
  failed_login_attempts INTEGER NOT NULL DEFAULT 0,
  locked_until TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.agent_tc_app_users ADD COLUMN IF NOT EXISTS auth_user_id TEXT;
ALTER TABLE public.agent_tc_app_users ADD COLUMN IF NOT EXISTS username TEXT;
ALTER TABLE public.agent_tc_app_users ADD COLUMN IF NOT EXISTS username_normalized TEXT;
ALTER TABLE public.agent_tc_app_users ADD COLUMN IF NOT EXISTS first_name TEXT;
ALTER TABLE public.agent_tc_app_users ADD COLUMN IF NOT EXISTS last_name TEXT;
ALTER TABLE public.agent_tc_app_users ADD COLUMN IF NOT EXISTS email TEXT;
ALTER TABLE public.agent_tc_app_users ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'user';
ALTER TABLE public.agent_tc_app_users ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE public.agent_tc_app_users ADD COLUMN IF NOT EXISTS password_hash TEXT;
ALTER TABLE public.agent_tc_app_users ADD COLUMN IF NOT EXISTS password_updated_at TIMESTAMPTZ;
ALTER TABLE public.agent_tc_app_users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE public.agent_tc_app_users ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ;
ALTER TABLE public.agent_tc_app_users ADD COLUMN IF NOT EXISTS approved_by TEXT;
ALTER TABLE public.agent_tc_app_users ADD COLUMN IF NOT EXISTS rejected_at TIMESTAMPTZ;
ALTER TABLE public.agent_tc_app_users ADD COLUMN IF NOT EXISTS rejected_by TEXT;
ALTER TABLE public.agent_tc_app_users ADD COLUMN IF NOT EXISTS rejection_reason TEXT;
ALTER TABLE public.agent_tc_app_users ADD COLUMN IF NOT EXISTS disabled_at TIMESTAMPTZ;
ALTER TABLE public.agent_tc_app_users ADD COLUMN IF NOT EXISTS disabled_by TEXT;
ALTER TABLE public.agent_tc_app_users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;
ALTER TABLE public.agent_tc_app_users ADD COLUMN IF NOT EXISTS failed_login_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE public.agent_tc_app_users ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ;
ALTER TABLE public.agent_tc_app_users ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE public.agent_tc_app_users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_tc_app_users_username_normalized
  ON public.agent_tc_app_users(username_normalized);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_tc_app_users_auth_user_id
  ON public.agent_tc_app_users(auth_user_id)
  WHERE auth_user_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_tc_app_users_email
  ON public.agent_tc_app_users(lower(email))
  WHERE email IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_agent_tc_app_users_status
  ON public.agent_tc_app_users(status);
CREATE INDEX IF NOT EXISTS idx_agent_tc_app_users_approved_by
  ON public.agent_tc_app_users(approved_by);
CREATE INDEX IF NOT EXISTS idx_agent_tc_app_users_rejected_by
  ON public.agent_tc_app_users(rejected_by);
CREATE INDEX IF NOT EXISTS idx_agent_tc_app_users_disabled_by
  ON public.agent_tc_app_users(disabled_by);

CREATE TABLE IF NOT EXISTS public.agent_tc_auth_sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES public.agent_tc_app_users(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  last_seen_at TIMESTAMPTZ,
  revoked_at TIMESTAMPTZ,
  ip_address TEXT,
  user_agent TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_tc_auth_sessions_token_hash
  ON public.agent_tc_auth_sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_agent_tc_auth_sessions_user
  ON public.agent_tc_auth_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_agent_tc_auth_sessions_expires
  ON public.agent_tc_auth_sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_agent_tc_auth_sessions_active
  ON public.agent_tc_auth_sessions(user_id, expires_at)
  WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS public.agent_tc_permission_catalog (
  code TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  categoria TEXT,
  descricao TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.agent_tc_permission_catalog ADD COLUMN IF NOT EXISTS label TEXT;
ALTER TABLE public.agent_tc_permission_catalog ADD COLUMN IF NOT EXISTS categoria TEXT;
ALTER TABLE public.agent_tc_permission_catalog ADD COLUMN IF NOT EXISTS descricao TEXT;
ALTER TABLE public.agent_tc_permission_catalog ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE public.agent_tc_permission_catalog ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

INSERT INTO public.agent_tc_permission_catalog (code, label, categoria) VALUES
  ('dashboard.view', 'Ver dashboard', 'plataforma'),
  ('modules.view', 'Ver modulos', 'plataforma'),
  ('runs.view', 'Ver rodagens', 'plataforma'),
  ('failures.view', 'Ver falhas', 'plataforma'),
  ('groups.view', 'Ver agrupamentos', 'plataforma'),
  ('performance.view', 'Ver performance', 'plataforma'),
  ('history.view', 'Ver historico', 'plataforma'),
  ('evidence.view', 'Ver evidencias', 'plataforma'),
  ('jenkins.view', 'Acessar Jenkins', 'jenkins'),
  ('jenkins.run', 'Disparar Jenkins', 'jenkins'),
  ('admin.view', 'Ver admin', 'admin'),
  ('admin.users.manage', 'Gerenciar usuarios', 'admin'),
  ('admin.permissions.manage', 'Gerenciar permissoes', 'admin')
ON CONFLICT (code) DO UPDATE
SET label = EXCLUDED.label,
    categoria = EXCLUDED.categoria,
    updated_at = now();

CREATE TABLE IF NOT EXISTS public.agent_tc_user_permissions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES public.agent_tc_app_users(id) ON DELETE CASCADE,
  permission_code TEXT NOT NULL REFERENCES public.agent_tc_permission_catalog(code) ON DELETE CASCADE,
  granted_by TEXT REFERENCES public.agent_tc_app_users(id) ON DELETE SET NULL,
  granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, permission_code)
);

CREATE INDEX IF NOT EXISTS idx_agent_tc_user_permissions_user
  ON public.agent_tc_user_permissions(user_id);
CREATE INDEX IF NOT EXISTS idx_agent_tc_user_permissions_permission
  ON public.agent_tc_user_permissions(permission_code);
CREATE INDEX IF NOT EXISTS idx_agent_tc_user_permissions_granted_by
  ON public.agent_tc_user_permissions(granted_by);

CREATE TABLE IF NOT EXISTS public.agent_tc_user_module_permissions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES public.agent_tc_app_users(id) ON DELETE CASCADE,
  modulo_slug TEXT NOT NULL,
  granted_by TEXT REFERENCES public.agent_tc_app_users(id) ON DELETE SET NULL,
  granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, modulo_slug)
);

CREATE INDEX IF NOT EXISTS idx_agent_tc_user_module_permissions_user
  ON public.agent_tc_user_module_permissions(user_id);
CREATE INDEX IF NOT EXISTS idx_agent_tc_user_module_permissions_modulo
  ON public.agent_tc_user_module_permissions(modulo_slug);
CREATE INDEX IF NOT EXISTS idx_agent_tc_user_module_permissions_granted_by
  ON public.agent_tc_user_module_permissions(granted_by);

CREATE TABLE IF NOT EXISTS public.agent_tc_admin_audit_log (
  id TEXT PRIMARY KEY,
  actor_id TEXT REFERENCES public.agent_tc_app_users(id) ON DELETE SET NULL,
  actor_username TEXT,
  target_id TEXT REFERENCES public.agent_tc_app_users(id) ON DELETE SET NULL,
  target_username TEXT,
  action TEXT NOT NULL,
  details JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_tc_admin_audit_log_created
  ON public.agent_tc_admin_audit_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_tc_admin_audit_log_actor
  ON public.agent_tc_admin_audit_log(actor_id);
CREATE INDEX IF NOT EXISTS idx_agent_tc_admin_audit_log_target
  ON public.agent_tc_admin_audit_log(target_id);

INSERT INTO public.agent_tc_schema_migrations(version, applied_at)
VALUES ('004_local_auth', now())
ON CONFLICT (version) DO NOTHING;

