-- SQL write action — static.secret_config INSERT (UI: Config → Secrets tab, "New").
-- Executed by data_viz/api/services/secret-config.service.ts through the pg
-- primary pool with $n parameters; convention of this tree (the app/layer
-- prefix mirrors the executing code's dir under data_viz/):
--   database/sql_writes/<app>/<layer>/<table>/<action>.sql
--
-- Params (node-postgres $n, in order):
--   $1 secret_type     SMALLINT label code (static.label_dim 'secret_type': 0 llm | 1 web_search)
--   $2 provider        SMALLINT label code, domain SELECTED BY secret_type (the audit
--                      pattern): llm -> 'ext_llm_provider' (0 zhipu | 99 other);
--                      web_search -> 'ext_http_source' (15 zhipu | 13 zhihu | 99 other)
--   $3 model_name      TEXT | NULL — llm: the model id (required); web_search: the engine or NULL
--   $4 context_length  INTEGER | NULL — llm only: max context window in tokens
--   $5 api_url         TEXT | NULL — the credential's endpoint
--   $6 api_key_enc     TEXT | NULL — ALREADY-ENCRYPTED key blob ('v1:…', AES-256-GCM by
--                      secret-box.service); plaintext NEVER reaches this file
--   $7 api_key_hint    TEXT | NULL — masked preview ('••••••••<last4>')
--   $8 is_active       BOOLEAN
--   $9 notes           TEXT | NULL
--
-- (secret_type, provider, COALESCE(model_name,'')) is unique — a duplicate is
-- a UI error (the caller maps the empty RETURNING to 409), not a silent
-- overwrite. Returns the non-secret columns only.
INSERT INTO static.secret_config
    (secret_type, provider, model_name, context_length, api_url,
     api_key_enc, api_key_hint, is_active, notes)
VALUES ($1::smallint, $2::smallint, $3, $4::int, $5, $6, $7, $8::boolean, $9)
ON CONFLICT (secret_type, provider, COALESCE(model_name, '')) DO NOTHING
RETURNING secret_id,
          secret_type,
          provider,
          model_name,
          context_length,
          api_url,
          api_key_hint,
          is_active,
          notes,
          updated_at;
