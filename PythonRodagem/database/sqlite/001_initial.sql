PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
  version TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS modules (
  id TEXT PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  system TEXT NOT NULL,
  codes_json TEXT NOT NULL DEFAULT '[]',
  active INTEGER NOT NULL DEFAULT 1,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  system TEXT NOT NULL,
  version TEXT NOT NULL,
  vm_name TEXT NOT NULL,
  module_id TEXT NOT NULL REFERENCES modules(id),
  started_at TEXT NOT NULL,
  finished_at TEXT,
  logs_path TEXT,
  status TEXT NOT NULL,
  total_archives INTEGER NOT NULL DEFAULT 0,
  total_occurrences INTEGER NOT NULL DEFAULT 0,
  total_executed INTEGER,
  total_ai_groups INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_module_started ON runs(module_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_vm_started ON runs(vm_name, started_at DESC);

CREATE TABLE IF NOT EXISTS ingestion_batches (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  status TEXT NOT NULL,
  summary_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS testcase_hierarchy (
  id TEXT PRIMARY KEY,
  system TEXT NOT NULL,
  module_id TEXT NOT NULL REFERENCES modules(id),
  module_code TEXT NOT NULL,
  module_name TEXT NOT NULL,
  node_id TEXT NOT NULL,
  parent_node_id TEXT,
  node_name TEXT NOT NULL,
  node_type TEXT NOT NULL,
  full_path_ids_json TEXT NOT NULL DEFAULT '[]',
  full_path_names_json TEXT NOT NULL DEFAULT '[]',
  full_path_label TEXT,
  script_name TEXT,
  procedure_name TEXT,
  mds_path TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(system, node_id)
);

CREATE INDEX IF NOT EXISTS idx_testcase_hierarchy_module ON testcase_hierarchy(module_id);
CREATE INDEX IF NOT EXISTS idx_testcase_hierarchy_parent ON testcase_hierarchy(parent_node_id);
CREATE INDEX IF NOT EXISTS idx_testcase_hierarchy_node ON testcase_hierarchy(node_id);

CREATE TABLE IF NOT EXISTS occurrences (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  module_id TEXT NOT NULL REFERENCES modules(id),
  testcase_node_id TEXT NOT NULL,
  testcase_name TEXT NOT NULL,
  testcase_description TEXT,
  group_node_id TEXT,
  group_name TEXT,
  source_archive_name TEXT,
  source_archive_size_bytes INTEGER,
  occurrence_type TEXT NOT NULL,
  status TEXT NOT NULL,
  error_message TEXT,
  log_summary TEXT,
  technical_signature TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_occurrences_run ON occurrences(run_id);
CREATE INDEX IF NOT EXISTS idx_occurrences_module ON occurrences(module_id);
CREATE INDEX IF NOT EXISTS idx_occurrences_type ON occurrences(occurrence_type);
CREATE INDEX IF NOT EXISTS idx_occurrences_testcase ON occurrences(testcase_node_id);

CREATE TABLE IF NOT EXISTS evidence_files (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  occurrence_id TEXT REFERENCES occurrences(id) ON DELETE CASCADE,
  module_id TEXT NOT NULL REFERENCES modules(id),
  file_role TEXT NOT NULL,
  file_type TEXT NOT NULL,
  original_name TEXT NOT NULL,
  local_path TEXT,
  storage_provider TEXT NOT NULL,
  storage_bucket TEXT,
  storage_path TEXT,
  public_url TEXT,
  signed_url TEXT,
  signed_url_expires_at TEXT,
  mime_type TEXT,
  extension TEXT,
  size_bytes INTEGER,
  sha256 TEXT,
  upload_status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_evidence_run ON evidence_files(run_id);
CREATE INDEX IF NOT EXISTS idx_evidence_occurrence ON evidence_files(occurrence_id);
CREATE INDEX IF NOT EXISTS idx_evidence_role ON evidence_files(file_role);
CREATE INDEX IF NOT EXISTS idx_evidence_storage_path ON evidence_files(storage_path);

CREATE TABLE IF NOT EXISTS report_differences (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  occurrence_id TEXT NOT NULL REFERENCES occurrences(id) ON DELETE CASCADE,
  module_id TEXT NOT NULL REFERENCES modules(id),
  testcase_node_id TEXT NOT NULL,
  base_evidence_id TEXT REFERENCES evidence_files(id),
  current_evidence_id TEXT REFERENCES evidence_files(id),
  base_file_name TEXT NOT NULL,
  current_file_name TEXT NOT NULL,
  base_lines INTEGER,
  current_lines INTEGER,
  changed_lines_estimate INTEGER,
  summary_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_report_differences_run ON report_differences(run_id);
CREATE INDEX IF NOT EXISTS idx_report_differences_occurrence ON report_differences(occurrence_id);

CREATE TABLE IF NOT EXISTS ai_analysis_jobs (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  model TEXT,
  request_json TEXT NOT NULL DEFAULT '{}',
  response_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  error_message TEXT,
  created_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS ai_groups (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  module_id TEXT NOT NULL REFERENCES modules(id),
  ai_analysis_job_id TEXT REFERENCES ai_analysis_jobs(id),
  title TEXT NOT NULL,
  technical_signature TEXT NOT NULL,
  classification TEXT,
  confidence INTEGER,
  justification TEXT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_groups_run ON ai_groups(run_id);

CREATE TABLE IF NOT EXISTS ai_group_occurrences (
  group_id TEXT NOT NULL REFERENCES ai_groups(id) ON DELETE CASCADE,
  occurrence_id TEXT NOT NULL REFERENCES occurrences(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  PRIMARY KEY (group_id, occurrence_id)
);

CREATE INDEX IF NOT EXISTS idx_ai_group_occurrences_occurrence ON ai_group_occurrences(occurrence_id);

CREATE TABLE IF NOT EXISTS recommended_actions (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  group_id TEXT REFERENCES ai_groups(id) ON DELETE CASCADE,
  occurrence_id TEXT REFERENCES occurrences(id) ON DELETE CASCADE,
  category TEXT NOT NULL,
  hypothesis TEXT,
  action TEXT NOT NULL,
  confidence INTEGER,
  priority TEXT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_recommended_actions_run ON recommended_actions(run_id);

CREATE TABLE IF NOT EXISTS run_delays (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  module_id TEXT NOT NULL REFERENCES modules(id),
  testcase_node_id TEXT NOT NULL,
  testcase_name TEXT,
  expected_seconds INTEGER NOT NULL,
  actual_seconds INTEGER NOT NULL,
  delay_seconds INTEGER NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_delays_run ON run_delays(run_id);

CREATE TABLE IF NOT EXISTS rerun_requests (
  id TEXT PRIMARY KEY,
  source_run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
  vm_name TEXT NOT NULL,
  version TEXT NOT NULL,
  module_id TEXT REFERENCES modules(id),
  test_cases TEXT NOT NULL,
  parallel TEXT,
  ct_desmarcar TEXT,
  branch TEXT,
  requested_by TEXT,
  request_type TEXT NOT NULL,
  configuration_mode TEXT NOT NULL,
  config_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  jenkins_queue_url TEXT,
  jenkins_build_url TEXT,
  jenkins_build_number TEXT,
  execution_status TEXT,
  execution_result TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rerun_requests_status ON rerun_requests(status);
CREATE INDEX IF NOT EXISTS idx_rerun_requests_created ON rerun_requests(created_at DESC);

CREATE TABLE IF NOT EXISTS run_presets (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  mode TEXT NOT NULL,
  config_json TEXT NOT NULL DEFAULT '{}',
  created_by TEXT,
  updated_by TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_presets_mode ON run_presets(mode);

CREATE TABLE IF NOT EXISTS regravacao_requests (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  status TEXT NOT NULL,
  requested_by TEXT,
  repository_url TEXT NOT NULL,
  repository_root TEXT,
  commit_message TEXT,
  items_json TEXT NOT NULL DEFAULT '[]',
  result_json TEXT NOT NULL DEFAULT '{}',
  svn_revision TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_regravacao_requests_status ON regravacao_requests(status);
CREATE INDEX IF NOT EXISTS idx_regravacao_requests_run ON regravacao_requests(run_id);

CREATE TABLE IF NOT EXISTS import_requests (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  requested_by TEXT,
  run_folder TEXT NOT NULL,
  vm TEXT,
  branch_url TEXT,
  exit_code INTEGER,
  output TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_import_requests_status ON import_requests(status);

CREATE TABLE IF NOT EXISTS merge_requests (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  requested_by TEXT,
  source_url TEXT NOT NULL,
  source_name TEXT,
  target_url TEXT NOT NULL,
  target_name TEXT,
  target_kind TEXT,
  commit_message TEXT,
  preview_json TEXT NOT NULL DEFAULT '{}',
  result_json TEXT NOT NULL DEFAULT '{}',
  svn_revision TEXT,
  error_message TEXT,
  confirmed_by TEXT,
  confirmed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_merge_requests_status ON merge_requests(status);

CREATE TABLE IF NOT EXISTS svn_branch_cache (
  id TEXT PRIMARY KEY,
  branches_json TEXT NOT NULL DEFAULT '[]',
  updated_at TEXT NOT NULL
);

-- Casos de teste marcados como desativados no dashboard (so visual: nome riscado na lista).
CREATE TABLE IF NOT EXISTS testcase_disabled (
  id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL,
  module_slug TEXT,
  reason TEXT,
  disabled_by TEXT,
  disabled_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_testcase_disabled_module ON testcase_disabled(module_slug);

-- Tempo de cada caso de teste: medicoes por inicio de rodagem e tempo base por caso.
CREATE TABLE IF NOT EXISTS testcase_times (
  id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL,
  measured_at TEXT NOT NULL,
  seconds INTEGER NOT NULL,
  run_id TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_testcase_times_node ON testcase_times(node_id);
CREATE INDEX IF NOT EXISTS idx_testcase_times_run ON testcase_times(run_id);

CREATE TABLE IF NOT EXISTS testcase_time_bases (
  id TEXT PRIMARY KEY,
  base_seconds INTEGER NOT NULL,
  origin TEXT NOT NULL,
  previous_seconds INTEGER,
  changed_by TEXT,
  changed_at TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_migrations(version, applied_at)
VALUES ('001_initial', datetime('now'));
