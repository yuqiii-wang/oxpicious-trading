-- Secret config — every active+retired secret row (no key material: the
-- api_key stays encrypted at rest and only api_key_hint surfaces).
-- Serves GET /api/config/secrets.
SELECT secret_id, secret_type, provider, model_name, context_length,
       api_url, api_key_hint, is_active, notes, updated_at
  FROM static.secret_config
 ORDER BY secret_type, provider, model_name NULLS FIRST
