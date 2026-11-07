-- ----------------------------------------------------------------------------
--  Secrets config — static.secret_config, the UI-editable registry of
--  EXTERNAL CREDENTIALS (the Config page's Secrets tab; DDL home
--  database/sql/static/secrets/, writes via the SQL action files under
--  database/sql_writes/data_viz/api/secret_config/). Renamed from
--  static.llm_config 2026-11-06 when web-search credentials joined —
--  the rename DROPs the old table here (it held only the keyless seed
--  row at rename time; the project-root .env keys migrated in
--  separately by temp_scripts/migrate_env_secrets.mjs).
--
--  One row per (secret_type, provider, model_name): the endpoint +
--  context window + API key a consumer resolves AT STARTUP — the deep
--  agent its chat config at agent start
--  (llm_agents/deep_agents/llm_config.py), every BaseLlmProvider its
--  key as the env chain's successor (llm_agents/_core/secret_config.py
--  — the root .env was deleted after the migration).
--
--  secret_type is a static.label_dim SMALLINT code ('secret_type');
--  provider is the AUDIT'S SELECTED-DOMAIN pattern
--  (monitor.external_resource_audit): decoded via 'ext_llm_provider'
--  when secret_type = llm, via 'ext_http_source' when web_search.
--
--  The API key is the ONE secret this table stores: it lands ONLY as
--  AES-256-GCM ciphertext (api_key_enc, key = LLM_SECRET_KEY in
--  database/.env) plus a masked preview (api_key_hint, last 4 chars).
--  NO API column ever selects api_key_enc — the plaintext exists
--  exclusively in-process: the Node side encrypts on save, the Python
--  side decrypts at consumer start. DB dumps / replica WAL therefore
--  carry ciphertext only.
-- ----------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS static;

-- the llm_config era's table — renamed away (empty of user data at the
-- rename; keys arrive via the migration, not this DDL)
DROP TABLE IF EXISTS static.llm_config CASCADE;
DROP TABLE IF EXISTS static.secret_config CASCADE;

CREATE TABLE static.secret_config (
    secret_id      INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    secret_type    SMALLINT NOT NULL,  -- label code (static.label_dim 'secret_type'): 0=llm, 1=web_search
    provider       SMALLINT NOT NULL,  -- selected domain (see COMMENT): ext_llm_provider when llm; ext_http_source when web_search
    model_name     TEXT,               -- llm: the model id (REQUIRED on llm rows); web_search: the engine (e.g. search_pro) or NULL
    context_length INTEGER,            -- llm only: max CONTEXT window in tokens; NULL = unspecified
    api_url        TEXT,               -- the credential's endpoint; NULL = the consumer's built-in default
    api_key_enc    TEXT,               -- 'v1:<b64(iv 12B)>.<b64(ct||tag)>' AES-256-GCM; NEVER selected by the API
    api_key_hint   TEXT,               -- masked preview '••••••••<last 4>' — all the API ever surfaces
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,  -- FALSE retires the row without deleting it
    notes          TEXT,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- llm rows name their model (the chat payload needs it); web
    -- search rows may leave it NULL
    CONSTRAINT ck_secret_config_llm_model
        CHECK (secret_type <> 0 OR model_name IS NOT NULL)
);

-- (secret_type, provider, model) identity — model_name is nullable (a
-- zhihu search credential names no engine), so the uniqueness rides a
-- COALESCE expression index: a table-level UNIQUE cannot carry
-- expressions.
CREATE UNIQUE INDEX uq_secret_config_identity
    ON static.secret_config (secret_type, provider, COALESCE(model_name, ''));

COMMENT ON TABLE  static.secret_config IS 'External credential registry — one row per (secret_type, provider, model): endpoint + context length + API key (encrypted at rest). UI-editable (Config → Secrets tab) via database/sql_writes/data_viz/api/secret_config/; consumed at STARTUP (deep agent per run: llm_agents/deep_agents/llm_config.py; every provider''s key fallback: llm_agents/_core/secret_config.py — successor of the deleted root .env keys).';
COMMENT ON COLUMN static.secret_config.secret_id IS 'Surrogate identity — the UI update targets this id; (secret_type, provider, model_name) is the natural uniqueness (COALESCE index: web_search rows may name no model).';
COMMENT ON COLUMN static.secret_config.secret_type IS 'The credential family, as its static.label_dim (''secret_type'') SMALLINT code: 0 llm | 1 web_search. Selects the provider domain (audit pattern).';
COMMENT ON COLUMN static.secret_config.provider IS 'The credential''s provider — domain SELECTED BY secret_type (monitor.external_resource_audit pattern): ''ext_llm_provider'' when llm (0 zhipu | 99 other), ''ext_http_source'' when web_search (15 zhipu | 13 zhihu | 99 other).';
COMMENT ON COLUMN static.secret_config.model_name IS 'llm rows: the chat-completion model id, e.g. glm-5.3-flash (required — ck_secret_config_llm_model). web_search rows: the search engine, e.g. search_pro, or NULL (zhihu names none). TEXT by contract (open-ended vocabulary).';
COMMENT ON COLUMN static.secret_config.context_length IS 'llm only: the model''s max CONTEXT window in tokens (input + output); NULL = unspecified. Informational today — consumers enforce their own per-request output ceilings (deep_agent_config.json max_output_tokens).';
COMMENT ON COLUMN static.secret_config.api_url IS 'The credential''s endpoint URL (chat completions / web search); NULL = the consumer class keeps its built-in default. TEXT by contract (URLs are open-ended).';
COMMENT ON COLUMN static.secret_config.api_key_enc IS 'The API key, AES-256-GCM ENCRYPTED: ''v1:<base64 iv>.<base64 ciphertext||tag>'', key = the 32-byte LLM_SECRET_KEY from database/.env (Node crypto + Python cryptography.hazmat AESGCM share the format). Plaintext is never stored, never selected, never logged.';
COMMENT ON COLUMN static.secret_config.api_key_hint IS 'Masked preview of the stored key, ''••••••••'' + the last 4 characters — the ONLY key-shaped value any API/UI read returns. Recomputed server-side on every key set/replace.';
COMMENT ON COLUMN static.secret_config.is_active IS 'FALSE retires a row without deleting it — the startup loaders only consider is_active rows (no DELETE action exists).';
COMMENT ON COLUMN static.secret_config.updated_at IS 'Write stamp — set by the sql_writes INSERT/UPDATE actions and the seed''s DEFAULT.';
