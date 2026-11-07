-- SQL write action — static.secret_config UPDATE (UI: Config → Secrets tab, row edit).
-- Executed by data_viz/api/services/secret-config.service.ts through the pg
-- primary pool with $n parameters; convention of this tree (the app/layer
-- prefix mirrors the executing code's dir under data_viz/):
--   database/sql_writes/<app>/<layer>/<table>/<action>.sql
--
-- Params (node-postgres $n, in order):
--   $1 secret_id       INTEGER — the row to update
--   $2 secret_type     SMALLINT label code (static.label_dim 'secret_type': 0 llm | 1 web_search)
--   $3 provider        SMALLINT label code, domain SELECTED BY secret_type (the audit
--                      pattern): llm -> 'ext_llm_provider' (0 zhipu | 99 other);
--                      web_search -> 'ext_http_source' (15 zhipu | 13 zhihu | 99 other)
--   $4 model_name      TEXT | NULL — llm: the model id (required); web_search: the engine or NULL
--   $5 context_length  INTEGER | NULL — llm only: max context window in tokens
--   $6 api_url         TEXT | NULL — the credential's endpoint
--   $7 key_mode        INTEGER — the API key tri-state the UI form expresses:
--                        0 = KEEP   (no new key typed)   → api_key_enc/api_key_hint unchanged
--                        1 = REPLACE (new key typed)    → $8/$9 land (already encrypted/masked)
--                        2 = CLEAR  (explicit clear)    → api_key_enc/api_key_hint = NULL
--   $8 api_key_enc     TEXT | NULL — the REPLACEMENT blob (read only when key_mode = 1;
--                      plaintext NEVER reaches this file)
--   $9 api_key_hint    TEXT | NULL — the REPLACEMENT masked preview (read only when key_mode = 1)
--   $10 is_active      BOOLEAN
--   $11 notes          TEXT | NULL
--
-- No row matches an unknown/retired id → empty RETURNING (the caller maps
-- it to 404). Returns the non-secret columns only.
UPDATE static.secret_config
   SET secret_type    = $2::smallint,
       provider       = $3::smallint,
       model_name     = $4,
       context_length = $5::int,
       api_url        = $6,
       api_key_enc    = CASE WHEN $7::int = 1 THEN $8
                             WHEN $7::int = 2 THEN NULL
                             ELSE api_key_enc END,
       api_key_hint   = CASE WHEN $7::int = 1 THEN $9
                             WHEN $7::int = 2 THEN NULL
                             ELSE api_key_hint END,
       is_active      = $10::boolean,
       notes          = $11,
       updated_at     = now()
 WHERE secret_id = $1::int
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
