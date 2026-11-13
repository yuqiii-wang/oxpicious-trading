-- ============================================================================
--  static.label_dim seed — MONITORING / INFRASTRUCTURE vocabularies
--
--  The operational monitors' closed vocabularies: the external request
--  audit's resource family, request host and LLM provider, the DB
--  health sampler's instance identity + WAL shipping state, the
--  secret registry's credential type + service tier, and the
--  maintainer backup audit's batch mode + outcome. Owning
--  tables: monitor.external_resource_audit (ext_resource /
--  ext_http_source / ext_llm_provider — sub_type's domain is selected
--  by resource_type), monitor.db_health (db_instance /
--  db_replication_state), static.secret_config (secret_type — the
--  provider domain is selected by it: ext_llm_provider when
--  llm / jev / tokenization, ext_http_source when web_search;
--  secret_sub_type — the credential service tier, ONE shared domain
--  across all types), maintenance.src_data_backup (backup_mode /
--  backup_status).
-- ============================================================================

INSERT INTO static.label_dim (domain, code, label, sort_order) VALUES
    -- ext_resource: monitor.external_resource_audit.resource_type — the
    -- external resource family (0 downloads HTTP proxy, 1 llm providers)
    ('ext_resource',     0, 'http', 0),
    ('ext_resource',     1, 'llm',  1),
    -- ext_http_source: sub_type when resource_type = 0 — the request
    -- host family (suffix-matched from the URL by
    -- _common/external_resource_audit.py); 99 = unrecognized host
    ('ext_http_source',  0, 'sse',        0),
    ('ext_http_source',  1, 'szse',       1),
    ('ext_http_source',  2, 'bse',        2),
    ('ext_http_source',  3, 'cffex',      3),
    ('ext_http_source',  4, 'csindex',    4),
    ('ext_http_source',  5, 'cnindex',    5),
    ('ext_http_source',  6, 'chinabond',  6),
    ('ext_http_source',  7, 'shibor',     7),
    ('ext_http_source',  8, 'huatai',     8),
    ('ext_http_source',  9, 'eastmoney',  9),
    ('ext_http_source', 10, 'cninfo',    10),
    ('ext_http_source', 11, 'gov',       11),
    ('ext_http_source', 12, 'pboc',      12),
    ('ext_http_source', 13, 'zhihu',     13),
    ('ext_http_source', 14, 'sina',      14),
    -- zhipu joins as an HTTP source for static.secret_config's
    -- web_search rows (the BigModel web_search endpoint) — the audit
    -- itself records zhipu traffic as ext_llm_provider.
    ('ext_http_source', 15, 'zhipu',     15),
    ('ext_http_source', 99, 'other',     99),
    -- ext_llm_provider: sub_type when resource_type = 1 — the provider
    -- name (RetryingJsonClient's owning BaseLlmProvider.name); 99 fallback
    ('ext_llm_provider',  0, 'zhipu',  0),
    -- dummy = the offline test double (test/dummy_langgraph at the
    -- project root — canned replies; the simulated endpoint lives in
    -- server_cluster.llm): the always-present llm secret_config row's
    -- provider label (secrets/01_seed_secret_config.sql), never an
    -- audited live provider.
    ('ext_llm_provider',  1, 'dummy',  1),
    -- local = the resident local-model host's REAL chat model
    -- (server_cluster.llm — the cluster's llm area, migrated from
    -- llm_agents.local_model_host 2026-11 — OpenAI-compatible
    -- /v1/chat/completions over the manager-loaded jev models, e.g.
    -- onejev-1.6-4b): the local secret_config row's provider label.
    -- Registry-only like dummy — the audit records the transport's own
    -- name (ZhipuChatProvider.name = zhipu).
    ('ext_llm_provider',  2, 'local',  2),
    ('ext_llm_provider', 99, 'other', 99),
    -- secret_type: static.secret_config.secret_type — the credential
    -- family a secret row serves. Its PROVIDER column's domain is
    -- selected by this one: ext_llm_provider when llm / jev /
    -- tokenization, ext_http_source when web_search (the audit's
    -- selected-domain pattern). jev = the local-model host's Jev /
    -- System One decision-model credentials (onejev-1.6-4b, the
    -- standard /v1/systemone contract); tokenization = its StructBERT
    -- word-segmenter credential (word-segmenter-zh,
    -- POST /v1/tokenization/segment) — both retyped out of llm
    -- 2026-11-11: they are the resident host's model-serving
    -- credentials, NOT chat-LLM ones (the deep agent's chat config
    -- reads ONLY llm rows).
    ('secret_type', 0, 'llm',  0),
    ('secret_type', 1, 'web_search', 1),
    ('secret_type', 2, 'jev', 2),
    ('secret_type', 3, 'tokenization', 3),
    -- secret_sub_type: static.secret_config.sub_type — the credential
    -- SERVICE TIER, one shared domain across both secret_types (0
    -- general = the standard tier — glm flash chat models, the
    -- offline dummy, generic content search; 1 pro = the paid-pro
    -- tier — the zhipu search_pro web-search engine)
    ('secret_sub_type', 0, 'general', 0),
    ('secret_sub_type', 1, 'pro', 1),
    -- db_instance: monitor.db_health.instance — the Postgres instance a
    -- health sample came from (docker-compose: db + db-replica)
    ('db_instance', 0, 'primary', 0),
    ('db_instance', 1, 'replica', 1),
    -- db_replication_state: monitor.db_health.replication_state — the WAL
    -- shipping state AS SEEN FROM THE SAMPLED INSTANCE (primary: the
    -- standby's pg_stat_replication.state; replica:
    -- pg_stat_wal_receiver.status). Code order is semantic: streaming is
    -- the only healthy state; 99 none = no standby attached (primary) /
    -- receiver absent (replica)
    ('db_replication_state',  0, 'streaming',  0),
    ('db_replication_state',  1, 'catchup',    1),
    ('db_replication_state',  2, 'startup',    2),
    ('db_replication_state',  3, 'backup',     3),
    ('db_replication_state',  4, 'stopping',   4),
    ('db_replication_state',  5, 'connecting', 5),
    ('db_replication_state',  6, 'stopped',    6),
    ('db_replication_state', 99, 'none',      99);

INSERT INTO static.label_dim (domain, code, label, sort_order) VALUES
    -- backup_mode: maintenance.src_data_backup.mode — the robocopy
    -- batch's scope (code order is semantic: incremental only exists
    -- after a full)
    ('backup_mode', 0, 'full',         0),
    ('backup_mode', 1, 'incremental',  1),
    -- backup_status: maintenance.src_data_backup.status — the batch
    -- outcome, from robocopy's exit code bits (0-3 ok, 4-7 mismatches
    -- present, 8+ copy failures / fatal). Code order is semantic:
    -- best -> worst
    ('backup_status', 0, 'ok',      0),
    ('backup_status', 1, 'partial', 1),
    ('backup_status', 2, 'failed',  2);
