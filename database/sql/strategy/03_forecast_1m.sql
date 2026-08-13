-- ============================================================================
--  1-Month Forward Sell-Confidence Forecast (mirror/flip OHLC simulation)
--  Replaces the single last-day FINAL LIQUIDATION SELL with a 20-trading-day
--  forward-looking, mirror/flip SELL confidence schedule.
--
--  For each strategy run (seq_id) ending with an open position, the forecast
--  takes the last 20-day OHLC + the 255-day daily-return std, computes three
--  scale ratios (255d/20d, 20d/255d, 1:1), and for each scale generates TWO
--  curves from the 20d history OHLC:
--    mirror = time-reversed history, deviation scaled
--    flip   = time-reversed + vertically-inverted history, deviation scaled
--  → 6 directional curves. A 7th 'mean' curve = 0.5σ random walk on all four
--  OHLC values. Each curve stores a full synthetic OHLC bar + trading_amt
--  per day (amt proportional to |Δclose|, scaled to 20d historical average)
--  + a simulated RSI + a 20-day SELL confidence schedule (normalized so
--  cumulative position sold = 100% by day 20) + the cumulative realized-P&L
--  forecast if that schedule is followed. The P&L forecast starts at the
--  backtest's final total_pnl so the forecast connects to the actual curve.
--
--  Two tables:
--    strategy.forecast_1m        — per (seq_id, forecast_date, scenario, day)
--    strategy.forecast_1m_stats  — 1:1 per (seq_id, forecast_date): the 20d +
--                                   255d historical stats driving the simulation
-- ============================================================================
DROP TABLE IF EXISTS strategy.forecast_1m;
DROP TABLE IF EXISTS strategy.forecast_1m_stats;

-- ---------------------------------------------------------------------------
--  Main table: 7 scenarios × 20 days = 140 rows per (seq_id, forecast_date)
-- ---------------------------------------------------------------------------
CREATE TABLE strategy.forecast_1m (
    seq_id              BIGINT        NOT NULL,
    code                TEXT          NOT NULL,
    forecast_date       DATE          NOT NULL,
    scenario            TEXT          NOT NULL
        CHECK (scenario IN ('mir_255d_std_scale','flip_255d_std_scale','mir_255d_std_half_scale','flip_255d_std_half_scale','mir_20d_std_scale','flip_20d_std_scale','mir_255d_max_std_scale','flip_255d_max_std_scale','rand','rand_opp','mean')),
    forecast_day        SMALLINT      NOT NULL,   -- 1..20

    -- Synthetic OHLC (base = 100 at the forecast_date close). For mirror/flip
    -- scenarios: time-reversed 20d history OHLC, deviation scaled by the std
    -- ratio. For 'mean': 0.5σ random walk on all four values.
    open_price          NUMERIC(18,6) NOT NULL,
    high_price          NUMERIC(18,6) NOT NULL,
    low_price           NUMERIC(18,6) NOT NULL,
    close_price         NUMERIC(18,6) NOT NULL,
    daily_return        NUMERIC(18,6) NOT NULL DEFAULT 0,

    -- Synthetic trading amount (NULL when the underlying has no trading_amount
    -- column, e.g. etf/stock basic_stats). Proportional to |Δclose|, scaled
    -- to the 20d historical average trading_amt.
    trading_amt         NUMERIC(24,4),

    -- Simulated RSI for this scenario/day (0-100). Starts from the current
    -- rsi_14 at forecast_date and drifts with the scenario's total return
    -- direction (up → overbought, down → oversold).
    rsi                 NUMERIC(10,6),

    -- SELL schedule for this scenario.
    -- sell_fraction: fraction of the ORIGINAL position sold on this day.
    --                Sums to 1.0 across the 20 days per scenario.
    -- sell_confidence: engine-scale confidence (0-100), = fraction of the
    --                  REMAINING position to sell. Day 20 = 100 (full
    --                  liquidation, mirroring FINAL LIQUIDATION).
    sell_fraction       NUMERIC(10,6) NOT NULL CHECK (sell_fraction >= 0),
    sell_confidence     NUMERIC(12,4) NOT NULL CHECK (sell_confidence >= 0 AND sell_confidence <= 100),

    -- Cumulative realized P&L forecast (in backtest-normalized money) if this
    -- scenario's sell schedule is followed. Starts at the backtest's final
    -- total_pnl so the forecast connects to the actual Total P&L curve.
    -- = last_total_pnl + running sum of
    --   (qty_sold/100) * (backtest_norm_close - cost_basis_norm)
    -- where backtest_norm_close = close_price * anchor_close / first_buy_fill_price.
    realized_pnl_forecast NUMERIC(18,6) NOT NULL DEFAULT 0,

    -- Scenario probability weight (NULL for all scenarios in the mirror/flip
    -- model — no probability weighting is applied; each scenario is a
    -- deterministic projection from the 20d history).
    scenario_weight     NUMERIC(10,6),

    -- Denormalized run context (constant across scenario/day for one forecast).
    total_qty           NUMERIC(12,4) NOT NULL,  -- position carried into the horizon
    cost_basis_norm     NUMERIC(18,6) NOT NULL,  -- weighted-avg BUY normalized price (backtest-norm)

    computed_at         TIMESTAMPTZ   NOT NULL DEFAULT now(),

    CONSTRAINT pk_forecast_1m PRIMARY KEY (seq_id, forecast_date, scenario, forecast_day),
    CONSTRAINT fk_forecast_1m_seq FOREIGN KEY (seq_id)
        REFERENCES strategy.strategy_identity (seq_id) ON DELETE CASCADE,
    CONSTRAINT chk_forecast_1m_day CHECK (forecast_day >= 1 AND forecast_day <= 20),
    CONSTRAINT chk_forecast_1m_weight CHECK (
        scenario_weight IS NULL OR (scenario_weight >= 0 AND scenario_weight <= 1)
    ),
    CONSTRAINT chk_forecast_1m_ohlc CHECK (
        low_price <= open_price AND low_price <= close_price
        AND high_price >= open_price AND high_price >= close_price
        AND low_price <= high_price
    )
);

COMMENT ON TABLE  strategy.forecast_1m                 IS '1-month (20-trading-day) forward forecast. 6 mirror/flip curves (3 scale ratios × 2 directions) + 1 random-walk mean, each carrying full synthetic OHLC + trading_amt + simulated RSI + a 20-day SELL schedule that fully liquidates the remaining position by day 20, plus the cumulative realized-P&L forecast starting at the backtest final total_pnl.';
COMMENT ON COLUMN strategy.forecast_1m.open_price      IS 'Synthetic open (base=100 at forecast_date close). Mirror/flip: time-reversed 20d history open, deviation scaled. Mean: 0.5σ random step from previous close.';
COMMENT ON COLUMN strategy.forecast_1m.high_price      IS 'Synthetic high. Mirror/flip: time-reversed 20d history high (or swapped low for flip), deviation scaled. Mean: max(open, close) + 0.5σ HL range.';
COMMENT ON COLUMN strategy.forecast_1m.low_price       IS 'Synthetic low. Mirror/flip: time-reversed 20d history low (or swapped high for flip), deviation scaled. Mean: min(open, close) - 0.5σ HL range.';
COMMENT ON COLUMN strategy.forecast_1m.close_price     IS 'Synthetic close (base=100). Mirror: +scale × (reversed hist close deviation). Flip: -scale × (reversed hist close deviation). Mean: 0.5σ random walk.';
COMMENT ON COLUMN strategy.forecast_1m.trading_amt     IS 'Synthetic trading amount (yuan). Proportional to |Δclose| / |prev_close|, scaled so the average matches the 20d historical avg trading_amt. NULL when the underlying has no trading_amount column (etf/stock).';
COMMENT ON COLUMN strategy.forecast_1m.rsi             IS 'Simulated RSI (0-100). Starts from rsi_14 at forecast_date, drifts by sign(total_return) × (t/20) × RSI_DRIFT_SCALE (15). Up scenarios → overbought, down → oversold.';
COMMENT ON COLUMN strategy.forecast_1m.sell_fraction   IS 'Fraction of ORIGINAL position sold this day. Sums to 1.0 across the 20 days per scenario.';
COMMENT ON COLUMN strategy.forecast_1m.sell_confidence IS 'Engine-scale SELL confidence (0-100) = fraction of REMAINING position to sell. Day 20 = 100 (full liquidation, mirroring FINAL LIQUIDATION).';
COMMENT ON COLUMN strategy.forecast_1m.realized_pnl_forecast IS 'Cumulative realized P&L (backtest-normalized money) starting at the backtest final total_pnl + running sum of (qty_sold/100)*(backtest_norm_close - cost_basis_norm). backtest_norm_close = close_price * anchor_close / first_buy_fill_price.';
COMMENT ON COLUMN strategy.forecast_1m.scenario_weight IS 'NULL for all scenarios in the mirror/flip model (no probability weighting).';
COMMENT ON COLUMN strategy.forecast_1m.total_qty       IS 'Position (qty/confidence units, NOT /100) carried into the horizon = total_qty_before of the run FINAL LIQUIDATION SELL. 0 => no forecast rows.';
COMMENT ON COLUMN strategy.forecast_1m.cost_basis_norm IS 'Weighted-avg BUY normalized price (backtest-norm base=100@first-buy) at horizon start = normalized_mean_buy_price of the FINAL LIQUIDATION SELL. P&L reference.';

CREATE INDEX IF NOT EXISTS idx_forecast_1m_seq_date
    ON strategy.forecast_1m (seq_id, forecast_date);
CREATE INDEX IF NOT EXISTS idx_forecast_1m_code
    ON strategy.forecast_1m (code, forecast_date DESC);

-- ---------------------------------------------------------------------------
--  Stats table: 1:1 per (seq_id, forecast_date). The 20d + 255d historical
--  stats driving the OHLC/amt/RSI simulation. Constant across scenario/day.
-- ---------------------------------------------------------------------------
CREATE TABLE strategy.forecast_1m_stats (
    seq_id              BIGINT        NOT NULL,
    forecast_date       DATE          NOT NULL,

    -- Volatility: daily log-return population std over the last 20 trading days.
    sigma_daily         NUMERIC(18,6) NOT NULL,

    -- Long-term volatility: daily log-return population std over the last 255
    -- trading days (1 trading year). Used to compute the mirror/flip scale
    -- ratios (255d/20d, 20d/255d). Falls back to sigma_daily when unavailable.
    sigma_255d          NUMERIC(18,6) NOT NULL,

    -- Peak long-term volatility: max rolling 255d std over the past calendar
    -- year. Used to compute the "maxstd" scale ratio (sigma_255d_max / sigma_20d)
    -- which captures the worst-case long-term volatility observed.
    sigma_255d_max      NUMERIC(18,6) NOT NULL,

    -- 20d open-close gap stats (signed: close-open / open).
    oc_gap_mean         NUMERIC(18,6) NOT NULL,
    oc_gap_std          NUMERIC(18,6) NOT NULL,

    -- 20d high-low gap stats (always positive: high-low / low).
    hl_gap_mean         NUMERIC(18,6) NOT NULL,
    hl_gap_std          NUMERIC(18,6) NOT NULL,

    -- 20d trading-amount stats. NULL when the underlying has no trading_amount
    -- column (etf/stock basic_stats).
    amt_mean            NUMERIC(24,4),
    amt_std             NUMERIC(24,4),
    -- Correlation between daily hl_gap and trading_amt over the 20d window.
    -- Validates the "amt proportional to gap" assumption; near 1 = strong.
    amt_hl_corr         NUMERIC(10,6),

    -- RSI levels at forecast_date (from analysis.mov_ave_rsi).
    rsi_6               NUMERIC(10,6),
    rsi_10              NUMERIC(10,6),
    rsi_14              NUMERIC(10,6),
    rsi_20              NUMERIC(10,6),

    -- Actual close on forecast_date (the anchor for forecast-norm base=100).
    -- Used to convert forecast close prices → backtest-normalized prices for
    -- the realized_pnl_forecast computation.
    anchor_close        NUMERIC(18,6) NOT NULL,

    -- Denormalized from strategy_results — the backtest normalization anchor
    -- (first BUY fill price). Combined with anchor_close to convert forecast
    -- prices into backtest-norm for P&L.
    first_buy_fill_price NUMERIC(18,6),

    -- The backtest's final total_pnl (from strategy_daily). The P&L forecast
    -- offset — realized_pnl_forecast starts at this value so the forecast
    -- connects to the actual Total P&L curve.
    last_total_pnl      NUMERIC(18,6) NOT NULL DEFAULT 0,

    computed_at         TIMESTAMPTZ   NOT NULL DEFAULT now(),

    CONSTRAINT pk_forecast_1m_stats PRIMARY KEY (seq_id, forecast_date),
    CONSTRAINT fk_forecast_1m_stats_seq FOREIGN KEY (seq_id)
        REFERENCES strategy.strategy_identity (seq_id) ON DELETE CASCADE,
    CONSTRAINT chk_forecast_1m_stats_corr CHECK (
        amt_hl_corr IS NULL OR (amt_hl_corr >= -1 AND amt_hl_corr <= 1)
    )
);

COMMENT ON TABLE  strategy.forecast_1m_stats            IS '1:1 per (seq_id, forecast_date). 20-day + 255-day historical stats (volatility, OHLC gaps, trading-amount, RSI) driving the forecast_1m mirror/flip simulation.';
COMMENT ON COLUMN strategy.forecast_1m_stats.sigma_daily IS 'Daily log-return population std over the last 20 trading days.';
COMMENT ON COLUMN strategy.forecast_1m_stats.sigma_255d  IS 'Daily log-return population std over the last 255 trading days. Used to compute the mirror/flip scale ratios (255d/20d, 20d/255d).';
COMMENT ON COLUMN strategy.forecast_1m_stats.oc_gap_mean IS 'Mean of (close-open)/open over the last 20 trading days. Signed — positive = close above open (up days).';
COMMENT ON COLUMN strategy.forecast_1m_stats.hl_gap_mean IS 'Mean of (high-low)/low over the last 20 trading days. Always positive — intraday range.';
COMMENT ON COLUMN strategy.forecast_1m_stats.amt_hl_corr IS 'Pearson correlation between daily hl_gap and trading_amt over the 20d window. Validates "amt proportional to gap" — near 1 = strong positive.';
COMMENT ON COLUMN strategy.forecast_1m_stats.anchor_close IS 'Actual close price on forecast_date. The forecast-norm anchor (close=100). Combined with first_buy_fill_price to convert forecast prices → backtest-norm for P&L.';
COMMENT ON COLUMN strategy.forecast_1m_stats.last_total_pnl IS 'The backtest final total_pnl (from strategy_daily). The P&L forecast offset — realized_pnl_forecast starts at this value.';
