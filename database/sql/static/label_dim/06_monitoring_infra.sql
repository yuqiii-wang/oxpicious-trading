-- ============================================================================
--  static.label_dim seed — MONITORING / INFRASTRUCTURE vocabularies
--
--  The operational monitors' closed vocabularies: the external request
--  audit's resource family, request host and LLM provider, the DB
--  health sampler's instance identity + WAL shipping state, and the
--  secret registry's credential type. Owning
--  tables: monitor.external_resource_audit (ext_resource /
--  ext_http_source / ext_llm_provider — sub_type's domain is selected
--  by resource_type), monitor.db_health (db_instance /
--  db_replication_state), static.secret_config (secret_type — the
--  provider domain is selected by it: ext_llm_provider when llm,
--  ext_http_source when web_search).
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
    -- dummy = the offline test double (llm_agents.test.dummy_llm —
    -- canned replies / the simulated endpoint): the always-present
    -- llm secret_config row's provider label (secrets/
    -- 01_seed_secret_config.sql), never an audited live provider.
    ('ext_llm_provider',  1, 'dummy',  1),
    ('ext_llm_provider', 99, 'other', 99),
    -- secret_type: static.secret_config.secret_type — the credential
    -- family a secret row serves. Its PROVIDER column's domain is
    -- selected by this one: ext_llm_provider when llm,
    -- ext_http_source when web_search (the audit's selected-domain
    -- pattern).
    ('secret_type', 0, 'llm',  0),
    ('secret_type', 1, 'web_search', 1),
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
