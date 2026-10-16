-- ============================================================================
--  static.label_dim seed — MONITORING / INFRASTRUCTURE vocabularies
--
--  The operational monitors' closed vocabularies: the external request
--  audit's resource family, request host and LLM provider, and the DB
--  health sampler's instance identity + WAL shipping state. Owning
--  tables: monitor.external_resource_audit (ext_resource /
--  ext_http_source / ext_llm_provider — sub_type's domain is selected
--  by resource_type), monitor.db_health (db_instance /
--  db_replication_state).
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
    ('ext_http_source', 99, 'other',     99),
    -- ext_llm_provider: sub_type when resource_type = 1 — the provider
    -- name (RetryingJsonClient's owning BaseLlmProvider.name); 99 fallback
    ('ext_llm_provider',  0, 'zhipu',  0),
    ('ext_llm_provider', 99, 'other', 99),
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
