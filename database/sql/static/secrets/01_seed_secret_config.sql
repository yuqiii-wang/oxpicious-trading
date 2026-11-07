-- ----------------------------------------------------------------------------
--  Seed — static.secret_config initial population (fresh-install source
--  of record; re-appliable). FOUR structural rows, all KEYLESS:
--
--    llm        / zhipu (ext_llm_provider)  / glm-5.3-flash / chat URL
--    llm        / dummy (ext_llm_provider)  / dummy-1       / NULL — the
--                 ALWAYS-PRESENT offline test double (INACTIVE:
--                 activating it makes the deep agent run the dummy LLM)
--    web_search / zhipu (ext_http_source)   / search_pro    / web_search URL
--    web_search / zhihu (ext_http_source)   / NULL          / zhihu search URL
--
--  provider joins static.label_dim per the SELECTED-DOMAIN contract:
--  'ext_llm_provider' for the llm rows, 'ext_http_source' for the
--  web_search rows. The API KEYS live nowhere in git — they migrate
--  from the (deleted) root .env by temp_scripts/
--  migrate_env_secrets.mjs, or are entered through the Config →
--  Secrets tab (that write encrypts them into api_key_enc).
--  ON CONFLICT DO NOTHING — the UI is the source of record after the
--  first insert; a seed re-apply never clobbers a UI edit.
-- ----------------------------------------------------------------------------
INSERT INTO static.secret_config
    (secret_type, provider, model_name, context_length, api_url, is_active, notes)
SELECT st.code, ld.code, 'glm-5.3-flash', NULL,
       'https://open.bigmodel.cn/api/paas/v4/chat/completions', TRUE,
       'seed — mirrors ZhipuChatProvider''s built-in constants (model + '
       'chat URL); the API key migrates from the root .env or is set '
       'through the Config → Secrets tab'
FROM static.label_dim st, static.label_dim ld
WHERE st.domain = 'secret_type' AND st.label = 'llm'
  AND ld.domain = 'ext_llm_provider' AND ld.label = 'zhipu'
ON CONFLICT (secret_type, provider, COALESCE(model_name, '')) DO NOTHING;

-- The dummy LLM (llm_agents.test.dummy_llm): the offline provider the
-- langgraph / agent tests and smoke runs use so the harness exercises
-- without provider spend. INACTIVE by construction — is_active TRUE
-- would make this row THE active llm config and silently redirect
-- every real run. Two selection paths that do NOT need the row active:
-- RunRequest.provider / the CLI's --provider dummy (in-process canned
-- replies, or HTTP-sim through --dummy-url). Activating the row flips
-- EVERY run to the dummy (config-provider semantics); set api_url to
-- a running `python -m llm_agents.test.dummy_llm.server` endpoint
-- (default http://127.0.0.1:8768/v1/chat/completions) for the
-- HTTP-sim form (the real transport/retry layer against the
-- simulation), leave it NULL for the in-process form.
INSERT INTO static.secret_config
    (secret_type, provider, model_name, context_length, api_url, is_active, notes)
SELECT st.code, ld.code, 'dummy-1', NULL,
       NULL, FALSE,
       'seed — the always-present offline test double '
       '(llm_agents.test.dummy_llm.DummyLlmProvider; inactive: activate '
       'deliberately to run every deep-agent conversation on the dummy '
       'LLM — set api_url to the dummy llm server''s endpoint for '
       'HTTP-sim, leave NULL for in-process; or select per run via '
       'RunRequest.provider / --provider dummy without activating)'
FROM static.label_dim st, static.label_dim ld
WHERE st.domain = 'secret_type' AND st.label = 'llm'
  AND ld.domain = 'ext_llm_provider' AND ld.label = 'dummy'
ON CONFLICT (secret_type, provider, COALESCE(model_name, '')) DO NOTHING;

INSERT INTO static.secret_config
    (secret_type, provider, model_name, context_length, api_url, is_active, notes)
SELECT st.code, ld.code, 'search_pro', NULL,
       'https://open.bigmodel.cn/api/paas/v4/web_search', TRUE,
       'seed — mirrors ZhipuSearchProvider''s built-in constants (engine '
       '+ web_search URL); the API key migrates from the root .env or '
       'is set through the Config → Secrets tab'
FROM static.label_dim st, static.label_dim ld
WHERE st.domain = 'secret_type' AND st.label = 'web_search'
  AND ld.domain = 'ext_http_source' AND ld.label = 'zhipu'
ON CONFLICT (secret_type, provider, COALESCE(model_name, '')) DO NOTHING;

INSERT INTO static.secret_config
    (secret_type, provider, model_name, context_length, api_url, is_active, notes)
SELECT st.code, ld.code, NULL, NULL,
       'https://developer.zhihu.com/api/v1/content/zhihu_search', TRUE,
       'seed — mirrors ZhihuSearchProvider''s built-in constant (zhihu '
       'content-search URL; no engine vocabulary); the API key migrates '
       'from the root .env or is set through the Config → Secrets tab'
FROM static.label_dim st, static.label_dim ld
WHERE st.domain = 'secret_type' AND st.label = 'web_search'
  AND ld.domain = 'ext_http_source' AND ld.label = 'zhihu'
ON CONFLICT (secret_type, provider, COALESCE(model_name, '')) DO NOTHING;
