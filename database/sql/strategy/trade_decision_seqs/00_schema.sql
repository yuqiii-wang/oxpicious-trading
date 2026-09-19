-- ============================================================================
--  Trade Decision Sequences
--  Records each strategy execution (one backtest run on ONE code) and the
--  ordered trade decisions executed within it.
--
--  Tables:
--    strategy.strategy_identity         — one row per (strategy, code) run (IDENTITY)
--    strategy.strategy_results     — 1:1 with strategy_identity: run RESULTS (dates,
--                                    total_buy_cost, first-buy anchor, P&L
--                                    summary moved here from strategy_identity /
--                                    strategy_risks)
--    strategy.trade_decision       — ordered decisions; carries
--                                    normalized_fill_price (base = 100 at the
--                                    first BUY fill)
--    strategy.strategy_daily       — daily portfolio state (one row per trading
--                                    day); unrealized_pnl = P&L if all remaining
--                                    position sold at the day's close
--    strategy.v_trade_decision_full — convenience JOIN of seq + info + decision
--
--  Normalization:
--    strategy_results.first_buy_fill_price is the anchor. Each trade_decision row
--    carries normalized_fill_price = fill_price / first_buy_fill_price * 100,
--    so the first BUY = 100 and every later fill reads as a % gain/loss from
--    the entry (105.0 = +5%, 94.1 = -5.9%). The UI also rebases the OHLC/MA
--    chart series off this same anchor.
--
--  No fixed capital: each BUY deploys (confidence/100) * buy_notional; cash
--  starts at 0 (goes negative on BUY = borrowing). MONEY metrics (position,
--  cash, realized_pnl, total_buy_cost) use shares = total_qty / 100 so they
--  are on a scale comparable to total_qty at the entry anchor (norm=100).
--  total_buy_cost = peak capital deployed = (max(total_qty_after)/100) ×
--  normalized_mean_buy_price at that decision. Computed AFTER the backtest
--  and stored on strategy_results. Total Return = final_cash / total_buy_cost.
--
--  Layout (this directory, applied in order):
--    00_schema.sql              — this file: schema, grants, search_path
--    01_strategy_identity.sql   — strategy.strategy_identity (+ indexes)
--    02_strategy_results.sql    — strategy.strategy_results (+ index)
--    03_trade_decision.sql      — strategy.trade_decision (+ index)
--    04_strategy_daily.sql      — strategy.strategy_daily (+ index)
--    05_strategy_risks.sql      — strategy.strategy_risks (+ index)
--    06_strategy_risk_period.sql     — strategy.strategy_risk_period (+ index)
--    07_strategy_risk_factors.sql    — strategy.strategy_risk_factors (+ index)
--    08_views.sql               — v_trade_decision_full / v_strategy_risk_full
--
--  Usage: psql -d strategy -f strategy/00_init.sql (applies everything
--  in order), or apply the files in this directory in filename order.
-- ============================================================================

-- ----------------------------------------------------------------------------
--  Schema + grants (mirrors analysis schema conventions defined in
--  database/sql/analysis/01_analysis_schema.sql)
-- ----------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS strategy;

GRANT USAGE ON SCHEMA strategy TO public;
GRANT USAGE ON SCHEMA strategy TO anon;
GRANT USAGE ON SCHEMA strategy TO authenticated;
GRANT USAGE ON SCHEMA strategy TO service_role;

ALTER DEFAULT PRIVILEGES IN SCHEMA strategy GRANT ALL ON TABLES TO public;
ALTER DEFAULT PRIVILEGES IN SCHEMA strategy GRANT ALL ON SEQUENCES TO public;

ALTER DEFAULT PRIVILEGES IN SCHEMA strategy GRANT SELECT ON TABLES TO anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA strategy GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA strategy GRANT ALL ON TABLES TO service_role;

ALTER DEFAULT PRIVILEGES IN SCHEMA strategy GRANT USAGE, SELECT ON SEQUENCES TO anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA strategy GRANT USAGE, SELECT ON SEQUENCES TO authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA strategy GRANT ALL ON SEQUENCES TO service_role;

-- Ensure postgres has full privileges on any existing objects
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA strategy TO postgres;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA strategy TO postgres;

-- Add strategy to the postgres search path (after stats, analysis, public)
ALTER ROLE postgres SET search_path TO stats, analysis, strategy, public;
