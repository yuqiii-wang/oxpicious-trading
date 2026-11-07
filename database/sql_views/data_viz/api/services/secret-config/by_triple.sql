-- Secret config — the stored row by the form's type+provider+model
-- triple (the update form's fill source when the id is absent; carries
-- api_key_enc so omitted form fields reuse the stored key; model match
-- treats '' and NULL as the same).
-- Serves POST /api/config/secrets (and PUT /api/config/secrets/:id).
-- args:
--   secret_type: value smallint — static.label_dim 'secret_type' code
--   provider: value smallint — static.label_dim 'ext_llm_provider' code
--   model_name: value text — nullable model name ('' == NULL)
SELECT secret_id, secret_type, provider, model_name, context_length,
       api_url, api_key_enc, api_key_hint, is_active, notes, updated_at
  FROM static.secret_config
 WHERE secret_type = {{secret_type}}::smallint AND provider = {{provider}}::smallint
   AND COALESCE(model_name, '') = COALESCE({{model_name}}, '')
