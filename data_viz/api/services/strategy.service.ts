/**
 * Strategy service — reads MA-spread backtest results from the DB.
 *
 * The backtest itself is run by the Python package
 * `strategy.ma_spread_trading` (python -m strategy.ma_spread_trading), which
 * writes results to `strategy.strategy_seq` + `strategy.trade_decision`.
 *
 * This service reads those pre-computed results and pairs them with the
 * OHLC / MA / trading-amount series from the mov-ave-spreads API so the
 * UI can render the chart + B/S markers + decision table.
 *
 * If no backtest run exists for the requested (code, sec_type), an empty
 * decisions array is returned (the UI shows an "info" alert).
 */
import { getMovAveSpreadChart } from "./analysis/mov-ave-spreads.js";
import { queryRows, formatDate, toNum } from "../lib/db.js";
import type {
  MaSpreadSecType,
  StrategyRiskSeq,
  StrategyRiskPeriod,
  StrategyRiskResponse,
} from "../../shared/types.js";

// ---------------------------------------------------------------------------
//  Response types (kept identical to the previous on-the-fly version so the
//  API contract / frontend types don't change)
// ---------------------------------------------------------------------------
export interface StrategyDecision {
  decision_no: number;
  side: "BUY" | "SELL";
  signal_date: string;
  exec_date: string;
  qty: number;
  fill_price: number;
  gross_value: number;
  commission: number;
  fees: number;
  position_before: number;
  position_after: number;
  cash_before: number;
  cash_after: number;
  realized_pnl: number;
  signal_value: number | null;
  signal_reason: string;
}

export interface StrategyOhlcRow {
  date: string;
  open: number | null;
  close: number | null;
  high: number | null;
  low: number | null;
  trading_amount: number | null;
  ma5: number | null;
  ma60: number | null;
}

export interface StrategyBacktestResponse {
  code: string;
  name: string;
  sec_type: MaSpreadSecType;
  ohlc: StrategyOhlcRow[];
  decisions: StrategyDecision[];
  summary: {
    n_buys: number;
    n_sells: number;
    realized_pnl: number;
    final_cash: number;
    total_return_pct: number;
    total_buy_cost: number;
  };
}

// ---------------------------------------------------------------------------
//  DB row types
// ---------------------------------------------------------------------------
interface TradeDecisionRow {
  decision_no: number;
  side: string;
  signal_date: string;
  exec_date: string | null;
  qty: string | number;
  fill_price: string | number | null;
  gross_value: string | number | null;
  commission: string | number;
  fees: string | number;
  position_before: string | number;
  position_after: string | number;
  cash_before: string | number;
  cash_after: string | number;
  realized_pnl: string | number;
  signal_value: string | number | null;
  signal_reason: string | null;
}

interface SeqRow {
  seq_id: number;
  total_buy_cost: string | number | null;
}

// ---------------------------------------------------------------------------
//  SQL — fetch the latest strategy_seq for a given code + sec_type (strategy_seq
//  is per-code now, so a direct filter on strategy_seq suffices — no JOIN to
//  trade_decision needed). Then all trade_decision rows in that seq (no code
//  filter needed — the seq is already per-code).
// ---------------------------------------------------------------------------
const SEQ_SQL = `
  SELECT seq_id, total_buy_cost
  FROM strategy.strategy_seq
  WHERE sec_type = $1 AND code = $2
  ORDER BY seq_no DESC
  LIMIT 1
`;

const DECISIONS_SQL = `
  SELECT decision_no, side, signal_date, exec_date,
         qty, fill_price, gross_value, commission, fees,
         position_before, position_after, cash_before, cash_after,
         realized_pnl, signal_value, signal_reason
  FROM strategy.trade_decision
  WHERE seq_id = $1
  ORDER BY decision_no ASC
`;

// ---------------------------------------------------------------------------
//  Helpers
// ---------------------------------------------------------------------------
function mapDecision(r: TradeDecisionRow): StrategyDecision {
  return {
    decision_no: r.decision_no,
    side: r.side as "BUY" | "SELL",
    signal_date: formatDate(r.signal_date),
    exec_date: formatDate(r.exec_date),
    qty: toNum(r.qty) ?? 0,
    fill_price: toNum(r.fill_price) ?? 0,
    gross_value: toNum(r.gross_value) ?? 0,
    commission: toNum(r.commission) ?? 0,
    fees: toNum(r.fees) ?? 0,
    position_before: toNum(r.position_before) ?? 0,
    position_after: toNum(r.position_after) ?? 0,
    cash_before: toNum(r.cash_before) ?? 0,
    cash_after: toNum(r.cash_after) ?? 0,
    realized_pnl: toNum(r.realized_pnl) ?? 0,
    signal_value: toNum(r.signal_value),
    signal_reason: r.signal_reason ?? "",
  };
}

// ---------------------------------------------------------------------------
//  Main entry — reads pre-computed backtest from DB + OHLC from mov-ave-spreads
// ---------------------------------------------------------------------------
export async function runMaSpreadBacktest(
  rawCode: string,
  rawSecType: string | undefined | null,
): Promise<StrategyBacktestResponse> {
  const secType = (rawSecType as MaSpreadSecType) ?? "index";

  // 1. Fetch OHLC + MA data from the mov-ave-spreads service (for the chart).
  const chart = await getMovAveSpreadChart(rawCode, rawSecType);

  // Build the OHLC output array from the Price/MA60 pair (short_value = close).
  const pricePair = chart.pairs.find(
    (p) => p.ma_short === 0 && p.ma_long === 60,
  );
  const ma5Pair = chart.pairs.find(
    (p) => p.ma_short === 5 && p.ma_long === 60,
  );

  const ohlc: StrategyOhlcRow[] = (pricePair?.rows ?? []).map((r, i) => ({
    date: r.date,
    open: r.open ?? null,
    close: r.short_value ?? null,
    high: r.high ?? null,
    low: r.low ?? null,
    trading_amount: r.trading_amount ?? null,
    ma5: ma5Pair?.rows[i]?.short_value ?? null,
    ma60: r.long_value ?? null,
  }));

  // 2. Fetch the latest strategy_seq for this (sec_type, code) from the DB.
  const seqRows = await queryRows<SeqRow>(SEQ_SQL, [secType, rawCode]);

  // No backtest run found — return chart-only response with empty decisions.
  if (seqRows.length === 0) {
    return {
      code: chart.code,
      name: chart.name,
      sec_type: secType,
      ohlc,
      decisions: [],
      summary: {
        n_buys: 0, n_sells: 0, realized_pnl: 0,
        final_cash: 0, total_return_pct: 0, total_buy_cost: 0,
      },
    };
  }

  const { seq_id, total_buy_cost: seqBuyCost } = seqRows[0];
  const totalBuyCost = toNum(seqBuyCost) ?? 0;

  // 3. Fetch trade_decision rows for this seq (seq is per-code, so no code
  //    filter needed).
  const decisionRows = await queryRows<TradeDecisionRow>(
    DECISIONS_SQL, [seq_id],
  );
  const decisions = decisionRows.map(mapDecision);

  // 4. Compute summary from the decisions.
  const nBuys = decisions.filter((d) => d.side === "BUY").length;
  const nSells = decisions.filter((d) => d.side === "SELL").length;
  const realizedPnl = decisions
    .filter((d) => d.side === "SELL")
    .reduce((sum, d) => sum + d.realized_pnl, 0);
  // final_cash = cash_after of the last decision (cash starts at 0; goes
  // negative on BUY = borrowing, comes back on SELL). Can be negative if
  // the strategy is still invested.
  const finalCash = decisions.length > 0
    ? decisions[decisions.length - 1].cash_after
    : 0;
  // Total Return = final_cash / total_buy_cost (percentage return on total
  // invested). When all positions are closed (final liquidation), final_cash
  // = realized_pnl, so this equals realized_pnl / total_buy_cost.
  const totalReturnPct = totalBuyCost > 0
    ? (finalCash / totalBuyCost) * 100
    : 0;

  return {
    code: chart.code,
    name: chart.name,
    sec_type: secType,
    ohlc,
    decisions,
    summary: {
      n_buys: nBuys,
      n_sells: nSells,
      realized_pnl: Math.round(realizedPnl * 100) / 100,
      final_cash: Math.round(finalCash * 100) / 100,
      total_return_pct: Math.round(totalReturnPct * 100) / 100,
      total_buy_cost: Math.round(totalBuyCost * 100) / 100,
    },
  };
}

// ===========================================================================
//  Risk metrics — reads pre-computed risk rows from strategy.strategy_risk_seq
//  + strategy.strategy_risk_period (computed by `python -m strategy._risks`).
// ===========================================================================

interface RiskSeqRow {
  seq_id: number;
  code: string;
  total_realized_pnl: string | number;
  total_abs_pnl: string | number;
  n_sells: number;
  n_buys: number;
  top_gain_pnl: string | number | null;
  top_gain_exec_date: string | null;
  top_gain_signal_reason: string | null;
  top_loss_pnl: string | number | null;
  top_loss_exec_date: string | null;
  top_loss_signal_reason: string | null;
  max_30d_abs_pnl: string | number | null;
  concentration_ratio: string | number | null;
  concentration_window_start: string | null;
  concentration_window_end: string | null;
  max_drawdown: string | number | null;
  risk_score: string | number | null;
  risk_grade: string | null;
  deepest_drop_since_unzero_pos: string | number | null;
  deepest_drop_since_unzero_pos_peak_date: string | null;
  deepest_drop_since_unzero_pos_trough_date: string | null;
  deepest_drop_since_last_buy: string | number | null;
  deepest_drop_since_last_buy_peak_date: string | null;
  deepest_drop_since_last_buy_trough_date: string | null;
}

interface RiskPeriodRow {
  seq_id: number;
  code: string;
  period_type: string;
  period_value: string;
  n_sells: number;
  n_buys: number;
  realized_pnl: string | number;
  abs_pnl: string | number;
  period_share: string | number | null;
  top_gain_pnl: string | number | null;
  top_gain_exec_date: string | null;
  top_loss_pnl: string | number | null;
  top_loss_exec_date: string | null;
  is_concentration_hotspot: boolean;
  is_counter_trend: boolean;
}

const RISK_SEQ_SQL = `
  SELECT r.seq_id, r.code,
         r.total_realized_pnl, r.total_abs_pnl, r.n_sells, r.n_buys,
         r.top_gain_pnl, r.top_gain_exec_date, r.top_gain_signal_reason,
         r.top_loss_pnl, r.top_loss_exec_date, r.top_loss_signal_reason,
         r.max_30d_abs_pnl, r.concentration_ratio,
         r.concentration_window_start, r.concentration_window_end,
         r.max_drawdown, r.risk_score, r.risk_grade,
         r.deepest_drop_since_unzero_pos,
         r.deepest_drop_since_unzero_pos_peak_date,
         r.deepest_drop_since_unzero_pos_trough_date,
         r.deepest_drop_since_last_buy,
         r.deepest_drop_since_last_buy_peak_date,
         r.deepest_drop_since_last_buy_trough_date
  FROM strategy.strategy_risk_seq r
  JOIN strategy.strategy_seq s ON s.seq_id = r.seq_id
  WHERE s.sec_type = $1 AND r.code = $2
  ORDER BY s.seq_no DESC
  LIMIT 1
`;

const RISK_PERIODS_SQL = `
  SELECT p.seq_id, p.code, p.period_type, p.period_value,
         p.n_sells, p.n_buys, p.realized_pnl, p.abs_pnl, p.period_share,
         p.top_gain_pnl, p.top_gain_exec_date,
         p.top_loss_pnl, p.top_loss_exec_date,
         p.is_concentration_hotspot, p.is_counter_trend
  FROM strategy.strategy_risk_period p
  JOIN strategy.strategy_seq s ON s.seq_id = p.seq_id
  WHERE s.sec_type = $1 AND p.code = $2 AND s.seq_id = $3
  ORDER BY p.period_type, p.period_value
`;

function mapRiskSeq(r: RiskSeqRow): StrategyRiskSeq {
  return {
    seq_id: r.seq_id,
    code: r.code,
    total_realized_pnl: toNum(r.total_realized_pnl) ?? 0,
    total_abs_pnl: toNum(r.total_abs_pnl) ?? 0,
    n_sells: r.n_sells,
    n_buys: r.n_buys,
    top_gain_pnl: toNum(r.top_gain_pnl),
    top_gain_exec_date: r.top_gain_exec_date ? formatDate(r.top_gain_exec_date) : null,
    top_gain_signal_reason: r.top_gain_signal_reason,
    top_loss_pnl: toNum(r.top_loss_pnl),
    top_loss_exec_date: r.top_loss_exec_date ? formatDate(r.top_loss_exec_date) : null,
    top_loss_signal_reason: r.top_loss_signal_reason,
    max_30d_abs_pnl: toNum(r.max_30d_abs_pnl),
    concentration_ratio: toNum(r.concentration_ratio),
    concentration_window_start: r.concentration_window_start ? formatDate(r.concentration_window_start) : null,
    concentration_window_end: r.concentration_window_end ? formatDate(r.concentration_window_end) : null,
    max_drawdown: toNum(r.max_drawdown),
    risk_score: toNum(r.risk_score),
    risk_grade: (r.risk_grade as StrategyRiskSeq["risk_grade"]) ?? null,
    deepest_drop_since_unzero_pos: toNum(r.deepest_drop_since_unzero_pos),
    deepest_drop_since_unzero_pos_peak_date: r.deepest_drop_since_unzero_pos_peak_date ? formatDate(r.deepest_drop_since_unzero_pos_peak_date) : null,
    deepest_drop_since_unzero_pos_trough_date: r.deepest_drop_since_unzero_pos_trough_date ? formatDate(r.deepest_drop_since_unzero_pos_trough_date) : null,
    deepest_drop_since_last_buy: toNum(r.deepest_drop_since_last_buy),
    deepest_drop_since_last_buy_peak_date: r.deepest_drop_since_last_buy_peak_date ? formatDate(r.deepest_drop_since_last_buy_peak_date) : null,
    deepest_drop_since_last_buy_trough_date: r.deepest_drop_since_last_buy_trough_date ? formatDate(r.deepest_drop_since_last_buy_trough_date) : null,
  };
}

function mapRiskPeriod(r: RiskPeriodRow): StrategyRiskPeriod {
  return {
    seq_id: r.seq_id,
    code: r.code,
    period_type: r.period_type as StrategyRiskPeriod["period_type"],
    period_value: r.period_value,
    n_sells: r.n_sells,
    n_buys: r.n_buys,
    realized_pnl: toNum(r.realized_pnl) ?? 0,
    abs_pnl: toNum(r.abs_pnl) ?? 0,
    period_share: toNum(r.period_share),
    top_gain_pnl: toNum(r.top_gain_pnl),
    top_gain_exec_date: r.top_gain_exec_date ? formatDate(r.top_gain_exec_date) : null,
    top_loss_pnl: toNum(r.top_loss_pnl),
    top_loss_exec_date: r.top_loss_exec_date ? formatDate(r.top_loss_exec_date) : null,
    is_concentration_hotspot: r.is_concentration_hotspot,
    is_counter_trend: r.is_counter_trend,
  };
}

export async function fetchStrategyRisks(
  rawCode: string,
  rawSecType: string | undefined | null,
): Promise<StrategyRiskResponse> {
  const secType = (rawSecType as MaSpreadSecType) ?? "index";

  // 1. Fetch the latest risk_seq row for this (sec_type, code).
  const seqRows = await queryRows<RiskSeqRow>(RISK_SEQ_SQL, [secType, rawCode]);

  if (seqRows.length === 0) {
    return { code: rawCode, sec_type: secType, risk_seq: null, periods: [] };
  }

  const riskSeq = mapRiskSeq(seqRows[0]);

  // 2. Fetch all period rows for the same seq_id.
  const periodRows = await queryRows<RiskPeriodRow>(
    RISK_PERIODS_SQL, [secType, rawCode, riskSeq.seq_id],
  );
  const periods = periodRows.map(mapRiskPeriod);

  return {
    code: rawCode,
    sec_type: secType,
    risk_seq: riskSeq,
    periods,
  };
}
