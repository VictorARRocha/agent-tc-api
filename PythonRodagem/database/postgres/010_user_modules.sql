-- Modulos de cada usuario para as acoes de rodagem e regravacao (ver rodagens continua livre).
-- Lista em JSON com os slugs (ex.: ["folha", "fiscal"]) ou ["*"] = todos os modulos.
-- Quem ja existia fica com ["*"] (nada muda no dia); novos cadastros gravam os modulos pedidos.
ALTER TABLE public.agent_tc_app_users ADD COLUMN IF NOT EXISTS modules JSONB NOT NULL DEFAULT '["*"]'::jsonb;

INSERT INTO public.agent_tc_schema_migrations(version, applied_at)
VALUES ('010_user_modules', now())
ON CONFLICT (version) DO NOTHING;
