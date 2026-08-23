-- ============================================================================
--  Table: analysis.mov_ave_rsi_holiday  (per-asset+date non-trading-day risk)
--    One row per (sec_type, code, date). For each trading day D, captures
--    information about the PREVIOUS calendar day (D-1) and today's price
--    gaps that are relevant for assessing gap risk after non-trading periods.
--
--  Columns:
--    sec_type, code, date              — PK; identifies the asset universe
--                                      (etf vs index vs stock), ticker, and
--                                      the current trading day.
--    is_prev_day_trading              — TRUE if D-1 was a trading day
--                                      (is_trading_day(D-1) == True).
--    is_prev_day_weekend              — TRUE if D-1 was a weekend
--                                      (Saturday or Sunday) that was NOT
--                                      an adjusted workday.
--    is_prev_day_holiday              — TRUE if D-1 was an official
--                                      Chinese public holiday (CN_HOLIDAYS).
--    is_prev_day_long_holiday         — TRUE if D-1 was part of a long
--                                      holiday period (>= 3 consecutive
--                                      non-trading days including at least
--                                      one official holiday — distinguishes
--                                      Spring Festival / Golden Week from
--                                      single-day holidays + adjacent
--                                      weekends).
--    non_trading_day_count            — Number of consecutive non-trading
--                                      calendar days ending on D-1
--                                      (inclusive). 0 if D-1 was a trading
--                                      day.
--    today_high_low_gap               — (high - low) / close on date D.
--                                      Intraday volatility measure.
--                                      NUMERIC(18,4) as a ratio
--                                      (e.g. 0.0150 = 1.50%). NOT NULL
--                                      DEFAULT 0.0 when OHLC is missing.
--    today_open_close_gap             — (close - open) / open on date D.
--                                      Intraday direction/impulse.
--                                      NUMERIC(18,4) as a ratio. NOT NULL
--                                      DEFAULT 0.0 when OHLC is missing.
--
--  FK: (sec_type, code, date) → analysis.mov_ave_rsi(sec_type, code, date)
--      ON DELETE CASCADE. Rows only exist for dates already in mov_ave_rsi
--      — data integrity is guaranteed by the parent pipeline's INNER JOIN
--      on source tables. The CASCADE lets the identity→rsi FK cascade
--      (03_mov_ave_spreads.sql) flow through: deleting an identity row
--      removes its rsi row AND its holiday row instead of erroring.
--
--  Populated by the internal holiday step of `analyze.mov_ave_spread`
--  (see holiday.py). Incremental upsert by missing dates; --force truncates
--  first.
--
--  NOTE: The table is rebuilt from scratch on every full run. Incremental
--  mode only adds missing dates. Holiday/weekend classification uses the
--  project calendar in `_common._holidays_and_weekdays` (CN_HOLIDAYS +
--  CN_ADJUSTED_WORKDAYS, 2020-2026).
-- ============================================================================

-- Drop prior version for clean reinstall. CASCADE handles dependent objects
-- (views, triggers) if any exist.
DROP TABLE IF EXISTS analysis.mov_ave_rsi_holiday CASCADE;
DELETE FROM analysis.analysis_identity WHERE name = 'mov_ave_rsi_holiday';

CREATE TABLE analysis.mov_ave_rsi_holiday (
    sec_type        TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    code            TEXT         NOT NULL,
    date            DATE         NOT NULL,

    is_prev_day_trading    BOOLEAN      NOT NULL DEFAULT FALSE,
    is_prev_day_weekend    BOOLEAN      NOT NULL DEFAULT FALSE,
    is_prev_day_holiday    BOOLEAN      NOT NULL DEFAULT FALSE,
    is_prev_day_long_holiday BOOLEAN    NOT NULL DEFAULT FALSE,
    non_trading_day_count  INTEGER      NOT NULL DEFAULT 0,
    today_high_low_gap     NUMERIC(18,4) NOT NULL DEFAULT 0.0,
    today_open_close_gap   NUMERIC(18,4) NOT NULL DEFAULT 0.0,

    CONSTRAINT pk_mov_ave_rsi_holiday PRIMARY KEY (sec_type, code, date),
    CONSTRAINT fk_non_trading_day_risk_expiry
        FOREIGN KEY (sec_type, code, date)
        REFERENCES analysis.mov_ave_rsi (sec_type, code, date)
        ON DELETE CASCADE,
    CONSTRAINT chk_mov_ave_rsi_holiday_sec_type
        CHECK (sec_type IN ('etf', 'index', 'stock'))
);

-- NOTE: no separate (sec_type, code, date) index — the PK already covers
-- that lookup (same rationale as mov_ave_spreads_detail / mov_ave_rsi).

COMMENT ON TABLE  analysis.mov_ave_rsi_holiday IS
    'Non-trading-day risk analysis: one row per (sec_type, code, date). '
    'For each trading day D, captures whether the previous calendar day '
    '(D-1) was a trading day / weekend / holiday / long holiday, the '
    'consecutive non-trading-day count ending on D-1, and today''s '
    'intraday high-low gap and open-close gap. Populated by the internal '
    'holiday step of analyze.mov_ave_spread (holiday.py).';
COMMENT ON COLUMN analysis.mov_ave_rsi_holiday.sec_type IS
    'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis.mov_ave_rsi_holiday.code IS
    'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis.mov_ave_rsi_holiday.date IS
    'Business date (trading day) — the current day D. Previous-day flags refer to D-1 (calendar day).';
COMMENT ON COLUMN analysis.mov_ave_rsi_holiday.is_prev_day_trading IS
    'TRUE if the previous calendar day (D-1) was a trading day per the project calendar (CN_HOLIDAYS + CN_ADJUSTED_WORKDAYS).';
COMMENT ON COLUMN analysis.mov_ave_rsi_holiday.is_prev_day_weekend IS
    'TRUE if D-1 was a weekend (Sat/Sun) that was NOT an adjusted workday.';
COMMENT ON COLUMN analysis.mov_ave_rsi_holiday.is_prev_day_holiday IS
    'TRUE if D-1 was an official Chinese public holiday (in CN_HOLIDAYS).';
COMMENT ON COLUMN analysis.mov_ave_rsi_holiday.is_prev_day_long_holiday IS
    'TRUE if D-1 was part of a long holiday period (>= 3 consecutive non-trading days including at least one official holiday). Distinguishes Spring Festival / Golden Week from single-day holidays with adjacent weekends.';
COMMENT ON COLUMN analysis.mov_ave_rsi_holiday.non_trading_day_count IS
    'Number of consecutive non-trading calendar days ending on D-1 (inclusive). 0 when D-1 was a trading day.';
COMMENT ON COLUMN analysis.mov_ave_rsi_holiday.today_high_low_gap IS
    '(high - low) / close on date D. Intraday volatility ratio (e.g. 0.0150 = 1.50%). NUMERIC(18,4). DEFAULT 0.0 when OHLC is missing.';
COMMENT ON COLUMN analysis.mov_ave_rsi_holiday.today_open_close_gap IS
    '(close - open) / open on date D. Intraday direction/impulse ratio. NUMERIC(18,4). DEFAULT 0.0 when OHLC is missing.';

-- Migrate: add columns to pre-existing installs (CREATE TABLE includes them
-- for fresh installs, but ADD COLUMN IF NOT EXISTS retro-fits them to an
-- already-existing table without dropping data). Future columns added should
-- follow this pattern.
-- (No migration columns yet — placeholder for future additions.)