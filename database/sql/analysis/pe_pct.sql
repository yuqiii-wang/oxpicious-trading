
-- ----------------------------------------------------------------------------
--  Table: analysis.pe_pct  (pe percentile band, monthly)
--    PK: (sec_type, code, date_year_month, period, pct_type)
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each security's monthly PE percentile band over trailing one-to-five-year windows — the cheap/expensive envelope of its own history.
-- tags: pe, 市盈率, percentile band, 分位区间, valuation, 估值

CREATE TABLE IF NOT EXISTS analysis.pe_pct (
    sec_type                        SMALLINT          NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock
    code                            TEXT          NOT NULL,
    date_year_month                 DATE          NOT NULL,  -- band month FIRST day
    period                          INTEGER       NOT NULL,  -- 252, 504, 756, 1260
    pct_type                        INTEGER       NOT NULL,  -- 1 | 5 | 10 (percent)

    high_val                        NUMERIC(12,6) NOT NULL,  -- (100 - pct_type)-th pct of the window
    low_val                         NUMERIC(12,6) NOT NULL,  -- pct_type-th pct of the window

    CONSTRAINT pk_pe_pct PRIMARY KEY (sec_type, code, date_year_month, period, pct_type)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed on code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('analysis', 'pe_pct', 8);

CREATE INDEX IF NOT EXISTS idx_pe_pct_code
    ON analysis.pe_pct (sec_type, code, date_year_month);

COMMENT ON TABLE  analysis.pe_pct               IS 'Per-(sec_type, code, month, period, pct_type) percentile BAND of the pe series (2026-09 split of the former combined analysis.pe_and_dividend_pct — the metric column is gone; the table name carries the metric). low_val = pct_type-th percentile and high_val = (100 - pct_type)-th percentile (linear interpolation) of the pe series'' non-NULL values over the TRAILING window of `period` observations (252/504/756/1260 = ~1/2/3/5 trading years) ending at the month''s LAST observation row; both legs use the SAME series. One band row per calendar month, anchored at the month''s last observation row and stored under the month''s first day; the in-progress month is skipped (no true month-end anchor yet). Windows near a code''s history start are naturally truncated; fewer than 252 non-NULL pe observations yields no band. Trailing windows make completed months immutable — only missing (code, month) triples are computed incrementally (a triple is complete when all 12 period x pct_type rows exist); --force / single-code rebuilds the scope. Internal step of analysis.pe_and_dividends (pct_bands.py).';
COMMENT ON COLUMN analysis.pe_pct.sec_type      IS 'Security type as its static.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf, 1 = index, 2 = stock, 3 = industry (composite industry, code = industry_id).';
COMMENT ON COLUMN analysis.pe_pct.code          IS 'Security code (bare index code e.g. 000300; ETF bare e.g. 159001; stock ticker with exchange suffix e.g. 600008.SS).';
COMMENT ON COLUMN analysis.pe_pct.date_year_month IS 'Calendar month of the band, stored as the month''s FIRST day (e.g. 2026-09-01) — the percentile window ENDS (inclusive) at the month''s last non-NULL observation row of the pe series. Part of the PK.';
COMMENT ON COLUMN analysis.pe_pct.period        IS 'Lookback window length in pe observations: 252 / 504 / 756 / 1260 (~1 / 2 / 3 / 5 trading years). Part of the PK.';
COMMENT ON COLUMN analysis.pe_pct.pct_type      IS 'Band tightness in percent: 1 / 5 / 10. pct_type 1 = near-full range of the window ([1st, 99th] percentile); 10 = core envelope ([10th, 90th]). Part of the PK.';
COMMENT ON COLUMN analysis.pe_pct.high_val      IS '(100 - pct_type)-th percentile of the window''s pe values (band top; a day whose value rises ABOVE it is a high-side break).';
COMMENT ON COLUMN analysis.pe_pct.low_val       IS 'pct_type-th percentile of the window''s pe values (band bottom; a day whose value falls BELOW it is a low-side break).';
