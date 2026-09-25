-- ============================================================================
--  LIVE options intraday OI store + computed intraday skewness series.
--
--  Two tables behind the Live UI "Options" tab (intraday OI-weighted
--  moneyness skewness vs the underlying's 5-min spot):
--
--  live.options_intraday_oi (RAW STORE — written by the SSE options
--  streamer, downloads/stream/sse/price/_options.py, one row per contract
--  per 5-min bar):
--      Estimated per-contract open interest DURING the session.
--      open_interest_est = oi_base + cum_volume (GENERATED, stored):
--        • oi_base    — the contract's latest DAILY open_interest from
--          stats.options_volume_oi STRICTLY BEFORE the bar date (prev
--          trading day) — the position base carried into the session.
--        • cum_volume — the contract's day-cumulative traded volume
--          through the bar (tstyle volume is day-cumulative; bar volumes
--          sum back to it).
--      This is an ESTIMATOR: every traded contract is counted as newly
--      opened interest — closing trades / rolls are not netted out, so
--      open_interest_est is an UPPER BOUND of the true intraday OI.
--      Known source gaps (by design, not bugs):
--        • SSE daily per-contract OI does not exist (tstyle `position`
--          returns null — see builds/options/sse/__main__.py), so SSE rows
--          carry oi_base = 0 and the estimate is the day's traded volume.
--        • SZSE / CFFEX contracts have no intraday bars yet, so they get
--          no rows here at all; consumers fall back to the flat prev-day
--          daily OI (see live.options_intraday_skewness comments).
--
--  live.options_intraday_skewness (COMPUTED SERIES — written by
--  python -m live.options_intraday_skewness, one row per underlying per
--  5-min bar per expiry group + one mean row):
--      The intraday sibling of analysis.options_skewness_stats
--      (skew_type = 'oi_moneyness'): OI-weighted mean moneyness
--      E[M] = SUM(oi * K / S(t)) / SUM(oi) per expiry month, plotted as
--      skew_price = S(t) * E[M] against the underlying's 5-min spot.
--      OI inputs resolve per bar to live.options_intraday_oi when the
--      date is covered by the store, else to the flat prev-day daily
--      stats.options_volume_oi value (old dates: the store stays empty —
--      the series is (re)computed on demand from base tables).
--      The sentinel expiry_date DATE '9998-12-31' marks the MEAN row
--      (all active expiries blended); every real expiry date marks its
--      own group's row.
--
--  RETENTION (both tables): ROLLING CACHE of the newest 20 TRADING dates
--  — pruned by python -m live.options_intraday_skewness (once per new
--  trading day, guarded by the 'options_intraday_skewness_prune'
--  live_identity bookkeeping row). Older dates: the OI store stays empty
--  (accepted nulls) while the skewness series can be recomputed on
--  demand from the ~90d intraday base tables.
--
--  Populated by Python (per project rule: INSERTs live in Python code,
--  not raw INSERT...SELECT SQL — the live_identity registrations below
--  are the usual exception).
-- ============================================================================

CREATE TABLE IF NOT EXISTS live.options_intraday_oi (
    contract_code      TEXT          NOT NULL,  -- numeric contract code (FK stats.options_identity)
    date               DATE          NOT NULL,  -- trading day of the bar
    time               TIME          NOT NULL,  -- 5-min bar END time on the shared grid (09:35..15:00)
    underlying_code    TEXT          NOT NULL,  -- denormalized 6-digit underlying (e.g. 510050)
    exchange           TEXT          NOT NULL,  -- bar source exchange: 'SSE' (only SSE streams today)
    oi_base            NUMERIC(24,4) NOT NULL DEFAULT 0,  -- prev trading day's daily open_interest (stats.options_volume_oi); 0 when no daily OI exists (SSE)
    cum_volume         NUMERIC(24,4) NOT NULL DEFAULT 0,  -- day-cumulative traded volume through this bar (contracts)
    open_interest_est  NUMERIC(24,4) GENERATED ALWAYS AS (oi_base + cum_volume) STORED,  -- oi_base + cum_volume: the intraday OI estimate (upper bound — closes/rolls not netted)
    created_at         TIMESTAMP     NOT NULL DEFAULT NOW(),  -- row insertion time

    CONSTRAINT pk_options_intraday_oi PRIMARY KEY (contract_code, date, time),
    CONSTRAINT chk_options_intraday_oi_exchange CHECK (exchange IN ('SSE', 'SZSE', 'CFFEX')),
    CONSTRAINT fk_options_intraday_oi_identity FOREIGN KEY (contract_code, date)
        REFERENCES stats.options_identity (contract_code, date)
) PARTITION BY HASH (contract_code);

SELECT public.create_hash_partitions('live', 'options_intraday_oi', 8);

-- Per-underlying day read (skewness pipeline) + date-scoped prune.
CREATE INDEX IF NOT EXISTS idx_options_intraday_oi_und_date
    ON live.options_intraday_oi (underlying_code, date, time);
CREATE INDEX IF NOT EXISTS idx_options_intraday_oi_date
    ON live.options_intraday_oi (date);

CREATE TABLE IF NOT EXISTS live.options_intraday_skewness (
    underlying_code  TEXT           NOT NULL,  -- 6-digit option underlying (e.g. 510050)
    date             DATE           NOT NULL,  -- trading day of the bar
    time             TIME           NOT NULL,  -- 5-min bar END time on the shared grid (09:35..15:00)
    expiry_date      DATE           NOT NULL,  -- group's real expiry; DATE '9998-12-31' = MEAN row (all active expiries blended)
    spot             NUMERIC(18,6),            -- underlying 5-min close S(t) the skew was computed at (yuan)
    skew_price       NUMERIC(18,6),            -- S(t) * E[M] — the skew-adjusted price level (yuan)
    skew_pct         NUMERIC(12,6),            -- (E[M] - 1) * 100 — positioning skew vs ATM in percent
    oi_total         NUMERIC(24,4),            -- group's total OI weight at t (contracts; drives the UI line-width encoding)
    otm_call_share   NUMERIC(10,6),            -- share of call OI at strikes >= S(t) (0..1; NULL when no call OI)
    otm_put_share    NUMERIC(10,6),            -- share of put OI at strikes <= S(t) (0..1; NULL when no put OI)
    skew_type        TEXT           NOT NULL DEFAULT 'oi_moneyness',  -- mirrors analysis.options_skewness_stats.skew_type vocab
    created_at       TIMESTAMP      NOT NULL DEFAULT NOW(),  -- row insertion time

    CONSTRAINT pk_options_intraday_skewness
        PRIMARY KEY (underlying_code, date, time, expiry_date, skew_type),
    CONSTRAINT chk_options_intraday_skewness_type
        CHECK (skew_type IN ('oi_moneyness', 'iv_smile', 'greek_delta', 'greek_gamma', 'greek_vega'))
) PARTITION BY HASH (underlying_code);

SELECT public.create_hash_partitions('live', 'options_intraday_skewness', 8);

-- The API read pattern: every series row of one underlying-day.
CREATE INDEX IF NOT EXISTS idx_options_intraday_skew_und_date
    ON live.options_intraday_skewness (underlying_code, date, time);
-- Date-scoped prune.
CREATE INDEX IF NOT EXISTS idx_options_intraday_skew_date
    ON live.options_intraday_skewness (date);

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE live.options_intraday_oi IS 'Rolling 20-trading-day store of ESTIMATED per-contract intraday open interest, written by the SSE options streamer one row per (contract_code, date, time) 5-min bar. open_interest_est = oi_base + cum_volume (GENERATED): the prev trading day''s daily OI (stats.options_volume_oi, strictly before the bar date) plus the day-cumulative traded volume through the bar. An upper-bound estimator — every trade counted as newly opened interest, closing trades / rolls not netted. Known gaps by design: SSE rows carry oi_base = 0 (no per-contract daily OI source exists for SSE — tstyle position returns null) so the estimate there is the day''s traded volume; SZSE / CFFEX contracts have no intraday bars and thus no rows here (consumers fall back to the flat prev-day daily OI). Retention: newest 20 trading dates, pruned by python -m live.options_intraday_skewness (once per new trading day via the options_intraday_skewness_prune live_identity guard); older dates intentionally stay empty.';
COMMENT ON COLUMN live.options_intraday_oi.contract_code IS 'Numeric option contract code (stats.options_identity grain) — the tstyle contractid resolved through the day-contract map.';
COMMENT ON COLUMN live.options_intraday_oi.date IS 'Trading day of the bar.';
COMMENT ON COLUMN live.options_intraday_oi.time IS '5-min bar END time on the shared exchange grid (09:35..15:00, ceiling convention — matches stats.options_intraday_5min.time).';
COMMENT ON COLUMN live.options_intraday_oi.underlying_code IS 'Denormalized 6-digit underlying (e.g. 510050) for per-underlying day reads without an identity join.';
COMMENT ON COLUMN live.options_intraday_oi.exchange IS 'Bar source exchange — literally ''SSE'' today (the only options stream). CHECK-constrained to the options_identity exchange vocab.';
COMMENT ON COLUMN live.options_intraday_oi.oi_base IS 'The contract''s latest DAILY open_interest from stats.options_volume_oi STRICTLY BEFORE the bar date (prev trading day) — the position base carried into the session. 0 when the day has no daily OI (SSE: no per-contract source exists).';
COMMENT ON COLUMN live.options_intraday_oi.cum_volume IS 'Day-cumulative traded volume of the contract through this bar (tstyle volume is day-cumulative; equal to the sum of the day''s per-bar volumes up to and including this bar).';
COMMENT ON COLUMN live.options_intraday_oi.open_interest_est IS 'GENERATED (oi_base + cum_volume): the intraday OI estimate at the bar — upper bound of true OI (closes / rolls not netted; new listings carry oi_base = 0 + today''s volume).';

COMMENT ON TABLE live.options_intraday_skewness IS 'Computed intraday OI-weighted moneyness skewness series — the 5-min sibling of analysis.options_skewness_stats (skew_type = ''oi_moneyness''). One row per (underlying_code, date, time, expiry_date, skew_type); expiry_date DATE ''9998-12-31'' marks the MEAN row (all active expiry groups blended). Per group: E[M] = SUM(oi * K / S(t)) / SUM(oi) over IV-valid active contracts (OI floored at 1, same semantics as the daily pipeline) with skew_price = S(t) * E[M], skew_pct = (E[M] - 1) * 100, oi_total (the group''s OI weight — drives the UI line-width encoding) and OTM shares. OI inputs: live.options_intraday_oi when the date is covered by the rolling store, else the flat prev-day daily stats.options_volume_oi value (SSE contracts: oi_base 0 + cumulative bar volume; SZSE / CFFEX: flat daily OI). Spot S(t) from the underlying''s 5-min table (stats.etf_intraday_5min / stats.index_intraday_5min by underlying_target_type). Written by python -m live.options_intraday_skewness (idempotent PK upserts); older dates are recomputed on demand from base tables. Retention: newest 20 trading dates (pruned with the OI store).';
COMMENT ON COLUMN live.options_intraday_skewness.underlying_code IS '6-digit option underlying (e.g. 510050 / 159915 / 000300).';
COMMENT ON COLUMN live.options_intraday_skewness.date IS 'Trading day of the bar.';
COMMENT ON COLUMN live.options_intraday_skewness.time IS '5-min bar END time on the shared exchange grid (09:35..15:00).';
COMMENT ON COLUMN live.options_intraday_skewness.expiry_date IS 'The expiry group''s real expiry date (contract-level expiry of the group); DATE ''9998-12-31'' is the MEAN-row sentinel (all active groups blended). The UI draws one dashed curve per real expiry plus the mean.';
COMMENT ON COLUMN live.options_intraday_skewness.spot IS 'Underlying 5-min close S(t) (yuan) the row was computed at — denormalized from the underlying''s intraday table so readers need no second table.';
COMMENT ON COLUMN live.options_intraday_skewness.skew_price IS 'S(t) * E[M] (yuan): the spot curve rebased by the group''s OI-weighted mean moneyness — plotted against spot; E[M] > 1 = OI parked overhead (supply), < 1 = support beneath.';
COMMENT ON COLUMN live.options_intraday_skewness.skew_pct IS '(E[M] - 1) * 100: the positioning skew vs ATM in percent, independent of the price level.';
COMMENT ON COLUMN live.options_intraday_skewness.oi_total IS 'The group''s total OI weight at t in contracts (sum of OI floored at 1) — drives the UI per-expiry line-width encoding; NULL when the group has no valid rows.';
COMMENT ON COLUMN live.options_intraday_skewness.otm_call_share IS 'Share of the group''s call OI at strikes >= S(t) (0..1) at this bar; NULL when the group has no call OI.';
COMMENT ON COLUMN live.options_intraday_skewness.otm_put_share IS 'Share of the group''s put OI at strikes <= S(t) (0..1) at this bar; NULL when the group has no put OI.';
COMMENT ON COLUMN live.options_intraday_skewness.skew_type IS 'Metric vocab of analysis.options_skewness_stats (oi_moneyness / iv_smile / greek_*); only ''oi_moneyness'' is written today.';
COMMENT ON COLUMN live.options_intraday_skewness.created_at IS 'Row insertion timestamp.';

-- ----------------------------------------------------------------------------
--  Register in live.live_identity (both writers).
-- ----------------------------------------------------------------------------
INSERT INTO live.live_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('options_intraday_oi', 'options_intraday_oi', 'options_intraday_oi', NOW(),
     'SSE options streamer write-through store of estimated per-contract intraday open interest in live.options_intraday_oi (one row per contract per 5-min bar; open_interest_est = oi_base + cum_volume GENERATED — prev trading day''s daily OI plus day-cumulative traded volume, an upper-bound estimator that does not net out closes / rolls). SSE rows carry oi_base = 0 because no per-contract daily OI source exists for SSE (tstyle position returns null); SZSE / CFFEX contracts have no intraday bars and get no rows (consumers fall back to flat prev-day daily OI). Rolling cache: newest 20 trading dates, pruned by the options_intraday_skewness pipeline.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;

INSERT INTO live.live_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('options_intraday_skewness', 'options_intraday_skewness', 'options_intraday_skewness', NOW(),
     'Intraday OI-weighted moneyness skewness series in live.options_intraday_skewness (one row per underlying per 5-min bar per expiry group + a DATE 9998-12-31 mean row; skew_price = S(t) * E[M], E[M] = OI-weighted mean moneyness over IV-valid active contracts — the 5-min sibling of analysis.options_skewness_stats oi_moneyness). OI inputs resolve per bar to live.options_intraday_oi when the rolling store covers the date, else to flat prev-day daily stats.options_volume_oi (SSE: 0 base + cumulative bar volume; SZSE / CFFEX: flat daily OI); spot from the underlying''s 5-min table. python -m live.options_intraday_skewness [--date D] [--underlying U] — idempotent PK upserts, API-invoked on demand for missing / stale dates, prune keeps the newest 20 trading dates (also reaps live.options_intraday_oi, once per new trading day via the options_intraday_skewness_prune live_identity guard).')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
