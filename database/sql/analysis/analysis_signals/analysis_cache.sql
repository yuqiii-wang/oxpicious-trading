-- ----------------------------------------------------------------------------
--  Analysis CACHE — the generic intermediate-result cache of the signal
--  analyses (2026-10-27, with the ohlcv-boost derivation's first tenancy).
--
--  ONE self-describing cache JSONB per (kind, signal_type, sec_type):
--  `kind` names the owning analysis's intermediate result (the
--  static.label_dim 'analysis_cache_kind' domain — first tenant
--  boost_derivation = 0, the ohlcv-boost derivation population the
--  ohlcv-boost engine reads/writes), `signal_type` / `sec_type` scope
--  the payload to the analysis's own axes. A CACHE, never a source of
--  truth — every row regenerates by re-running its owning analysis:
--  the owner's auto-refresh folds in only the mutable-tail rows past
--  cache_date, with a full reload on miss only.
--
--  Owned by analysis/analysis_signals (the engines' fetch inputs);
--  NOT purged by the rules tier's per-run wipes.
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: The generic intermediate-result cache of the signal analyses — self-describing cache payloads per kind, signal type and security type, regenerable by re-running their owning analysis.
-- tags: cache, 缓存, intermediate results, 中间结果, boost derivation, 推导缓存

CREATE TABLE IF NOT EXISTS analysis_signals.analysis_cache (
    kind         SMALLINT      NOT NULL,  -- static.label_dim 'analysis_cache_kind' code
    signal_type  SMALLINT      NOT NULL,  -- static.label_dim 'signal_type' code
    sec_type     SMALLINT      NOT NULL,  -- static.label_dim 'sec_type' code
    cache        JSONB         NOT NULL,
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT now(),

    CONSTRAINT pk_analysis_cache PRIMARY KEY (kind, signal_type, sec_type)
);

COMMENT ON TABLE  analysis_signals.analysis_cache IS 'generic analysis intermediate-result cache: one self-describing cache jsonb per (kind, signal_type, sec_type). A CACHE, never a source of truth — every row regenerates by re-running its owning analysis. First tenant: kind 0 boost_derivation (the ohlcv-boost derivation population; the engine auto-refresh folds in only the mutable-tail rows past cache_date, full reload on miss only).';
COMMENT ON COLUMN analysis_signals.analysis_cache.kind        IS 'The owning analysis''s intermediate-result kind as its static.label_dim (''analysis_cache_kind'' domain) SMALLINT code: 0 = boost_derivation (the ohlcv-boost derivation population).';
COMMENT ON COLUMN analysis_signals.analysis_cache.signal_type IS 'Signal family as its static.label_dim (''signal_type'' domain) SMALLINT code — the cache payload''s signal axis.';
COMMENT ON COLUMN analysis_signals.analysis_cache.sec_type    IS 'Security type as its static.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf, 1 = index, 2 = stock, 3 = industry.';
COMMENT ON COLUMN analysis_signals.analysis_cache.cache       IS 'The self-describing cache payload (JSONB) — the owning analysis''s intermediate result, regenerable by re-running that analysis.';
