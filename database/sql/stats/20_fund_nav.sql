-- ============================================================================
--  Fund NAV — OTC fund (ETF联接基金 feeder fund) net-asset-value history.
--
--  Feeder codes (e.g. 012761) are off-exchange funds: NO market OHLCV
--  exists, so their trend series is the fund's published NAV (单位净值 /
--  累计净值 / 日涨跌%). One row per (code, nav date) — the fund-side
--  mirror of stats.etf_identity's market-side OHLCV for the target ETF.
--
--  Source: downloads.fund.eastmoney.nav — eastmoney pingzhongdata JS
--  (aggregated fund-company NAV publications), full inception-to-date
--  series per request, for the feeder universe defined by
--  stats.sec_etf_links / etf_feeder_map_*.csv.
--  Loaded by: builds.fund (incremental: per-code max-date append;
--  --force = truncate + reload).
--
--  Consumed by: /api/sec-baseline?sec_type=fund — the "fund" baseline
--  bundle (NAV close-line) rendered by the shared CodeTrendChart for the
--  ETF page's Linked Feeder ETFs row-click expansion.
-- ============================================================================

CREATE TABLE IF NOT EXISTS stats.fund_nav (
    code        TEXT          NOT NULL,
    date        DATE          NOT NULL,
    nav         NUMERIC(12,6) NOT NULL,
    cum_nav     NUMERIC(12,6),
    day_pct     NUMERIC(10,4),

    CONSTRAINT pk_fund_nav PRIMARY KEY (code, date)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed on code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'fund_nav', 8);

COMMENT ON TABLE  stats.fund_nav        IS 'OTC fund (ETF联接基金 feeder) NAV history — one row per (code, nav date). Fund-side trend series for feeder codes that have no market OHLCV; market-side counterpart of the target ETF lives in stats.etf_identity. Source: downloads.fund.eastmoney.nav (eastmoney pingzhongdata); loaded by builds.fund (per-code max-date incremental).';
COMMENT ON COLUMN stats.fund_nav.code   IS 'OTC fund code, bare 6-digit (e.g. 012761 — the feeder family code listed in stats.sec_etf_links.feeder_code).';
COMMENT ON COLUMN stats.fund_nav.date   IS 'NAV publication date (估值日期).';
COMMENT ON COLUMN stats.fund_nav.nav    IS '单位净值 (unit NAV) — the baseline close series.';
COMMENT ON COLUMN stats.fund_nav.cum_nav IS '累计净值 (cumulative NAV, dividends reinvested).';
COMMENT ON COLUMN stats.fund_nav.day_pct IS '日涨跌 in PERCENT (equityReturn) — same convention as exchange pct_change.';
