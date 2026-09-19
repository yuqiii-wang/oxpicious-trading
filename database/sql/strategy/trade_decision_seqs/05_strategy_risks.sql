-- ============================================================================
--  Internal Risk Analytics — strategy.strategy_risks
--  Per-(seq_id, code) RISK-SPECIFIC metrics (risk philosophy in the banner below).
--  Part of strategy/trade_decision_seqs/ (applied in order by
--  strategy/00_init.sql; overview in 00_schema.sql).
-- ============================================================================

-- ============================================================================
--  Internal Risk Analytics
--  Computes strategy risk metrics from the trade_decision history.
--
--  Two tables:
--    strategy.strategy_risks    — per-(seq_id, code) RISK-SPECIFIC metrics:
--                                    chronological concentration of gains/losses,
--                                    exponential risk score, top gain/loss trades,
--                                    price-based drawdowns. P&L summary cols
--                                    (total_realized_pnl / total_abs_pnl / n_sells
--                                    / n_buys) MOVED to strategy_results.
--    strategy.strategy_risk_period — per-period (year / season / month)
--                                    aggregations of gains/losses with the top
--                                    trades in each period.
--
--  Risk philosophy (chronological concentration):
--    If most gains/losses are concentrated in a short period, risk INCREASES
--    EXPONENTIALLY (a clustered drawdown is far more dangerous than a spread-
--    out one of equal magnitude, because it implies regime-dependent behavior
--    and a higher probability of ruin during that regime).
--    If gains/losses are spread evenly across time, risk DROPS (the strategy
--    performs consistently across regimes — lower regime dependence).
--
--  The concentration_ratio ∈ [0,1] measures the share of total |P&L| that
--    falls in the worst 30-day rolling window (still computed for the UI
--    hotspot flag, but NO LONGER drives risk_score directly).
--
--  risk_score is now an EXPONENTIAL ROLLING-WINDOW score over multiple
--    time horizons. For each window W ∈ {1d, 30d, 90d, 365d}, the worst
--    W-day rolling LOSS (realized, and separately unrealized MTM dip +
--    window-end residual) contributes:
--        exp(k · loss_fraction / threshold_W) - 1
--    where loss_fraction = |loss| / total_abs_pnl (LOSSES ONLY), threshold_W
--    comes from a log-curve fit through (month=25%, season=50%, year=75%
--    of total_abs_pnl), and k = ln 2 so hitting a threshold = +1.0.
--    Unrealized contributions are weighted at 30% vs realized. The score
--    thus scales EXPONENTIALLY with loss severity per horizon and rewards
--    spread-out (non-clustered) loss patterns.
-- ============================================================================
CREATE TABLE IF NOT EXISTS strategy.strategy_risks (
    seq_id                        BIGINT        NOT NULL,
    code                          TEXT          NOT NULL,

    -- Top-3 gain / loss / confidence BUY trade references
    -- (FK → trade_decision(seq_id, decision_no)).
    -- seq_id is shared with trade_decision (strategy_risks.seq_id IS the trade's
    -- seq_id), so only decision_no is stored. NULL when fewer than 3 trades
    -- of that side exist. The UI JOINs to trade_decision to fetch pnl /
    -- exec_date / signal_reason / qty on demand — no denormalized copies here.
    pnl_gain_1st_decision_no      INTEGER,
    pnl_gain_2nd_decision_no      INTEGER,
    pnl_gain_3rd_decision_no      INTEGER,
    pnl_loss_1st_decision_no      INTEGER,
    pnl_loss_2nd_decision_no      INTEGER,
    pnl_loss_3rd_decision_no      INTEGER,
    -- Top-3 highest-confidence BUYs (by qty descending; qty = confidence 0-100)
    confidence_buy_1st_decision_no      INTEGER,
    confidence_buy_2nd_decision_no      INTEGER,
    confidence_buy_3rd_decision_no      INTEGER,

    -- Chronological concentration
    --   max_30d_abs_pnl: the largest 30-day rolling sum of |realized_pnl|
    --   concentration_ratio = max_30d_abs_pnl / total_abs_pnl  (∈ [0,1])
    --   1.0 = all P&L crammed into a single 30-day window (max risk)
    --   ~0  = perfectly spread across time (min risk)
    max_30d_abs_pnl               NUMERIC(24,4),
    concentration_ratio           NUMERIC(10,6),  -- [0,1]
    concentration_window_start    DATE,           -- start of the worst 30d window
    concentration_window_end      DATE,           -- end of the worst 30d window

    -- Exponential rolling-window risk score (see table-level comment).
    --   The top-3 cumulative-P&L drawdown DATES + MAGNITUDES below are still
    --   persisted for UI display, but the risk_score itself is now computed
    --   from multi-horizon rolling losses (not concentration * drawdown).
    drawdown_1st_date             DATE,  -- trough date of the worst cumulative-P&L drawdown
    drawdown_2nd_date             DATE,  -- trough date of the 2nd-worst drawdown
    drawdown_3rd_date             DATE,  -- trough date of the 3rd-worst drawdown
    -- Per-episode drawdown magnitude (trough_cum_pnl - peak_cum_pnl, <= 0).
    -- 1st is the worst drawdown magnitude (for UI display only).
    drawdown_1st_val              NUMERIC(24,4),  -- worst drawdown magnitude (<= 0)
    drawdown_2nd_val              NUMERIC(24,4),  -- 2nd-worst drawdown magnitude (<= 0)
    drawdown_3rd_val              NUMERIC(24,4),  -- 3rd-worst drawdown magnitude (<= 0)
    risk_score                    NUMERIC(24,4),  -- exponential rolling-window score (see table comment)
    -- Approximate total PnL of the FT amplified strategy (NULL = no FT):
    -- BUY picks higher conf, SELL-at-loss higher, SELL-at-gain lower. The
    -- delta vs baseline total_realized_pnl drives the FT risk factor.
    ft_amplified_total_pnl        NUMERIC(24,4),

    -- Price-based drawdowns (worst unrealized peak-to-trough decline of the
    --   security's CLOSE price, as a signed fractional ratio <= 0).
    --   deepest_drop_since_unzero_pos: worst close-price drawdown observed
    --     during any maximal span where position > 0 (unzero holding period).
    --     Captures the worst paper-loss endured while holding.
    --   deepest_drop_since_last_buy: worst close-price drawdown observed from
    --     a BUY entry (seed peak = fill_price) until the next decision.
    --     Captures the maximum adverse excursion following an entry.
    --   peak_date / trough_date pinpoint where each drop occurred (for UI).
    deepest_drop_since_unzero_pos         NUMERIC(10,6),  -- fractional ratio (<= 0)
    deepest_drop_since_unzero_pos_peak_date    DATE,
    deepest_drop_since_unzero_pos_trough_date  DATE,
    deepest_drop_since_last_buy           NUMERIC(10,6),  -- fractional ratio (<= 0)
    deepest_drop_since_last_buy_peak_date      DATE,
    deepest_drop_since_last_buy_trough_date    DATE,

    -- Risk grade (derived from the absolute risk_score on the new
    --   exponential scale, k = ln 2 ⇒ one window at threshold = 1.0):
    --   LITTLE = criteria-based (almost no losses + stable gains + profitable)
    --   < 1.0 = LOW, 1.0–3.0 = MODERATE, 3.0–6.0 = ELEVATED, > 6.0 = HIGH
    risk_grade                    TEXT
        CHECK (risk_grade IN ('LITTLE', 'LOW', 'MODERATE', 'ELEVATED', 'HIGH')),

    computed_at                   TIMESTAMPTZ   NOT NULL DEFAULT now(),

    CONSTRAINT pk_strategy_risks PRIMARY KEY (seq_id, code),
    CONSTRAINT fk_strategy_risks_seq FOREIGN KEY (seq_id)
        REFERENCES strategy.strategy_identity(seq_id) ON DELETE CASCADE,
    -- FK: top-3 gain/loss trades → trade_decision(seq_id, decision_no).
    -- ON DELETE SET NULL: if a decision is removed, the ref just goes NULL
    -- rather than cascading the delete into the risk row.
    CONSTRAINT fk_risks_pnl_gain_1st FOREIGN KEY (seq_id, pnl_gain_1st_decision_no)
        REFERENCES strategy.trade_decision(seq_id, decision_no) ON DELETE SET NULL,
    CONSTRAINT fk_risks_pnl_gain_2nd FOREIGN KEY (seq_id, pnl_gain_2nd_decision_no)
        REFERENCES strategy.trade_decision(seq_id, decision_no) ON DELETE SET NULL,
    CONSTRAINT fk_risks_pnl_gain_3rd FOREIGN KEY (seq_id, pnl_gain_3rd_decision_no)
        REFERENCES strategy.trade_decision(seq_id, decision_no) ON DELETE SET NULL,
    CONSTRAINT fk_risks_pnl_loss_1st FOREIGN KEY (seq_id, pnl_loss_1st_decision_no)
        REFERENCES strategy.trade_decision(seq_id, decision_no) ON DELETE SET NULL,
    CONSTRAINT fk_risks_pnl_loss_2nd FOREIGN KEY (seq_id, pnl_loss_2nd_decision_no)
        REFERENCES strategy.trade_decision(seq_id, decision_no) ON DELETE SET NULL,
    CONSTRAINT fk_risks_pnl_loss_3rd FOREIGN KEY (seq_id, pnl_loss_3rd_decision_no)
        REFERENCES strategy.trade_decision(seq_id, decision_no) ON DELETE SET NULL,
    CONSTRAINT fk_risks_confidence_buy_1st FOREIGN KEY (seq_id, confidence_buy_1st_decision_no)
        REFERENCES strategy.trade_decision(seq_id, decision_no) ON DELETE SET NULL,
    CONSTRAINT fk_risks_confidence_buy_2nd FOREIGN KEY (seq_id, confidence_buy_2nd_decision_no)
        REFERENCES strategy.trade_decision(seq_id, decision_no) ON DELETE SET NULL,
    CONSTRAINT fk_risks_confidence_buy_3rd FOREIGN KEY (seq_id, confidence_buy_3rd_decision_no)
        REFERENCES strategy.trade_decision(seq_id, decision_no) ON DELETE SET NULL,
    CONSTRAINT chk_risk_concentration CHECK (
        concentration_ratio IS NULL OR
        (concentration_ratio >= 0 AND concentration_ratio <= 1)
    ),
    -- Price-based drops are signed fractional ratios (<= 0); 0 = no drop.
    CONSTRAINT chk_risk_drop_unzero CHECK (
        deepest_drop_since_unzero_pos IS NULL OR
        deepest_drop_since_unzero_pos <= 0
    ),
    CONSTRAINT chk_risk_drop_last_buy CHECK (
        deepest_drop_since_last_buy IS NULL OR
        deepest_drop_since_last_buy <= 0
    ),
    -- Drawdown magnitudes are signed P&L deltas (trough - peak, <= 0).
    CONSTRAINT chk_risk_drawdown_1st_val CHECK (
        drawdown_1st_val IS NULL OR drawdown_1st_val <= 0
    ),
    CONSTRAINT chk_risk_drawdown_2nd_val CHECK (
        drawdown_2nd_val IS NULL OR drawdown_2nd_val <= 0
    ),
    CONSTRAINT chk_risk_drawdown_3rd_val CHECK (
        drawdown_3rd_val IS NULL OR drawdown_3rd_val <= 0
    )
) PARTITION BY HASH (seq_id);

-- Native hash partitions (8) keyed by seq_id
-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('strategy', 'strategy_risks', 8);

COMMENT ON COLUMN strategy.strategy_risks.ft_amplified_total_pnl IS
    'Approximate total PnL of the FT amplified strategy (NULL = no FT). BUY amplifies position, SELL-at-loss sells more, SELL-at-gain sells less. Delta vs baseline total_realized_pnl drives the fault_tolerance risk factor.';

COMMENT ON TABLE  strategy.strategy_risks                  IS 'Per-(seq, code) RISK-SPECIFIC metrics: chronological P&L concentration, exponential risk score, top-3 gain/loss trade FK refs, price-based drawdowns, FT amplified PnL. P&L summary (total_realized_pnl / total_abs_pnl / n_sells / n_buys) moved to strategy_results.';
COMMENT ON COLUMN strategy.strategy_risks.seq_id           IS 'FK → strategy_identity.seq_id.';
COMMENT ON COLUMN strategy.strategy_risks.code             IS 'Security code this risk row pertains to (denormalized for fast UI display).';
COMMENT ON COLUMN strategy.strategy_risks.pnl_gain_1st_decision_no IS 'decision_no of the 1st-largest single-trade gain (max realized_pnl among SELLs). FK → trade_decision(seq_id, decision_no). NULL if no SELL exists.';
COMMENT ON COLUMN strategy.strategy_risks.pnl_gain_2nd_decision_no IS 'decision_no of the 2nd-largest single-trade gain. FK → trade_decision(seq_id, decision_no). NULL if fewer than 2 SELLs.';
COMMENT ON COLUMN strategy.strategy_risks.pnl_gain_3rd_decision_no IS 'decision_no of the 3rd-largest single-trade gain. FK → trade_decision(seq_id, decision_no). NULL if fewer than 3 SELLs.';
COMMENT ON COLUMN strategy.strategy_risks.pnl_loss_1st_decision_no IS 'decision_no of the 1st-largest single-trade loss (min realized_pnl among SELLs). FK → trade_decision(seq_id, decision_no). NULL if no SELL exists.';
COMMENT ON COLUMN strategy.strategy_risks.pnl_loss_2nd_decision_no IS 'decision_no of the 2nd-largest single-trade loss. FK → trade_decision(seq_id, decision_no). NULL if fewer than 2 SELLs.';
COMMENT ON COLUMN strategy.strategy_risks.pnl_loss_3rd_decision_no IS 'decision_no of the 3rd-largest single-trade loss. FK → trade_decision(seq_id, decision_no). NULL if fewer than 3 SELLs.';
COMMENT ON COLUMN strategy.strategy_risks.confidence_buy_1st_decision_no IS 'decision_no of the highest-confidence BUY (max qty; qty = confidence 0-100). FK → trade_decision(seq_id, decision_no). NULL if no BUY exists.';
COMMENT ON COLUMN strategy.strategy_risks.confidence_buy_2nd_decision_no IS 'decision_no of the 2nd-highest-confidence BUY. FK → trade_decision(seq_id, decision_no). NULL if fewer than 2 BUYs.';
COMMENT ON COLUMN strategy.strategy_risks.confidence_buy_3rd_decision_no IS 'decision_no of the 3rd-highest-confidence BUY. FK → trade_decision(seq_id, decision_no). NULL if fewer than 3 BUYs.';
COMMENT ON COLUMN strategy.strategy_risks.max_30d_abs_pnl  IS 'The largest 30-day rolling sum of |realized_pnl| — peak P&L concentration window.';
COMMENT ON COLUMN strategy.strategy_risks.concentration_ratio IS 'max_30d_abs_pnl / total_abs_pnl (total_abs_pnl on strategy_results). 1.0 = all P&L in one 30-day window (max risk); ~0 = evenly spread (min risk).';
COMMENT ON COLUMN strategy.strategy_risks.concentration_window_start IS 'Start date of the worst (most concentrated) 30-day P&L window.';
COMMENT ON COLUMN strategy.strategy_risks.concentration_window_end   IS 'End date of the worst 30-day P&L window.';
COMMENT ON COLUMN strategy.strategy_risks.drawdown_1st_date IS 'Trough date (SELL exec_date where cumulative realized P&L bottomed) of the WORST peak-to-trough drawdown in cumulative realized P&L. NULL if there is no drawdown episode.';
COMMENT ON COLUMN strategy.strategy_risks.drawdown_2nd_date IS 'Trough date of the 2nd-worst cumulative-P&L drawdown. NULL if fewer than 2 drawdown episodes.';
COMMENT ON COLUMN strategy.strategy_risks.drawdown_3rd_date IS 'Trough date of the 3rd-worst cumulative-P&L drawdown. NULL if fewer than 3 drawdown episodes.';
COMMENT ON COLUMN strategy.strategy_risks.drawdown_1st_val IS 'Magnitude (trough_cum_pnl - peak_cum_pnl, signed <= 0) of the WORST cumulative-P&L drawdown. For UI display only (risk_score now uses rolling-window losses). NULL if no drawdown episode.';
COMMENT ON COLUMN strategy.strategy_risks.drawdown_2nd_val IS 'Magnitude (trough_cum_pnl - peak_cum_pnl, signed <= 0) of the 2nd-worst cumulative-P&L drawdown. NULL if fewer than 2 drawdown episodes.';
COMMENT ON COLUMN strategy.strategy_risks.drawdown_3rd_val IS 'Magnitude (trough_cum_pnl - peak_cum_pnl, signed <= 0) of the 3rd-worst cumulative-P&L drawdown. NULL if fewer than 3 drawdown episodes.';
COMMENT ON COLUMN strategy.strategy_risks.risk_score       IS 'Exponential rolling-window risk score. For each window W in {1d,30d,90d,365d}, the worst W-day rolling LOSS (realized + unrealized MTM dip + window-end residual) contributes exp(k * loss_fraction / threshold_W) - 1, where loss_fraction = |loss|/total_abs_pnl (LOSSES ONLY), threshold_W is a log-curve fit through (month=25%, season=50%, year=75% of total_abs_pnl), k = ln 2. Unrealized weighted at 30% vs realized. Higher = more dangerous.';
COMMENT ON COLUMN strategy.strategy_risks.risk_grade       IS 'LITTLE / LOW / MODERATE / ELEVATED / HIGH — LITTLE is criteria-based (almost no losses + stable gains + profitable); otherwise derived from risk_score: <1.0 LOW, <3.0 MODERATE, <6.0 ELEVATED, else HIGH.';
COMMENT ON COLUMN strategy.strategy_risks.deepest_drop_since_unzero_pos IS 'Worst close-price peak-to-trough drawdown (signed fractional ratio, <= 0) observed during any maximal span where position > 0. Captures the worst paper-loss endured while holding. 0 = price only rose while holding.';
COMMENT ON COLUMN strategy.strategy_risks.deepest_drop_since_unzero_pos_peak_date   IS 'Biz date of the peak (running max close) from which the worst unzero-position drop was measured.';
COMMENT ON COLUMN strategy.strategy_risks.deepest_drop_since_unzero_pos_trough_date IS 'Biz date of the trough (lowest close) reached in the worst unzero-position drop.';
COMMENT ON COLUMN strategy.strategy_risks.deepest_drop_since_last_buy  IS 'Worst close-price peak-to-trough drawdown (signed fractional ratio, <= 0) from a BUY entry (seed peak = fill_price) until the next decision. Maximum adverse excursion following an entry. 0 = price never fell below the entry after a buy.';
COMMENT ON COLUMN strategy.strategy_risks.deepest_drop_since_last_buy_peak_date     IS 'Biz date of the peak (running max close, seeded by the BUY fill_price) from which the worst since-last-buy drop was measured.';
COMMENT ON COLUMN strategy.strategy_risks.deepest_drop_since_last_buy_trough_date   IS 'Biz date of the trough (lowest close) reached in the worst since-last-buy drop.';
COMMENT ON COLUMN strategy.strategy_risks.computed_at      IS 'Timestamp this risk row was computed (UTC).';

-- Indexes
CREATE INDEX IF NOT EXISTS idx_strategy_risks_code
    ON strategy.strategy_risks (code);
