-- ============================================================================
--  Internal Risk Analytics — strategy.strategy_risk_factors
--  One row per contribution factor to the risk_score.
--  Part of strategy/trade_decision_seqs/ (applied in order by
--  strategy/00_init.sql; overview in 00_schema.sql).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Table: strategy.strategy_risk_factors
--   One row per contribution factor to the risk_score. The risk_score on
--   strategy_risks is the SUM of all factor contributions for that
--   (seq_id, code). This table lets the UI expand the risk score row to
--   show HOW the score was derived (which components fired, their raw
--   values, thresholds, and individual contributions).
--
--   Components:
--     realized         — per rolling-window realized loss (1/30/90/365d).
--     unrealized       — the MAX rolling-window unrealized MTM dip (×30%).
--     streak           — consecutive losing-month streak penalty.
--     period_asymmetry — losses dominate gains in var/mean (per period type).
--     period_tail      — worst period loss z-score beyond 2σ.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS strategy.strategy_risk_factors (
    seq_id          BIGINT        NOT NULL,
    code            TEXT          NOT NULL,
    component       TEXT          NOT NULL,
    label           TEXT          NOT NULL,
    sub_key         TEXT          NOT NULL DEFAULT '',
    contribution    NUMERIC(10,4) NOT NULL DEFAULT 0,
    raw_value       NUMERIC(18,6),
    threshold       NUMERIC(18,6),
    ratio           NUMERIC(10,4),

    CONSTRAINT pk_strategy_risk_factors
        PRIMARY KEY (seq_id, code, component, sub_key),
    CONSTRAINT fk_risk_factors_seq FOREIGN KEY (seq_id)
        REFERENCES strategy.strategy_identity(seq_id) ON DELETE CASCADE
) PARTITION BY HASH (seq_id);

-- Native hash partitions (8) keyed by seq_id
-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('strategy', 'strategy_risk_factors', 8);

COMMENT ON TABLE  strategy.strategy_risk_factors IS 'Per-factor contribution to risk_score. One row per (seq, code, component, sub_key). SUM(contribution) = strategy_risks.risk_score.';
COMMENT ON COLUMN strategy.strategy_risk_factors.component    IS 'realized / unrealized / streak / period_asymmetry / period_tail.';
COMMENT ON COLUMN strategy.strategy_risk_factors.label        IS 'Human-readable label (e.g. "Realized Loss (30d window)").';
COMMENT ON COLUMN strategy.strategy_risk_factors.sub_key     IS 'Window days (1/30/90/365) for realized/unrealized, streak length, or period type (month/season/year) for period signals.';
COMMENT ON COLUMN strategy.strategy_risk_factors.contribution IS 'This factor''s contribution to the total risk_score.';
COMMENT ON COLUMN strategy.strategy_risk_factors.raw_value    IS 'The raw input (loss amount, streak months, dominance ratio, z-score).';
COMMENT ON COLUMN strategy.strategy_risk_factors.threshold    IS 'The threshold at which this factor contributes 1.0 (or 6.0 for period signals).';
COMMENT ON COLUMN strategy.strategy_risk_factors.ratio        IS 'raw_value / threshold (capped at 4.0), the exponential driver.';

CREATE INDEX IF NOT EXISTS idx_strategy_risk_factors_seq_code
    ON strategy.strategy_risk_factors (seq_id, code);
