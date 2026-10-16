-- ============================================================================
--  DIVIDEND DELIVERIES — per-(sec_type, code, window_end_date) rolling
--  5-year dividend-delivery audit (2026-10-03; etf + stock scope, index
--  reserved).
--
--  The table audits a security's dividend DELIVERY track record: one row
--  per rolling 5-year window, strided by 1 year, aggregating the per-event
--  yield / EPS / stability metrics of every ex-dividend delivery inside
--  the window. The Dividends page renders it as the "Dividend Deliveries"
--  table under the baseline code-trend chart (where the dividend_est
--  estimation curve stays as the dashed overlay).
--
--  DEFINITIONS
--    delivery  = one ex-dividend event with a strictly positive per-share
--                cash dividend:
--                  etf   — stats.etf_adjustment.implied_dividend_per_share
--                  stock — stats.stock_dividends.dividend_per_share_pre_tax
--    per-event audited metrics:
--      yield    = D / close(ex-date)   (raw close from the basic-stats table)
--      eps      = eps(ex-date)         (stats.*_basic_stats.eps = close/pe)
--      stable   = 1 when the SAME code also delivered within +/- 20 days
--                 of the same date ONE YEAR earlier (365d +/- 20d repeat
--                 window; leap-year drift of +/- 1d sits inside the
--                 tolerance); the first-ever delivery of a series is not
--                 stable.
--
--  WINDOW GRID (rolling 5y, stride 1y)
--    Anchors are each calendar year's LAST data date per code (the current
--    year anchors at the code's latest close date — the open, mutable
--    window). window = [anchor - 5 calendar years + 1 day, anchor], so
--    consecutive anchors step exactly 1 year apart.
--    Rows exist only for PAYER codes (>= 1 delivery in history) and only
--    for anchors at/after the code's FIRST delivery date (the dividend
--    program's start). Zero-delivery windows within that span are stored
--    with count 0 and NULL aggregates (an audit must show the gap).
--
--  AGGREGATES (per window)
--    deliveries          count of delivery events
--    years_with_delivery distinct event calendar years (0..6 — a 5y window not
--    aligned to year boundaries touches up to 6 calendar years)
--    avg_yield           mean of per-event D/close (NULL when 0)
--    annualized_yield    Σ per-event D/close ÷ window span years (~5)
--    latest_year_yield   Σ per-event D/close over the final 365d of the
--                        window — trailing-1y yield at the anchor
--    avg_eps             mean of per-event eps (NULL when 0 or no EPS source)
--    avg_eps             mean of per-event eps (NULL when 0 or no EPS source)
--    stable_events       count of stable events
--    stability           stable_events / deliveries (NULL when 0)
--
--  MUTABILITY: every window ending at a PAST year is immutable once the
--  year closes; the current-year anchor and any late-arriving event are
--  mutable, and the table is tiny (~rows = payers x years), so the
--  pipeline rebuilds the sec_type's scope wholesale on every run that
--  processes the stage (the pe_pct_streaks precedent).
--
--  POPULATION
--    analysis.pe_and_dividends --metrics deliveries (Python module; etf +
--    stock stages — the index stage skips with a log line, an index has no
--    per-share deliveries). Per project rule, ALL INSERTs are in Python —
--    no raw INSERT...SELECT SQL in this file.
--
--  Register in analysis.analysis_identity (name='dividend_deliveries').
-- ============================================================================

-- ----------------------------------------------------------------------------
--  Table: analysis.dividend_deliveries  (rolling 5y / 1y-stride audit)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS analysis.dividend_deliveries (
    sec_type          SMALLINT         NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock
    code              TEXT         NOT NULL,
    window_end_date   DATE         NOT NULL,  -- anchor: the year's last data date
    window_start_date DATE         NOT NULL,  -- window_end_date - 5y + 1d

    deliveries          INT           NOT NULL,
    years_with_delivery INT           NOT NULL,
    avg_yield           DOUBLE PRECISION,       -- mean per-event D / close
    annualized_yield    DOUBLE PRECISION,       -- Σ D/close ÷ window years
    latest_year_yield   DOUBLE PRECISION,       -- Σ D/close, final 365d only
    avg_eps             DOUBLE PRECISION,       -- mean per-event eps (close/pe)
    stable_events       INT           NOT NULL,
    stability           DOUBLE PRECISION,       -- stable_events / deliveries

    CONSTRAINT pk_dividend_deliveries PRIMARY KEY (code, sec_type, window_end_date),
    CONSTRAINT chk_dividend_deliveries_sec_type
        CHECK (sec_type IN (0, 1, 2)),  -- label codes: 0=etf 1=index 2=stock
    CONSTRAINT chk_dividend_deliveries_counts
        CHECK (deliveries >= 0 AND stable_events >= 0
               AND stable_events <= deliveries
               AND years_with_delivery BETWEEN 0 AND 6
               AND window_start_date <= window_end_date)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis', 'dividend_deliveries', 16);

CREATE INDEX IF NOT EXISTS idx_dividend_deliveries_sec_type_window
    ON analysis.dividend_deliveries (sec_type, window_end_date);

COMMENT ON TABLE  analysis.dividend_deliveries IS 'Rolling 5-year (1-year stride) dividend DELIVERY audit per (code, sec_type): one row per annual window anchor (each year''s last data date; current year anchors at the latest close — the open window), aggregating the delivery events'' yield (D/close at the ex-date), eps (basic-stats eps at the ex-date) and stability (the last-year ±20d repeat rule: an event is stable when the same code also delivered within 20 days of the same date one year earlier). Rows only for payer codes and only from the first delivery onward; zero-delivery windows stored with 0 counts and NULL aggregates. Rebuilt wholesale per sec_type on every run that processes the stage (tiny table; past windows immutable, current-year anchor mutable). Built by analysis.pe_and_dividends --metrics deliveries (etf + stock; index skips); all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.dividend_deliveries.sec_type          IS 'Subject security type as its static.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf, 2 = stock (index reserved — an index has no per-share deliveries).';
COMMENT ON COLUMN analysis.dividend_deliveries.code              IS 'Security code (ETF bare e.g. 510880; stock suffixed e.g. 600000.SS — same convention as analysis.pe / analysis.dividends).';
COMMENT ON COLUMN analysis.dividend_deliveries.window_end_date   IS 'Window anchor: the calendar year''s LAST data date of the code (current year = latest close date; the open, mutable window). Grid stride is exactly 1 year.';
COMMENT ON COLUMN analysis.dividend_deliveries.window_start_date IS 'window_end_date - 5 calendar years + 1 day (Feb-29 anchors clamp to Feb-28 five years back).';
COMMENT ON COLUMN analysis.dividend_deliveries.deliveries        IS 'Delivery events (ex-date with D > 0) inside the window.';
COMMENT ON COLUMN analysis.dividend_deliveries.years_with_delivery IS 'Distinct event calendar years inside the window (0..6: a 5y window not aligned to year boundaries touches up to 6 calendar years).';
COMMENT ON COLUMN analysis.dividend_deliveries.avg_yield          IS 'Mean of the per-event yields D / close(ex-date) (raw close); NULL when the window has no delivery.';
COMMENT ON COLUMN analysis.dividend_deliveries.annualized_yield    IS 'Sum of the per-event yields D / close(ex-date) divided by the window span in years (every window spans anchor-5y+1d..anchor, so ~5.003) — the window''s average ANNUAL cash yield on per-event prices; NULL when the window has no delivery.';
COMMENT ON COLUMN analysis.dividend_deliveries.latest_year_yield   IS 'Sum of the per-event yields D / close(ex-date) over the FINAL 365 days of the window — the trailing-1-year cash yield as of the anchor date (equals the live trailing-12m yield for the current-year anchor); 0 when the window has deliveries but none in the last year, NULL when the window has no delivery at all.';
COMMENT ON COLUMN analysis.dividend_deliveries.avg_eps            IS 'Mean of the per-event eps (stats.*_basic_stats.eps = close/pe at the ex-date); NULL when the window has no delivery or no EPS source (pe NULL / loss-making).';
COMMENT ON COLUMN analysis.dividend_deliveries.stable_events     IS 'Delivery events with a same-code delivery within +/- 20 days of the same date one year earlier (last-year ±20d repeat rule).';
COMMENT ON COLUMN analysis.dividend_deliveries.stability         IS 'stable_events / deliveries — the share of deliveries that repeated year-over-year within 20 days of the same date; NULL when the window has no delivery.';

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('dividend_deliveries', 'dividend_deliveries', NULL, NOW(),
     'Rolling 5-year (1-year stride) dividend delivery audit per (code, sec_type; etf + stock): per-event yield (D/close at the ex-date), eps (close/pe) and the stability flag (delivered within 20 days of the same date one year earlier), aggregated over each annual window anchor (the year''s last data date). Zero-delivery windows stored with 0 counts / NULL aggregates; payer codes only, from the first delivery onward. Rebuilt wholesale per sec_type on every run (tiny table). Built by analysis.pe_and_dividends --metrics deliveries; all INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
