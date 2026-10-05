-- ============================================================================
--  Dividend EST — per-(sec_type, code, date) dividend price
--  ESTIMATION curve (2026-10-03; ETF scope only for now).
--
--  The table stores the "dividend-based price estimation curve": a smooth
--  cum-dividend fair-value line built from the security's own ex-dividend
--  events, replacing the PE curve on the UI's Dividends page (ETF panel).
--
--  MODEL (per code; segments delimited by the code's ex-dividend dates
--  e_1 < ... < e_n from stats.etf_adjustment WHERE
--  implied_dividend_per_share > 0):
--
--    Completed segment i (t in [e_i, e_{i+1})):
--      est(t) = close(e_i) * exp( r_i * days(t - e_i) )
--      r_i    = ln( (close(e_{i+1}) + D_{i+1}) / close(e_i) )
--               / days(e_{i+1} - e_i - 1 calendar day)
--    i.e. the curve anchors on the POST-DROP close of the ex-date (the
--    exchange's ex-dividend reference convention: record-date close minus
--    the dividend), then grows exponentially so that ON THE RECORD DATE
--    (the last calendar day before the next ex-date) it equals the next
--    cum-dividend level close(e_{i+1}) + D_{i+1} (full dividend value on
--    the record date, minus the dividend the next trading day — the
--    user-specified sawtooth pricing scheme).
--
--    Open TAIL segment (t >= e_n, no next ex-date yet):
--      est(t) = close(e_n) * exp( r_tail * days(t - e_n) )
--      r_tail = ln( 1 + D_n / close(e_n) ) / freq_days
--      freq_days = median of the last (up to) 3 inter-ex-date calendar-day
--      gaps (the code's dividend frequency; 365 when only one event
--      exists) — over one dividend period the tail accrues exactly the
--    last dividend yield (the "exponential growth by dividend frequency,
--    next dividend yield as end value" terminal condition).
--
--  Tail rows are MUTABLE: when the next ex-date lands, the whole former
--  tail segment is recomputed with realized anchors, so the pipeline
--  re-upserts every row from the STORED tail's seg_start_date onward on
--  each run (plus missing dates). Completed segments are immutable
--  (historical closes + dividends are facts).
--
--  Rows exist only from each code's FIRST ex-dividend date onward (no
--  dividend model before it) and only on trading rows with a close.
--  Non-payer codes have NO rows.
--
--  POPULATION
--    analyze.pe_and_dividends --metrics tech (Python module; ETF stage
--    only for now — the index/stock stages skip with a log line until
--    explicitly extended). Per project rule, ALL INSERTs are in Python —
--    no raw INSERT...SELECT SQL in this file.
--
--  Register in analysis.analysis_identity (name='dividend_est').
-- ============================================================================

-- ----------------------------------------------------------------------------
--  Table: analysis.dividend_est  (daily dividend est curve)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS analysis.dividend_est (
    sec_type        TEXT         NOT NULL,  -- 'etf' (index/stock reserved)
    code            TEXT         NOT NULL,
    date            DATE         NOT NULL,

    -- Estimation curve value in RAW price units (CNY per share / index
    -- points), anchored on raw closes. NOT the adjusted display price —
    -- the API converts to adjusted space via the row's adj_close/close
    -- factor when overlaying the adjusted OHLC chart.
    div_est_price   NUMERIC(18,6) NOT NULL,

    -- Current segment diagnostics.
    seg_start_date  DATE         NOT NULL,  -- anchor ex-date of the segment
    seg_end_date    DATE,                   -- next ex-date; NULL = open tail
    seg_next_div    NUMERIC(18,6),          -- D at the next ex-date; NULL on tail
    seg_rate        DOUBLE PRECISION NOT NULL, -- daily continuous rate r_i / r_tail

    CONSTRAINT pk_dividend_est PRIMARY KEY (code, sec_type, date),
    CONSTRAINT chk_dividend_est_sec_type
        CHECK (sec_type IN ('stock', 'etf', 'index'))
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis', 'dividend_est', 16);

CREATE INDEX IF NOT EXISTS idx_dividend_est_date
    ON analysis.dividend_est (date);
CREATE INDEX IF NOT EXISTS idx_dividend_est_sec_type_date
    ON analysis.dividend_est (sec_type, date);

COMMENT ON TABLE  analysis.dividend_est IS 'Per-(code, sec_type, date) dividend price ESTIMATION curve (2026-10-03, ETF scope only for now): a smooth cum-dividend fair-value line anchored on the post-drop close of each ex-dividend date, growing exponentially to the next cum-dividend level (close(next ex) + next D) by the next ex-date — the ex-dividend sawtooth pricing scheme (record-date full value, next-day minus dividend, exponential regrowth). Open tail after the last ex-date grows at ln(1 + D_last/close(anchor))/freq_days (freq = median of the last up-to-3 inter-ex-date gaps, 365 when only one event). div_est_price is in RAW price units; the API converts to adjusted space for the chart overlay. Rows only from the code''s first ex-date onward; non-payers have no rows. Tail rows are mutable (recomputed when the next ex-date lands) — the pipeline re-upserts from the stored tail''s seg_start_date onward plus missing dates. Built by analyze.pe_and_dividends --metrics tech; all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.dividend_est.sec_type       IS 'Subject security type: etf (index/stock reserved for later extension).';
COMMENT ON COLUMN analysis.dividend_est.code           IS 'Security code (bare ETF code e.g. 510880; same convention as analysis.pe / analysis.dividends).';
COMMENT ON COLUMN analysis.dividend_est.date           IS 'Trading date.';
COMMENT ON COLUMN analysis.dividend_est.div_est_price  IS 'Dividend price estimation curve value in RAW price units: completed segment = close(ex_i) * exp(r_i * days since ex_i) with r_i = ln((close(ex_{i+1}) + D_{i+1}) / close(ex_i)) / days(ex_{i+1} - ex_i - 1 calendar day) — the curve reaches the full cum-dividend level exactly on the record date; open tail = close(ex_last) * exp(r_tail * days since ex_last) with r_tail = ln(1 + D_last / close(ex_last)) / freq_days.';
COMMENT ON COLUMN analysis.dividend_est.seg_start_date IS 'Anchor ex-dividend date of the row''s segment (the curve resets to that day''s raw post-drop close).';
COMMENT ON COLUMN analysis.dividend_est.seg_end_date   IS 'Next ex-dividend date bounding the segment; NULL on the open tail (the mutable segment re-upserted on every run).';
COMMENT ON COLUMN analysis.dividend_est.seg_next_div   IS 'Per-share cash dividend going ex on seg_end_date (implied_dividend_per_share); NULL on the open tail.';
COMMENT ON COLUMN analysis.dividend_est.seg_rate       IS 'Daily continuous growth rate of the segment: r_i = ln((close(ex_{i+1}) + D_{i+1})/close(ex_i))/days(ex_{i+1} - ex_i - 1 calendar day) for completed segments; r_tail = ln(1 + D_last/close(ex_last))/freq_days for the open tail.';

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------
-- The 2026-10-03 rename dividend_tech_stats -> dividend_est also renamed the
-- identity row (PK = name), so the legacy name must go. On a pre-rename DB
-- the TABLE rename itself (parent + _p00.._p15 partitions + constraints +
-- indexes) is applied by temp_scripts/_apply_dividend_est_rename.py — this
-- file's CREATE TABLE IF NOT EXISTS only covers fresh installs.
DELETE FROM analysis.analysis_identity WHERE name = 'dividend_tech';

INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('dividend_est', 'dividend_est', NULL, NOW(),
     'Per-(sec_type, code, date) dividend price estimation curve (ETF scope only for now): cum-dividend fair-value line anchored on each ex-date''s post-drop close, exponential growth to the next cum-dividend level (close(next ex) + next D) by the next ex-date; open tail grows at ln(1 + D_last/close)/freq (freq = median of the last up-to-3 inter-ex-date gaps, 365 fallback). Tail rows are mutable — re-upserted from the stored tail''s seg_start_date onward plus missing dates. Built by analyze.pe_and_dividends --metrics tech; all INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
