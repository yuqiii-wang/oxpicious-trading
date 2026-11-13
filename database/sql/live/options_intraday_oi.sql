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
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Estimated per-contract open interest through the trading day — the previous day's OI plus cumulative traded volume, refreshed per 5-minute bar.
-- tags: options, 期权, open interest, 持仓量, intraday, 盘中, live, 实时


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
    CONSTRAINT chk_options_intraday_oi_exchange CHECK (exchange IN ('SSE', 'SZSE', 'CFFEX'))
) PARTITION BY HASH (contract_code);

SELECT public.create_hash_partitions('live', 'options_intraday_oi', 8);

-- Per-underlying day read (skewness pipeline) + date-scoped prune.
CREATE INDEX IF NOT EXISTS idx_options_intraday_oi_und_date
    ON live.options_intraday_oi (underlying_code, date, time);
CREATE INDEX IF NOT EXISTS idx_options_intraday_oi_date
    ON live.options_intraday_oi (date);


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
