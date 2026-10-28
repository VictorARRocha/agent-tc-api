-- Permissoes por usuario (alem do papel admin): "rodagem" (solicitar rodagens), "merge" e "regravar".
-- Lista em JSON, ex.: ["rodagem", "merge"]. Vazia = so ver rodagens. Admin pode tudo, sem depender da lista.
-- Usuarios ja existentes ficam com a lista vazia; o admin libera pela tela de usuarios.
ALTER TABLE public.agent_tc_app_users ADD COLUMN IF NOT EXISTS permissions JSONB NOT NULL DEFAULT '[]'::jsonb;

INSERT INTO public.agent_tc_schema_migrations(version, applied_at)
VALUES ('009_user_permissions', now())
ON CONFLICT (version) DO NOTHING;
