-- ============================================================================
--  Reconstruction Views
--  Create views that JOIN split tables to reconstruct the original table structure.
--  These views can be queried exactly like the original tables from schema.sql.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- View: v_debt_baseline
--   Reconstructs original debt_baseline table via JOIN on date
--   DROP first because CREATE OR REPLACE VIEW cannot insert columns in the
--   middle of an existing column list (only append). This is a leaf view —
--   nothing else depends on it.
-- ----------------------------------------------------------------------------
DROP VIEW IF EXISTS stats.v_debt_baseline;
CREATE OR REPLACE VIEW stats.v_debt_baseline AS
SELECT
    i.date,
    -- PBoC OMO
    o.omo_rate,
    o.omo_quantity,
    o.omo_tenor_days,
    o.omo_tenor_label,
    o.omo_all_rates,
    o.omo_all_tenors,
    o.omo_all_quantities,
    o.omo_dur_qty_pairs,
    -- Reverse-repo lifecycle
    r.repo_start_quantity,
    r.repo_end_quantity,
    r.repo_net_injection,
    r.repo_cumulative,
    -- Outright repo
    orp.outright_repo_marker,
    orp.outright_repo_quantity,
    orp.outright_repo_tenor_days,
    orp.outright_repo_tenor_label,
    orp.outright_repo_serial,
    -- MLF
    m.mlf_marker,
    m.mlf_quantity,
    m.mlf_tenor_days,
    m.mlf_tenor_label,
    m.mlf_serial,
    -- SHIBOR
    s.shibor_o_n,
    s.shibor_1w,
    s.shibor_2w,
    s.shibor_1m,
    s.shibor_3m,
    s.shibor_6m,
    s.shibor_9m,
    s.shibor_1y,
    -- Treasury yield curve
    t.cb_0d,
    t.cb_1m,
    t.cb_2m,
    t.cb_3m,
    t.cb_6m,
    t.cb_9m,
    t.cb_1y,
    t.cb_2y,
    t.cb_3y,
    t.cb_5y,
    t.cb_7y,
    t.cb_10y,
    t.cb_15y,
    t.cb_20y,
    t.cb_30y,
    t.cb_40y,
    t.cb_50y,
    -- LPR (monthly)
    l.lpr_1y,
    l.lpr_5y
FROM stats.debt_identity i
LEFT JOIN stats.debt_omo o ON i.date = o.date
LEFT JOIN stats.debt_repo r ON i.date = r.date
LEFT JOIN stats.debt_outright_repo orp ON i.date = orp.date
LEFT JOIN stats.debt_mlf m ON i.date = m.date
LEFT JOIN stats.debt_shibor s ON i.date = s.date
LEFT JOIN stats.debt_treasury t ON i.date = t.date
LEFT JOIN stats.debt_lpr l ON i.date = l.date;

COMMENT ON VIEW stats.v_debt_baseline IS 'Reconstructed debt_baseline view: JOIN of debt_identity + all debt sub-tables.';

-- ----------------------------------------------------------------------------
-- View: v_etf_margin
--   Reconstructs original etf_margin table via JOIN on (date, code)
--   DROP first because CREATE OR REPLACE VIEW cannot insert columns in the
--   middle of an existing column list (only append). These reconstruction
--   views are leaf views — nothing else depends on them.
-- ----------------------------------------------------------------------------
DROP VIEW IF EXISTS stats.v_etf_margin;
CREATE OR REPLACE VIEW stats.v_etf_margin AS
SELECT
    i.date,
    i.code,
    i.name,
    -- Raw OHLCV
    o.prev_close,
    o.open,
    o.high,
    o.low,
    o.close,
    o.pct_change,
    o.has_intraday_5mins,
    -- Adjustment
    a.cum_split_factor,
    a.is_split_event_day,
    a.action_type,
    a.implied_dividend_per_share,
    a.cum_dividend_per_share,
    a.adj_prev_close,
    a.adj_open,
    a.adj_high,
    a.adj_low,
    a.adj_close,
    -- Technical
    t.ma5,
    t.ma5_ratio,
    t.ma20,
    t.ma60,
    t.ma120,
    t.ma255,
    -- Liquidity & margin
    lm.volume_wan,
    lm.amount_wan,
    lm.rz_buy,
    lm.rz_balance,
    lm.rq_sell_qty,
    lm.rq_balance_qty,
    lm.rq_balance_amt,
    lm.total_balance
FROM stats.etf_identity i
LEFT JOIN stats.etf_basic_stats o ON i.date = o.date AND i.code = o.code
LEFT JOIN stats.etf_adjustment a ON i.date = a.date AND i.code = a.code
LEFT JOIN stats.etf_tech_stats t ON i.date = t.date AND i.code = t.code
LEFT JOIN stats.etf_liquidity_margin lm ON i.date = lm.date AND i.code = lm.code;

COMMENT ON VIEW stats.v_etf_margin IS 'Reconstructed etf_margin view: JOIN of etf_identity + all ETF sub-tables.';

-- ----------------------------------------------------------------------------
-- View: v_options_quote
--   Reconstructs original options_quote table via JOIN on (date, contract_code)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW stats.v_options_quote AS
SELECT
    i.date,
    i.contract_code,
    i.contract_name,
    -- Terms
    t.underlying_code,
    t.underlying_name,
    t.option_type,
    t.expiry_month,
    t.expiry_date,
    t.days_to_expiry,
    -- Strike
    s.strike_str,
    s.strike_price_raw,
    s.strike_price,
    s.has_a_suffix,
    -- Settlement
    st.prev_settle,
    st.close,
    st.settle,
    st.pct_change,
    st.prev_settle_norm,
    st.close_norm,
    st.settle_norm,
    st.underlying_close,
    st.moneyness_ratio,
    -- Greeks
    g.implied_vol,
    g.delta,
    g.theta,
    g.gamma,
    g.vega,
    g.rho,
    -- Volume & OI
    vo.volume,
    vo.volume_wan,
    vo.open_interest,
    vo.open_interest_wan,
    -- Aggregate
    a.total_volume_underlying,
    a.total_oi_underlying,
    a.volume_pct,
    a.open_interest_pct,
    a.oi_call_put_ratio,
    a.vol_call_put_ratio,
    a.open_interest_call,
    a.open_interest_put,
    a.volume_call,
    a.volume_put,
    a.oi_total_call_put_ratio
FROM stats.options_identity i
LEFT JOIN stats.options_terms t ON i.date = t.date AND i.contract_code = t.contract_code
LEFT JOIN stats.options_strike s ON i.date = s.date AND i.contract_code = s.contract_code
LEFT JOIN stats.options_settlement st ON i.date = st.date AND i.contract_code = st.contract_code
LEFT JOIN stats.options_greeks g ON i.date = g.date AND i.contract_code = g.contract_code
LEFT JOIN stats.options_volume_oi vo ON i.date = vo.date AND i.contract_code = vo.contract_code
LEFT JOIN stats.options_aggregate a ON i.date = a.date AND i.contract_code = a.contract_code;

COMMENT ON VIEW stats.v_options_quote IS 'Reconstructed options_quote view: JOIN of options_identity + all options sub-tables.';

-- ----------------------------------------------------------------------------
-- View: v_index_baseline
--   Reconstructs daily index data via JOIN on (date, code)
--   DROP first (see v_etf_margin note re: column insertion).
-- ----------------------------------------------------------------------------
DROP VIEW IF EXISTS stats.v_index_baseline;
CREATE OR REPLACE VIEW stats.v_index_baseline AS
SELECT
    i.date,
    i.code,
    i.name,
    -- Basic OHLCV
    bs.open,
    bs.high,
    bs.low,
    bs.close,
    bs.volume,
    bs.amount,
    bs.change,
    bs.change_pct,
    bs.has_intraday_5mins,
    -- Valuation
    v.pe,
    v.cons_number,
    -- Technical
    t.ma5,
    t.ma5_ratio,
    t.ma20,
    t.ma60,
    t.ma120,
    t.ma255
FROM stats.index_identity i
LEFT JOIN stats.index_basic_stats bs ON i.date = bs.date AND i.code = bs.code
LEFT JOIN stats.index_valuation v ON i.date = v.date AND i.code = v.code
LEFT JOIN stats.index_tech_stats t ON i.date = t.date AND i.code = t.code;

COMMENT ON VIEW stats.v_index_baseline IS 'Reconstructed index_baseline view: JOIN of index_identity + all index sub-tables.';

-- ----------------------------------------------------------------------------
-- View: v_stock_baseline
--   Reconstructs stock daily data via JOIN on (date, code)
--   Mirrors v_etf_margin structure (identity + basic_stats).
--   DROP first (see v_etf_margin note re: column insertion).
-- ----------------------------------------------------------------------------
DROP VIEW IF EXISTS stats.v_stock_baseline;
CREATE OR REPLACE VIEW stats.v_stock_baseline AS
SELECT
    i.date,
    i.code,
    i.name,
    -- OHLC + pct_change (mirrors etf_basic_stats)
    b.prev_close,
    b.open,
    b.high,
    b.low,
    b.close,
    b.pct_change,
    b.has_intraday_5mins,
    -- Stock-specific valuation
    b.pe,
    b.is_pe_estimated
FROM stats.stock_identity i
LEFT JOIN stats.stock_basic_stats b ON i.date = b.date AND i.code = b.code;

COMMENT ON VIEW stats.v_stock_baseline IS 'Reconstructed stock_baseline view: JOIN of stock_identity + stock_basic_stats. Mirrors v_etf_margin structure.';


-- ============================================================================
--  Sample queries (mirror what data_viz services return)
--  Using reconstructed views for backward compatibility
-- ============================================================================

-- (A) Debt-baseline: filter by date range, sorted ascending
-- SELECT date, omo_rate, omo_quantity, repo_cumulative,
--        outright_repo_marker, mlf_marker,
--        shibor_o_n, shibor_1y,
--        cb_1y, cb_10y, cb_30y
--   FROM stats.v_debt_baseline
--  WHERE date >= :start_date AND date <= :end_date
--  ORDER BY date ASC;

-- (B) ETF margin: time-series for one ETF
-- SELECT date, open, high, low, close, adj_close,
--        volume_wan, amount_wan,
--        rz_balance, rq_balance_amt, total_balance
--   FROM stats.v_etf_margin
--  WHERE code = :code
--    AND date >= :start_date AND date <= :end_date
--  ORDER BY date ASC;

-- (C) Latest top-5 holdings for one ETF
-- SELECT t.rank, t.stock_code, t.stock_name, t.weight_pct
--   FROM stats.sec_composition t
--  WHERE t.code = :code
--    AND t.source_type = 'etf'
--    AND t.rank <= 5
--    AND t.snapshot_date = (
--        SELECT MAX(snapshot_date)
--          FROM stats.sec_composition
--         WHERE code = :code
--           AND source_type = 'etf'
--    )
--  ORDER BY t.rank ASC;

-- (D) Options: all contracts for one underlying within a date range
-- SELECT date, contract_code, contract_name, option_type,
--        expiry_date, days_to_expiry, strike_price, settle,
--        underlying_close, moneyness_ratio,
--        open_interest, volume,
--        implied_vol, delta, theta, gamma, vega, rho
--   FROM stats.v_options_quote
--  WHERE underlying_code = :underlying_code
--    AND date >= :start_date AND date <= :end_date
--  ORDER BY date ASC;

-- (E) Option snapshot for one (underlying, date)
-- SELECT *
--   FROM stats.v_options_quote
--  WHERE underlying_code = :underlying_code
--    AND date = :snapshot_date
--  ORDER BY expiry_date, strike_price, option_type;