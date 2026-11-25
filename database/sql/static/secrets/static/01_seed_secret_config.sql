-- ----------------------------------------------------------------------------
--  Seed — static.secret_config initial population (fresh-install source
--  of record; re-appliable). SIX structural rows. The THREE local-host
--  rows (llm/dummy, jev/local, tokenization/local) embed their API key
--  INLINE: the KNOWN DUMMY value `lmh-dummy` (plaintext documented
--  right here — it is NOT a secret; the resident host checks that the
--  Bearer header is non-empty, not which secret it carries), AES-256-GCM
--  encrypted into api_key_enc in the exact `v1:<b64 iv>.<b64 ct||tag>`
--  format data_viz's secret-box.service writes (master key =
--  LLM_SECRET_KEY from the git-tracked database/.env, so the blobs
--  decrypt on every install of this repo), plus the masked
--  api_key_hint (•••••••• + last 4):
--
--    llm        / zhipu (ext_llm_provider)  / glm-5.3-flash / general
--                 / chat URL / KEYLESS (real key)
--    llm        / dummy (ext_llm_provider)  / dummy-1       / general
--                 / NULL — the ALWAYS-PRESENT offline test double
--                 (INACTIVE: activating it makes the deep agent run the
--                 dummy LLM) / lmh-dummy embedded
--    jev        / local (ext_llm_provider)  / onejev-1.6-4b / general
--                 / the resident host's /v1/systemone URL — its REAL
--                 Jev / System One decision model (retyped out of llm
--                 2026-11-11: a decision-model credential, NOT a chat
--                 one — the deep agent's chat config reads ONLY llm
--                 rows, so activating it redirects nothing)
--                 / lmh-dummy embedded
--    tokenization / local (ext_llm_provider) / word-segmenter-zh
--                 / general / the resident host's segment URL — its
--                 StructBERT word segmenter (the ChineseWordSegmenter
--                 host-first lane) / lmh-dummy embedded
--    web_search / zhipu (ext_http_source)   / search_pro    / pro
--                 / web_search URL / KEYLESS (real key)
--    web_search / zhihu (ext_http_source)   / NULL          / general
--                 / zhihu search URL / KEYLESS (real key)
--
--  provider joins static.label_dim per the SELECTED-DOMAIN contract:
--  'ext_llm_provider' for the llm / jev / tokenization rows,
--  'ext_http_source' for the web_search rows. sub_type joins
--  'secret_sub_type' the same way (glm flash + the dummy + the local
--  models + zhihu content search are the general tier; the zhipu
--  search_pro engine is the pro tier). The REAL API KEYS (zhipu /
--  zhihu) live nowhere in git — they migrate from the (deleted) root
--  .env by temp_scripts/migrate_env_secrets.mjs or are entered
--  through the Config → Secrets tab (that write encrypts them into
--  api_key_enc).
--  ON CONFLICT DO NOTHING — the UI is the source of record after the
--  first insert; a seed re-apply never clobbers a UI edit (and a
--  re-apply on a live DB whose rows already carry keys changes
--  nothing — the embedded blobs only land on a FRESH install).
-- ----------------------------------------------------------------------------
INSERT INTO static.secret_config
    (secret_type, provider, sub_type, model_name, context_length,
     input_modality, output_modality, api_url,
     is_active, notes)
SELECT st.code, ld.code, sd.code, 'glm-5.3-flash', NULL,
       im.code, om.code,
       'https://open.bigmodel.cn/api/paas/v4/chat/completions', TRUE,
       'seed — mirrors ZhipuChatProvider''s built-in constants (model + '
       'chat URL); the API key migrates from the root .env or is set '
       'through the Config → Secrets tab'
FROM static.label_dim st, static.label_dim ld, static.label_dim sd,
     static.label_dim im, static.label_dim om
WHERE st.domain = 'secret_type' AND st.label = 'llm'
  AND ld.domain = 'ext_llm_provider' AND ld.label = 'zhipu'
  AND sd.domain = 'secret_sub_type' AND sd.label = 'general'
  AND im.domain = 'llm_modality' AND im.label = 'text+vision'
  AND om.domain = 'llm_modality' AND om.label = 'text'
ON CONFLICT (secret_type, provider, COALESCE(model_name, '')) DO NOTHING;

-- The dummy langgraph double (test/dummy_langgraph at the project
-- root): the offline provider the langgraph / agent tests and smoke
-- runs use so the harness exercises without provider spend. INACTIVE
-- by construction — is_active TRUE would make this row THE active llm
-- config and silently redirect every real run. Two selection paths
-- that do NOT need the row active: RunRequest.provider / the CLI's
-- --provider dummy (the REAL transport HTTP-sim against the resident
-- local-model host's dummy-1 simulation — api_url empty defaults to
-- http://127.0.0.1:8767/v1/chat/completions since the in-process
-- factory lane was removed 2026-11-11). Activating the row flips
-- EVERY run to the dummy (config-provider semantics); the endpoint —
-- python -m server_cluster.llm, the watchdog-supervised llm area of
-- the server cluster (the local-model host, migrated from
-- llm_agents.local_model_host 2026-11) that absorbed the former
-- standalone test.dummy_llm.server; the known dummy key "lmh-dummy"
-- rides in api_key_enc — embedded in THIS seed's INSERT below.
INSERT INTO static.secret_config
    (secret_type, provider, sub_type, model_name, context_length, api_url,
     api_key_enc, api_key_hint, is_active, notes)
SELECT st.code, ld.code, sd.code, 'dummy-1', NULL,
       NULL,
       'v1:B9G6zTV2dHnmdmlY.qCT6+abuTA2fF+nwV1hNnfGSG0LFdme6Nw==',
       '••••••••ummy', FALSE,
       'seed — the always-present offline test double '
       '(test.dummy_langgraph.DummyLanggraphProvider; inactive: activate '
       'deliberately to run every deep-agent conversation on the dummy '
       'langgraph double — HTTP-sim runs the REAL transport against the '
       'local model host''s dummy-1 endpoint (127.0.0.1:8767) + the '
       'lmh-dummy api key (embedded in this seed, encrypted at rest; '
       'api_url empty defaults there since 2026-11-11); or select per '
       'run via RunRequest.provider / --provider dummy without '
       'activating)'
FROM static.label_dim st, static.label_dim ld, static.label_dim sd
WHERE st.domain = 'secret_type' AND st.label = 'llm'
  AND ld.domain = 'ext_llm_provider' AND ld.label = 'dummy'
  AND sd.domain = 'secret_sub_type' AND sd.label = 'general'
ON CONFLICT (secret_type, provider, COALESCE(model_name, '')) DO NOTHING;

-- The resident local-model host's REAL Jev / System One decision
-- model (server_cluster.llm — the watchdog-supervised llm area of
-- the server cluster (the local-model host, migrated from
-- llm_agents.local_model_host 2026-11); onejev-1.6-4b per
-- host_config.json, served under the standard /v1/systemone
-- contract: noul/choice/score questions in, calibrated probabilities
-- out). Typed jev (NOT llm) since 2026-11-11 — a decision-model
-- credential, not a chat one: the deep agent's chat config reads
-- ONLY llm rows, so activating this row redirects nothing. The api
-- key is the KNOWN DUMMY lmh-dummy value (plaintext documented in
-- this file's header — the host accepts any non-empty Bearer when
-- its auth_token is null), embedded in the INSERT below.
INSERT INTO static.secret_config
    (secret_type, provider, sub_type, model_name, context_length, api_url,
     api_key_enc, api_key_hint, is_active, notes)
SELECT st.code, ld.code, sd.code, 'onejev-1.6-4b', NULL,
       'http://127.0.0.1:8767/v1/systemone',
       'v1:dxz8FPC5ahDXh3VA.RyvWUbpS93ch+/Pm3b2y4rZxCmYFxKeiCw==',
       '••••••••ummy', TRUE,
       'seed — the resident local-model host''s Jev / System One '
       'decision model (server_cluster.llm, onejev-1.6-4b, the '
       'standard /v1/systemone contract; typed jev, not llm — the '
       'deep agent''s chat config reads only llm rows, so activation '
       'redirects nothing; the lmh-dummy api key is embedded in this '
       'seed (encrypted at rest) — the host accepts any non-empty '
       'Bearer while its auth_token is null)'
FROM static.label_dim st, static.label_dim ld, static.label_dim sd
WHERE st.domain = 'secret_type' AND st.label = 'jev'
  AND ld.domain = 'ext_llm_provider' AND ld.label = 'local'
  AND sd.domain = 'secret_sub_type' AND sd.label = 'general'
ON CONFLICT (secret_type, provider, COALESCE(model_name, '')) DO NOTHING;

-- The resident local-model host's tokenization model — the StructBERT
-- Chinese word segmenter (word-segmenter-zh per host_config.json,
-- POST /v1/tokenization/segment: {texts} in, {words} out). The
-- host-first lane of _common/tokenization/
-- chinese_word_segmenter.py: the in-process torch path stays the
-- fallback (a host outage must never stop a pipeline). Typed
-- tokenization (NOT llm) — the segmenter is not a chat credential;
-- model_name is informational (the segment endpoint resolves the
-- host's single registered segmenter without it). Same KNOWN DUMMY
-- lmh-dummy key story as the jev row above — embedded in the INSERT.
INSERT INTO static.secret_config
    (secret_type, provider, sub_type, model_name, context_length, api_url,
     api_key_enc, api_key_hint, is_active, notes)
SELECT st.code, ld.code, sd.code, 'word-segmenter-zh', NULL,
       'http://127.0.0.1:8767/v1/tokenization/segment',
       'v1:IMWx/xnqEtUrN3dZ.4Fe/noGL82hFyBwQfRg+41mjvZOx77S5KQ==',
       '••••••••ummy', TRUE,
       'seed — the resident local-model host''s StructBERT Chinese '
       'word segmenter (server_cluster.llm, word-segmenter-zh, '
       'POST /v1/tokenization/segment; the host-first lane of '
       '_common/tokenization/chinese_word_segmenter.py — the '
       'in-process torch path stays the fallback; model_name is '
       'informational, the endpoint resolves the host''s single '
       'registered segmenter; the lmh-dummy api key is embedded in '
       'this seed, encrypted at rest)'
FROM static.label_dim st, static.label_dim ld, static.label_dim sd
WHERE st.domain = 'secret_type' AND st.label = 'tokenization'
  AND ld.domain = 'ext_llm_provider' AND ld.label = 'local'
  AND sd.domain = 'secret_sub_type' AND sd.label = 'general'
ON CONFLICT (secret_type, provider, COALESCE(model_name, '')) DO NOTHING;

-- The resident local-model host's OCR model — PaddleOCR-VL
-- (paddleocr-vl-1.6 per host_config.json, POST /v1/ocr: images in
-- (base64/multipart), per-image transcription out; the layout mode's
-- text spotting feeds builds/text/image_text.py's Pseudo Imagea
-- blocks). Typed ocr (NOT llm) — a model-serving credential like
-- jev/tokenization; input_modality text+vision (it ACCEPTS images),
-- output text. The endpoint enforces the Bearer like every serving
-- route — same KNOWN DUMMY lmh-dummy key story as the jev row above,
-- embedded in the INSERT below.
INSERT INTO static.secret_config
    (secret_type, provider, sub_type, model_name, context_length,
     input_modality, output_modality, api_url,
     api_key_enc, api_key_hint, is_active, notes)
SELECT st.code, ld.code, sd.code, 'paddleocr-vl-1.6', NULL,
       im.code, om.code,
       'http://127.0.0.1:8767/v1/ocr',
       'v1:dxz8FPC5ahDXh3VA.RyvWUbpS93ch+/Pm3b2y4rZxCmYFxKeiCw==',
       '••••••••ummy', TRUE,
       'seed — the resident local-model host''s PaddleOCR-VL image '
       'transcriber (server_cluster.llm, paddleocr-vl-1.6, '
       'POST /v1/ocr; typed ocr, not llm — a model-serving credential; '
       'input_modality text+vision: the model accepts images, output '
       'text; the endpoint enforces the Bearer like every serving '
       'route — the lmh-dummy api key is embedded in this seed '
       '(encrypted at rest), the host accepts any non-empty Bearer '
       'while its auth_token is null)'
FROM static.label_dim st, static.label_dim ld, static.label_dim sd,
     static.label_dim im, static.label_dim om
WHERE st.domain = 'secret_type' AND st.label = 'ocr'
  AND ld.domain = 'ext_llm_provider' AND ld.label = 'local'
  AND sd.domain = 'secret_sub_type' AND sd.label = 'general'
  AND im.domain = 'llm_modality' AND im.label = 'text+vision'
  AND om.domain = 'llm_modality' AND om.label = 'text'
ON CONFLICT (secret_type, provider, COALESCE(model_name, '')) DO NOTHING;

INSERT INTO static.secret_config
    (secret_type, provider, sub_type, model_name, context_length, api_url,
     is_active, notes)
SELECT st.code, ld.code, sd.code, 'search_pro', NULL,
       'https://open.bigmodel.cn/api/paas/v4/web_search', TRUE,
       'seed — mirrors ZhipuSearchProvider''s built-in constants (engine '
       '+ web_search URL); the API key migrates from the root .env or '
       'is set through the Config → Secrets tab'
FROM static.label_dim st, static.label_dim ld, static.label_dim sd
WHERE st.domain = 'secret_type' AND st.label = 'web_search'
  AND ld.domain = 'ext_http_source' AND ld.label = 'zhipu'
  AND sd.domain = 'secret_sub_type' AND sd.label = 'pro'
ON CONFLICT (secret_type, provider, COALESCE(model_name, '')) DO NOTHING;

INSERT INTO static.secret_config
    (secret_type, provider, sub_type, model_name, context_length, api_url,
     is_active, notes)
SELECT st.code, ld.code, sd.code, NULL, NULL,
       'https://developer.zhihu.com/api/v1/content/zhihu_search', TRUE,
       'seed — mirrors ZhihuSearchProvider''s built-in constant (zhihu '
       'content-search URL; no engine vocabulary); the API key migrates '
       'from the root .env or is set through the Config → Secrets tab'
FROM static.label_dim st, static.label_dim ld, static.label_dim sd
WHERE st.domain = 'secret_type' AND st.label = 'web_search'
  AND ld.domain = 'ext_http_source' AND ld.label = 'zhihu'
  AND sd.domain = 'secret_sub_type' AND sd.label = 'general'
ON CONFLICT (secret_type, provider, COALESCE(model_name, '')) DO NOTHING;
