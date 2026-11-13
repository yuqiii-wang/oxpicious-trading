-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Contiguous same-regime day spans per security — the bands charts shade regimes with.
-- tags: market, 市场, regime, 市场状态

DROP TABLE IF EXISTS stats.market_regime_spans CASCADE;

-- ----------------------------------------------------------------------------
--  Derived TABLE: contiguous same-regime spans (the UI shading source —
--  replaces the retired mov_ave_market_hypes EPISODE rows). One row per
--  maximal run of consecutive trading dates holding the SAME regime:
--  (start_date, end_date) inclusive + span_days (trading-date count).
--  MATERIALIZED by builds.market_regimes — the same run computes the
--  spans from the daily states it just wrote and replaces the scope's
--  rows wholesale alongside the registry (--force truncates both).
--  Formerly a per-query VIEW whose gaps-and-islands recomputed on every
--  /api/analysis/market-regimes call; the table turns that endpoint
--  into a pure row read.
-- ----------------------------------------------------------------------------
CREATE TABLE stats.market_regime_spans (
    sec_type   SMALLINT NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock 3=industry
    code       TEXT    NOT NULL,
    regime     SMALLINT NOT NULL,  -- label code (static.label_dim 'regime'): 0=calm 1=hot 2=panic 3=quiet
    start_date DATE    NOT NULL,  -- inclusive
    end_date   DATE    NOT NULL,  -- inclusive
    span_days  INTEGER NOT NULL,  -- trading-date count in [start_date, end_date]

    CONSTRAINT pk_market_regime_spans
        PRIMARY KEY (code, sec_type, regime, start_date),
    CONSTRAINT ck_market_regime_spans_regime
        CHECK (regime BETWEEN 0 AND 3),
    CONSTRAINT ck_market_regime_spans_dates
        CHECK (start_date <= end_date)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed on code — same convention as the
-- daily registry (the per-code endpoint read prunes to one partition).
SELECT public.create_hash_partitions('stats', 'market_regime_spans', 8);

COMMENT ON TABLE stats.market_regime_spans IS 'One row per CONTIGUOUS run of same-regime days over stats.market_regimes: (start_date, end_date) inclusive span + span_days (trading-date count). sec_type / regime carry their static.label_dim SMALLINT codes (sec_type: 0 etf / 1 index / 2 stock; regime: 0 calm / 1 hot / 2 panic / 3 quiet). The UI regime-shading source replacing the retired mov_ave_market_hypes episode rows — hot spans shade as the old "hyped" bands did; panic/quiet get their own colors. MATERIALIZED by builds.market_regimes (wholesale-rebuilt per scope alongside the registry; formerly a per-query view).';
COMMENT ON COLUMN stats.market_regime_spans.sec_type IS 'Security type as its static.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf, 1 = index, 2 = stock, 3 = industry (reserved). Part of the PK; decode to strings only at the API boundary.';

UPDATE stats.market_regime_spans
   SET code = split_part(code, '.', 1)
 WHERE sec_type = 0 AND code ~ '^\d{6}\.';
