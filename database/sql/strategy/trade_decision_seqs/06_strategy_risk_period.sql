-- ============================================================================
--  Internal Risk Analytics — strategy.strategy_risk_period
--  Per-period (year / season / month) aggregations of gains/losses.
--  Part of strategy/trade_decision_seqs/ (applied in order by
--  strategy/00_init.sql; overview in 00_schema.sql).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Table: strategy_risk_period
--   Per-period gain/loss aggregations. One row per (seq_id, code, period_type,
--   period_value). period_type is 'year' / 'season' / 'month'.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS strategy.strategy_risk_period (
    seq_id                        BIGINT        NOT NULL,
    code                          TEXT          NOT NULL,
    period_type                   TEXT          NOT NULL
        CHECK (period_type IN ('year', 'season', 'month')),
    -- period_value encoding:
    --   year   = 'YYYY'               e.g. '2024'
    --   season = 'YYYY-Qn'            e.g. '2024-Q1' (Q1=Jan-Mar, ... Q4=Oct-Dec)
    --   month  = 'YYYY-MM'            e.g. '2024-08'
    period_value                  TEXT          NOT NULL,

    n_sells                       INTEGER       NOT NULL DEFAULT 0,
    n_buys                        INTEGER       NOT NULL DEFAULT 0,
    realized_pnl                  NUMERIC(24,4) NOT NULL DEFAULT 0,
    -- Mark-to-market change in unrealized_pnl during this period =
    -- unrealized_pnl(end of period) - unrealized_pnl(end of previous period).
    -- From strategy_daily. Realized + unrealized = total economic P&L for the period.
    unrealized_pnl                NUMERIC(24,4) NOT NULL DEFAULT 0,
    -- Worst (min, most negative) daily unrealized_pnl within this period —
    -- the deepest intra-period MTM loss (maximum unrealized loss). From
    -- strategy_daily. UI draws a transparent red bar for this.
    max_loss_unrealized_pnl       NUMERIC(24,4) NOT NULL DEFAULT 0,
    -- Peak (max, most positive) daily unrealized_pnl within this period —
    -- the highest intra-period MTM gain (maximum unrealized gain). From
    -- strategy_daily. UI draws a transparent green bar for this.
    max_gain_unrealized_pnl       NUMERIC(24,4) NOT NULL DEFAULT 0,
    -- Unrealized_pnl at the LAST trading day of this period (absolute level,
    -- not a change). From strategy_daily. Used by the UI to draw the
    -- period-end bar.
    end_unrealized_pnl            NUMERIC(24,4) NOT NULL DEFAULT 0,
    -- FT amplified realized P&L for this period (sum of per-SELL amplified
    -- P&L; the amplified strategy picks the adverse OHLC direction per
    -- SELL). 0 when no FT was applied or no SELLs in this period.
    ft_amplified_pnl              NUMERIC(24,4) NOT NULL DEFAULT 0,
    abs_pnl                       NUMERIC(24,4) NOT NULL DEFAULT 0,
    -- share of the run's total |P&L| that this period contributed (∈ [0,1])
    period_share                  NUMERIC(10,6),

    -- Is this period a "concentration hotspot"?
    is_concentration_hotspot      BOOLEAN       NOT NULL DEFAULT FALSE,
    is_counter_trend              BOOLEAN       NOT NULL DEFAULT FALSE,

    computed_at                   TIMESTAMPTZ   NOT NULL DEFAULT now(),

    CONSTRAINT pk_strategy_risk_period PRIMARY KEY (seq_id, code, period_type, period_value),
    CONSTRAINT fk_strategy_risk_period_seq FOREIGN KEY (seq_id)
        REFERENCES strategy.strategy_identity(seq_id) ON DELETE CASCADE,
    CONSTRAINT chk_risk_period_share CHECK (
        period_share IS NULL OR (period_share >= 0 AND period_share <= 1)
    )
) PARTITION BY HASH (seq_id);

-- Native hash partitions (8) keyed by seq_id
-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('strategy', 'strategy_risk_period', 8);

COMMENT ON TABLE  strategy.strategy_risk_period                  IS 'Per-period (year/season/month) gain/loss aggregations with top trades and concentration flags.';
COMMENT ON COLUMN strategy.strategy_risk_period.seq_id           IS 'FK → strategy_identity.seq_id.';
COMMENT ON COLUMN strategy.strategy_risk_period.code             IS 'Security code this period row pertains to.';
COMMENT ON COLUMN strategy.strategy_risk_period.period_type      IS 'year / season / month.';
COMMENT ON COLUMN strategy.strategy_risk_period.period_value     IS 'Period label: YYYY (year), YYYY-Qn (season), YYYY-MM (month).';
COMMENT ON COLUMN strategy.strategy_risk_period.realized_pnl     IS 'Sum of realized_pnl across SELLs in this period.';
COMMENT ON COLUMN strategy.strategy_risk_period.ft_amplified_pnl IS 'Sum of per-SELL FT amplified realized_pnl in this period. Amplified strategy picks adverse OHLC direction (loss→sell more, gain→sell less). 0 when no FT applied. UI plots cumulative trend vs baseline.';
COMMENT ON COLUMN strategy.strategy_risk_period.unrealized_pnl   IS 'Mark-to-market change in unrealized_pnl during this period = unrealized_pnl(end of period) - unrealized_pnl(end of previous period). From strategy_daily. Realized + unrealized = total economic P&L for the period.';
COMMENT ON COLUMN strategy.strategy_risk_period.max_loss_unrealized_pnl IS 'Worst (min, most negative) daily unrealized_pnl within this period — the deepest intra-period MTM loss (maximum unrealized loss). From strategy_daily. UI draws a transparent red bar for this.';
COMMENT ON COLUMN strategy.strategy_risk_period.max_gain_unrealized_pnl IS 'Peak (max, most positive) daily unrealized_pnl within this period — the highest intra-period MTM gain (maximum unrealized gain). From strategy_daily. UI draws a transparent green bar for this.';
COMMENT ON COLUMN strategy.strategy_risk_period.end_unrealized_pnl IS 'Unrealized_pnl at the LAST trading day of this period (absolute level, not a change). From strategy_daily. UI draws the period-end bar for this.';
COMMENT ON COLUMN strategy.strategy_risk_period.abs_pnl          IS 'Sum of |realized_pnl| across SELLs in this period.';
COMMENT ON COLUMN strategy.strategy_risk_period.period_share     IS 'abs_pnl / total_abs_pnl (total_abs_pnl on strategy_results) for the run. High share = concentrated activity in this period.';
COMMENT ON COLUMN strategy.strategy_risk_period.is_concentration_hotspot IS 'TRUE if period_share >= 0.25 — this period accounts for a quarter+ of all P&L activity.';
COMMENT ON COLUMN strategy.strategy_risk_period.is_counter_trend IS 'TRUE if this period''s realized_pnl sign differs from the run''s total (hidden counter-trend risk).';

-- idx_strategy_risk_period_seq_code_type dropped: (seq_id, code, period_type,
-- period_value) is exactly the PK, which already serves those lookups.

CREATE INDEX IF NOT EXISTS idx_strategy_risk_period_hotspot
    ON strategy.strategy_risk_period (seq_id, code)
    WHERE is_concentration_hotspot = TRUE;
