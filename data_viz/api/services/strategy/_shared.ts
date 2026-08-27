/**
 * Shared internals for the strategy service sub-package — SQL constants,
 * DB row types, and row→API mappers used by more than one module.
 *
 * Public API surface is re-exported from ./index.ts.
 */
import { formatDate, toNum } from "../db.service.js";
import type { StrategyDailyRow } from "../../../shared/types.js";

// Algo default — must match the Python DEFAULT_ALGO in
// strategy/factors_and_algos/_algo/registry.py.
// The DB strategy_name is EITHER an algo name (binary mode:
// "macd") OR a portfolio name (mixed mode: "portfolio:macd*0.5").
// Default: macd (binary).
export const DEFAULT_STRATEGY_NAME = "macd";

// ---------------------------------------------------------------------------
//  DB row types
// ---------------------------------------------------------------------------
export interface TradeDecisionRow {
  decision_no: number;
  side: string;
  exec_date: string | null;
  qty: string | number;
  fill_price: string | number | null;
  normalized_fill_price: string | number | null;
  normalized_mean_buy_price: string | number | null;
  position_before: string | number;
  position_after: string | number;
  cash_before: string | number;
  cash_after: string | number;
  total_qty_before: string | number;
  total_qty_after: string | number;
  realized_pnl: string | number;
  slippage: string | number | null;
  fee: string | number | null;
  signal_value: string | number | null;
  signal_reason: string | null;
  ft_stressed_conf_up: string | number | null;
  ft_stressed_conf_down: string | number | null;
}

export interface SeqRow {
  seq_id: number;
  fault_tolerance: string | number | null;
}

export interface InfoRow {
  total_buy_cost: string | number | null;
  first_buy_date: string | null;
  first_buy_fill_price: string | number | null;
  total_realized_pnl: string | number;
  total_abs_pnl: string | number;
  n_sells: number;
  n_buys: number;
}

export interface DailyRow {
  trade_date: string | Date;
  unrealized_pnl: string | number;
  total_pnl: string | number;
  realized_pnl_cum: string | number;
  total_qty: string | number;
  position_value: string | number;
  normalized_mean_buy_period: string | number;
  return_rate: string | number;
  sharpe_ratio: string | number;
  sharpe_ratio_255d: string | number;
  sharpe_ratio_500d: string | number;
}

// ---------------------------------------------------------------------------
//  SQL — fetch the latest strategy_seq for a given code + sec_type (strategy_seq
//  is per-code, so a direct filter on strategy_seq suffices — no JOIN to
//  trade_decision needed). Run RESULTS (total_buy_cost, first-buy anchor, P&L
//  summary) live on the 1:1 strategy_results row. Then all trade_decision rows in
//  that seq (no code filter needed — the seq is already per-code).
// ---------------------------------------------------------------------------
// $3 = strategy_name (algo name for binary mode, or portfolio:bb*0.5+macd*0.5 for mixed).
//
// Priority order for selecting the "latest" run:
//   1. is_active = TRUE (strategy_identity) — the run the UI loads by default
//   2. seq_no DESC fallback — highest sequence number
export const SEQ_SQL = `
  SELECT seq_id, fault_tolerance
  FROM strategy.strategy_identity
  WHERE sec_type = $1 AND code = $2 AND strategy_name = $3
  ORDER BY CASE WHEN is_active THEN 0 ELSE 1 END, seq_no DESC
  LIMIT 1
`;

export const INFO_SQL = `
  SELECT total_buy_cost,
         first_buy_date,
         first_buy_fill_price,
         total_realized_pnl,
         total_abs_pnl,
         n_sells,
         n_buys
  FROM strategy.strategy_results
  WHERE seq_id = $1
`;

export const DECISIONS_SQL = `
  SELECT decision_no, side, exec_date,
         qty, fill_price, normalized_fill_price, normalized_mean_buy_price,
         position_before, position_after, cash_before, cash_after,
         total_qty_before, total_qty_after,
         realized_pnl, slippage, fee, signal_value, signal_reason,
         ft_stressed_conf_up, ft_stressed_conf_down
  FROM strategy.trade_decision
  WHERE seq_id = $1
  ORDER BY decision_no ASC
`;

// Daily portfolio state (one row per trading day from first BUY to end).
// unrealized_pnl = (total_qty/100) * (normalized_close - cost_basis_norm) —
// P&L if all remaining position were sold at the day's close.
// normalized_mean_buy_period = weighted-avg BUY period (calendar days since
// first BUY), weighted on remaining qty — the mean buy time.
export const DAILY_SQL = `
  SELECT trade_date, unrealized_pnl, total_pnl, realized_pnl_cum,
         total_qty, position_value, normalized_mean_buy_period,
         return_rate,
         sharpe_ratio, sharpe_ratio_255d, sharpe_ratio_500d
  FROM strategy.strategy_daily
  WHERE seq_id = $1
  ORDER BY trade_date ASC
`;

// ---------------------------------------------------------------------------
//  Mappers
// ---------------------------------------------------------------------------
export function mapDecision(r: TradeDecisionRow): import("./backtest.js").StrategyDecision {
  return {
    decision_no: r.decision_no,
    side: r.side as "BUY" | "SELL",
    exec_date: formatDate(r.exec_date),
    qty: toNum(r.qty) ?? 0,
    fill_price: toNum(r.fill_price) ?? 0,
    normalized_fill_price: toNum(r.normalized_fill_price) ?? 0,
    normalized_mean_buy_price: toNum(r.normalized_mean_buy_price) ?? 0,
    position_before: toNum(r.position_before) ?? 0,
    position_after: toNum(r.position_after) ?? 0,
    cash_before: toNum(r.cash_before) ?? 0,
    cash_after: toNum(r.cash_after) ?? 0,
    total_qty_before: toNum(r.total_qty_before) ?? 0,
    total_qty_after: toNum(r.total_qty_after) ?? 0,
    realized_pnl: toNum(r.realized_pnl) ?? 0,
    slippage: toNum(r.slippage),
    fee: toNum(r.fee),
    signal_value: toNum(r.signal_value),
    signal_reason: r.signal_reason ?? "",
    ft_stressed_conf_up: r.ft_stressed_conf_up !== null && r.ft_stressed_conf_up !== undefined
      ? toNum(r.ft_stressed_conf_up) : null,
    ft_stressed_conf_down: r.ft_stressed_conf_down !== null && r.ft_stressed_conf_down !== undefined
      ? toNum(r.ft_stressed_conf_down) : null,
  };
}

export function mapDaily(r: DailyRow): StrategyDailyRow {
  return {
    trade_date: formatDate(r.trade_date),
    unrealized_pnl: toNum(r.unrealized_pnl) ?? 0,
    total_pnl: toNum(r.total_pnl) ?? 0,
    realized_pnl_cum: toNum(r.realized_pnl_cum) ?? 0,
    total_qty: toNum(r.total_qty) ?? 0,
    position_value: toNum(r.position_value) ?? 0,
    normalized_mean_buy_period: toNum(r.normalized_mean_buy_period) ?? 0,
    return_rate: toNum(r.return_rate) ?? 0,
    sharpe_ratio: toNum(r.sharpe_ratio) ?? 0,
    sharpe_ratio_255d: toNum(r.sharpe_ratio_255d) ?? 0,
    sharpe_ratio_500d: toNum(r.sharpe_ratio_500d) ?? 0,
  };
}
