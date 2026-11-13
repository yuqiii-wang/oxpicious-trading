
-- ----------------------------------------------------------------------------
--  Table: analysis.pe_codes  (per-code summary of pe ∪ dividends — ONE row
--  per (sec_type, code))
--
--  Precomputed source for the /api/analysis/pe/codes endpoint. The live
--  query it replaced aggregated analysis.pe ∪ analysis.dividends on every
--  request (UNION + sort + GROUP BY over 1.3M rows for index / 7.1M for
--  stock, plus DISTINCT-ON latest-row and identity-name lookups): 4.7s on
--  the index universe and 35s on the stock universe, gating the PE /
--  Dividends pages' first open (2026-09-29). This table moves that work
--  into the analysis.pe_and_dividends pipeline, where it runs offline once
--  per write pass; the endpoint becomes a small indexed table read
--  (~10ms). Same pattern as analysis.mov_ave_spreads_codes.
--
--  Maintained by analysis/pe_and_dividends/_common/codes_summary.py
--  (refresh_pe_codes_summary): DELETE + INSERT per affected sec_type (or
--  per --code in single-code runs) after every detail write pass.
--  Backfill = the same INSERT, run manually once per sec_type after
--  creating the table.
--
--  Column semantics mirror the former request-time query exactly:
--    first_date / last_date / n_dates — date coverage over the UNION of
--                                       the code's analysis.pe and
--                                       analysis.dividends rows
--    latest_pe / latest_dividend_yield — the metric value at the code's
--                                       last row in each metric table
--    name — latest identity name (from stats.{etf,index,stock}_identity)
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Per-security summary of the PE and dividend-yield studies — date coverage, latest readings and names.
-- tags: pe, 市盈率, dividend yield, 股息率, summary, 汇总, reference, 参考

CREATE TABLE IF NOT EXISTS analysis.pe_codes (
    sec_type              SMALLINT         NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock
    code                  TEXT         NOT NULL,  -- matches pe/dividends.code (ETF bare; stock suffixed)
    name                  TEXT         NOT NULL DEFAULT '',
    first_date            DATE         NOT NULL,
    last_date             DATE         NOT NULL,
    n_dates               BIGINT       NOT NULL,

    latest_pe             NUMERIC(10,4),
    latest_dividend_yield NUMERIC(10,6),

    CONSTRAINT pk_pe_codes PRIMARY KEY (sec_type, code)
);

COMMENT ON TABLE  analysis.pe_codes IS 'Per-code summary of analysis.pe ∪ analysis.dividends (ONE row per sec_type+code): date coverage over the union, latest pe + dividend_yield snapshot, latest identity name. Precomputed source for the /api/analysis/pe/codes endpoint (the former live aggregate took 4.7s index / 35s stock per request). Maintained by analysis.pe_and_dividends after every detail write pass; the mov_ave_spreads_codes pattern.';
COMMENT ON COLUMN analysis.pe_codes.name                  IS 'Latest identity name (the sec_type''s identity-table row at the greatest date; sec_type=3 reads stats.industry_identity). Empty string when the identity table has no row for the code.';
COMMENT ON COLUMN analysis.pe_codes.n_dates               IS 'COUNT(DISTINCT date) over the UNION of the code''s analysis.pe and analysis.dividends rows (a date present in both counts once).';
COMMENT ON COLUMN analysis.pe_codes.latest_pe             IS 'pe at the code''s LAST analysis.pe row (NULL when the code has no pe rows).';
COMMENT ON COLUMN analysis.pe_codes.latest_dividend_yield IS 'dividend_yield at the code''s LAST analysis.dividends row (NULL when the code has no dividends rows).';

-- ----------------------------------------------------------------------------
--  Maintenance statement (the pipeline's refresh_pe_codes_summary runs this
--  per affected sec_type, with an optional code equality filter for
--  single-code runs; {identity} = stats.{etf,index,stock}_identity):
--
--  DELETE FROM analysis.pe_codes WHERE sec_type = $1 [AND code = $2];
--
--  INSERT INTO analysis.pe_codes (
--      sec_type, code, name, first_date, last_date, n_dates,
--      latest_pe, latest_dividend_yield)
--  WITH u AS (
--      SELECT code, date FROM analysis.pe WHERE sec_type = $1 [AND code = $2]
--      UNION
--      SELECT code, date FROM analysis.dividends WHERE sec_type = $1 [AND code = $2]
--  ), agg AS (
--      SELECT code, MIN(date) AS first_date, MAX(date) AS last_date,
--             COUNT(*) AS n_dates FROM u GROUP BY code
--  ), latest_pe AS (
--      SELECT DISTINCT ON (code) code, pe FROM analysis.pe
--       WHERE sec_type = $1 [AND code = $2] ORDER BY code, date DESC
--  ), latest_dy AS (
--      SELECT DISTINCT ON (code) code, dividend_yield FROM analysis.dividends
--       WHERE sec_type = $1 [AND code = $2] ORDER BY code, date DESC
--  )
--  SELECT $1, a.code, COALESCE(n.name, ''),
--         a.first_date, a.last_date, a.n_dates, lp.pe, ld.dividend_yield
--    FROM agg a
--    LEFT JOIN LATERAL (
--        SELECT x.name FROM {identity} x
--         WHERE x.code = a.code ORDER BY x.date DESC LIMIT 1 OFFSET 0
--    ) n ON TRUE
--    LEFT JOIN latest_pe lp ON lp.code = a.code
--    LEFT JOIN latest_dy ld ON ld.code = a.code;
-- ----------------------------------------------------------------------------

-- ----------------------------------------------------------------------------
--  Register the PE split steps in analysis.analysis_identity (the legacy
--  combined 'pe_and_dividend_pct' / 'pe_and_dividend_pct_streaks' entries
--  and the removed 'dividends_pct' / 'dividends_pct_streaks' entries are
--  removed)
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('pe_pct', 'pe_pct', NULL, NOW(),
     'Per-(sec_type, code, month, period, pct_type) percentile BAND of the pe series from analysis.pe (the mov_ave_high_low_pct pattern applied to the valuation metric). low_val = pct_type-th and high_val = (100 - pct_type)-th percentile (linear interpolation) of the pe series'' non-NULL values over the TRAILING window of `period` observations (252/504/756/1260 = ~1/2/3/5 trading years) ending at the month''s last observation row; both legs use the same series. One band per calendar month stored under the month''s first day; the in-progress month is skipped; fewer than 252 non-NULL observations yields no band. Trailing windows make completed months immutable — only missing (code, month) triples are computed incrementally; --force / single-code rebuilds the scope. Internal step of analysis.pe_and_dividends (pct_bands.py).')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;

INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('pe_pct_streaks', 'pe_pct_streaks', NULL, NOW(),
     'Band-BREAK excursion streaks of the pe series audited against analysis.pe_pct (the mov_ave_high_low_pct_streaks pattern applied to the valuation metric): a day is OUT-OF-BAND when its pe value is ABOVE its own month-band high_val or BELOW low_val; an excursion streak is the maximal consolidation of same-side out-of-band trading days with in-band re-entries of up to 5 consecutive days bridged (longer gap or side switch ends it). start/end_value = pe on the first/last out-of-band day; max/min_value span the whole streak; day_count counts span trading rows. Non-trading and NULL-pe rows are excluded. Episodes shift with new data — rebuilt WHOLESALE per sec_type (per code in single-code mode) on every run that processes the scope. Side is derived at query time from the END month''s band. Internal step of analysis.pe_and_dividends (pct_streaks.py).')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
