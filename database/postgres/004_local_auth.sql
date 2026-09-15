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
