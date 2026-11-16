-- ============================================================================
--  Market Crowdedness hot sets — the frozen annual hot-stock rosters
--  behind analysis.market_crowdedness_year.
--
--    Table: analysis.market_crowdedness_hot_sets
--    PK: (code, sec_type, hot_year, stock_code)
--
--  One row per pinned member: which names defined a year's hot basket
--  for a pool, in year-rank order. Doubles as:
--    — the /api/analysis/market-crowdedness/hot-years dropdown source
--      (DISTINCT hot_year per code);
--    — the frozen-set read-back for incremental pipeline runs (a
--      closed year's roster never changes, so appending new dates to
--      its variant reloads the roster from here instead of re-deriving
--      it from year data outside the load window);
--    — evidence (stock_name included for direct display).
--
--  POPULATION: analysis.market_crowdedness (Python module, same pass
--  as the year variants). The OPEN year's rows are re-derived and
--  replaced nightly until the year closes; closed rosters are frozen.
--  Per project rule, ALL INSERTs are in Python — no raw INSERT...SELECT
--  SQL in this file.
--
--  SET RULE (mirrors market_crowdedness_year.sql):
--    |Y(y)| = max(ceil(0.1 · mean active_n over the year's trading
--    days), 5); members ranked by MEAN 60-active-day amount
--    (amt_ma60) over the year's own trading days; hot_rank 1..|Y(y)|.
--    sec_type ∈ ('index' | 'industry') — same scope as the rolling
--    table (label_dim 'sec_type' domain 0=etf 1=index 2=stock
--    3=industry; the CHECK keeps the full domain).
--
--  ============================================================================
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: The frozen annual hot-stock rosters behind the year-pinned crowdedness variants — which names defined each year's hottest basket per index or industry.
-- tags: hot stocks, 热门股, hot set, 热门名单, roster, 名单, annual, 年度, crowdedness, 拥挤度


CREATE TABLE IF NOT EXISTS analysis.market_crowdedness_hot_sets (
    sec_type     SMALLINT  NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock 3=industry
    code         TEXT      NOT NULL,
    hot_year     INT       NOT NULL,  -- the calendar year (2025 = the "2025" roster)
    hot_rank     INT       NOT NULL,  -- 1-based rank within the year set (year-average amt_ma60 desc)
    stock_code   TEXT      NOT NULL,  -- suffixed member stock code (e.g. 600519.SS), as carried by stats.stock_liquidity_margin
    stock_name   TEXT      NOT NULL,  -- display name at build time (evidence; names may drift later)

    CONSTRAINT pk_market_crowdedness_hot_sets PRIMARY KEY (code, sec_type, hot_year, stock_code),
    CONSTRAINT chk_market_crowdedness_hot_sets_sec_type
        CHECK (sec_type IN (0, 1, 2, 3)),  -- label codes: 0=etf 1=index 2=stock 3=industry
    CONSTRAINT chk_market_crowdedness_hot_sets_rank
        CHECK (hot_rank >= 1)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis', 'market_crowdedness_hot_sets', 8);

CREATE INDEX IF NOT EXISTS idx_market_crowdedness_hot_sets_sty
    ON analysis.market_crowdedness_hot_sets (sec_type, hot_year);

COMMENT ON TABLE  analysis.market_crowdedness_hot_sets IS 'The frozen annual hot-stock rosters behind analysis.market_crowdedness_year (2026-11-14): one row per pinned member — the year-average top max(ceil(0.1·mean active_n), 5) members by mean 60-active-day trading amount over the year''s own trading days, per index/industry pool. Open-year rows are re-derived nightly and freeze on year close. Serves the /api/analysis/market-crowdedness/hot-years dropdown and the incremental pipeline''s frozen-set read-back. Built by analysis.market_crowdedness; all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.market_crowdedness_hot_sets.sec_type   IS 'Security type as its static.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf, 1 = index, 2 = stock, 3 = industry. Scoped to 1 (index) and 3 (industry), same as the crowdedness tables.';
COMMENT ON COLUMN analysis.market_crowdedness_hot_sets.code       IS 'Pool code (bare index code e.g. 000001; sec_type=3: industry_id e.g. BANKS).';
COMMENT ON COLUMN analysis.market_crowdedness_hot_sets.hot_year   IS 'The calendar year (2025 = the "2025" roster) — joins analysis.market_crowdedness_year.hot_year and the UI dropdown value.';
COMMENT ON COLUMN analysis.market_crowdedness_hot_sets.hot_rank   IS '1-based rank within the year set: year-average amt_ma60 descending. The pinned top_stocks evidence lists members in this order.';
COMMENT ON COLUMN analysis.market_crowdedness_hot_sets.stock_code IS 'Member stock code with exchange suffix (e.g. 600519.SS), the code space of stats.stock_liquidity_margin.';
COMMENT ON COLUMN analysis.market_crowdedness_hot_sets.stock_name IS 'Display name at build time (latest stats.stock_identity name) — evidence for direct display; the authoritative name lookup remains stats.stock_identity.';
