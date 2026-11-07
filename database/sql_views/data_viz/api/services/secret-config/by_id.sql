-- Secret config — the stored row BY ID (the update form's fill source;
-- carries api_key_enc so omitted form fields reuse the stored key).
-- Serves PUT /api/config/secrets/:id (and POST /api/config/secrets).
-- args:
--   secret_id: value int — the row to fetch
SELECT secret_id, secret_type, provider, model_name, context_length,
       api_url, api_key_enc, api_key_hint, is_active, notes, updated_at
  FROM static.secret_config
 WHERE secret_id = {{secret_id}}::int
