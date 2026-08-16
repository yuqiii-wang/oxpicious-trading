/**
 * Strategy service — reads MA-spread backtest results from the DB.
 *
 * The backtest itself is run by the Python package
 * `strategy.singleton_trading` (python -m strategy.singleton_trading), which
 * writes results to:
 *   - strategy.strategy_identity (pure identity: one row per (strategy, code) run)
 *   - strategy.strategy_results  (1:1 with strategy_identity: run RESULTS — dates,
 *     total_buy_cost, the first-buy normalization anchor, P&L summary)
 *   - strategy.trade_decision (ordered decisions; each carries
 *     normalized_fill_price = fill_price / first_buy_fill_price * 100, so the
 *     first BUY reads as 100 and later fills as % change from entry)
 *
 * This service reads those pre-computed results and pairs them with the
 * OHLC / MA / trading-amount series from the mov-ave-spreads API so the
 * UI can render the chart + B/S markers + decision table. The chart also
 * rebases its OHLC/MA series off strategy_results.first_buy_fill_price so the
 * whole plot is in the same base-100 frame as the markers.
 *
 * If no backtest run exists for the requested (code, sec_type), an empty
 * decisions array is returned (the UI shows an "info" alert).
 */
import { getMovAveSpreadChart } from "./analysis/mov-ave-spreads.js";
import { queryRows, formatDate, toNum } from "./db.service.js";
import { runPythonModule, type RunScriptResult } from "./py-runner.service.js";
import type {
  MaSpreadSecType,
  StrategyRiskSeq,
  StrategyRiskPeriod,
  StrategyRiskResponse,
  StrategyRiskFactor,
  StrategyDailyRow,
  StrategyForecast1mRow,
  StrategyForecast1mStats,
  StrategyForecast1mResponse,
} from "../../shared/types.js";

// ---------------------------------------------------------------------------
//  Response types
// ---------------------------------------------------------------------------
export interface StrategyDecision {
  decision_no: number;
  side: "BUY" | "SELL";
  exec_date: string;
  qty: number;
  fill_price: number;
  /** fill_price rebased to 100 at the first BUY fill (= fill_price /
   *  strategy_results.first_buy_fill_price * 100). First BUY = 100; later fills
   *  read as % change from entry (105 = +5%, 94 = -6%). */
  normalized_fill_price: number;
  /** Weighted-avg BUY normalized_fill_price across all historical BUYs still
   *  in the remaining position (cost basis). BUY rows carry the post-BUY
   *  value; SELL rows carry the pre-SELL value used to compute realized_pnl. */
  normalized_mean_buy_price: number;
  position_before: number;
  position_after: number;
  cash_before: number;
  cash_after: number;
  /** Cumulative quantity (in qty/confidence units, NOT /100) before/after
   *  this decision. BUY adds qty (= confidence); SELL subtracts qty_sold
   *  (= (confidence/100) * total_qty_before). */
  total_qty_before: number;
  total_qty_after: number;
  realized_pnl: number;
  /** Slippage = |fill_price - close| / 100: how far the worst-case OHLC fill
   *  deviates from the day's close, normalized to per-100-shares scale.
   *  ≥ 0 for both BUY and SELL. */
  slippage: number | null;
  /** Fee = 0.2% of BUY notional (normalized money). BUY only; 0 for SELL.
   *  Deducted from cash_after on BUY. */
  fee: number | null;
  signal_value: number | null;
  signal_reason: string;
  /** FT stressed confidence when OHLC moved UP. NULL = no FT applied.
   *  0 = trade would be removed under UP stress. >0 = stressed magnitude. */
  ft_stressed_conf_up: number | null;
  /** FT stressed confidence when OHLC moved DOWN. NULL = no FT applied.
   *  0 = trade would be removed under DOWN stress. >0 = stressed magnitude. */
  ft_stressed_conf_down: number | null;
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
  daily: StrategyDailyRow[];
  summary: {
    n_buys: number;
    n_sells: number;
    realized_pnl: number;
    final_cash: number;
    total_return_pct: number;
    total_buy_cost: number;
    /** exec_date of the FIRST BUY decision — the normalization anchor date
     *  (null if the run made no BUY). */
    first_buy_date: string | null;
    /** fill_price of the FIRST BUY decision — the normalization anchor. The
     *  chart rebases OHLC/MA series off this so the first BUY sits at y=100.
     *  null if no BUY. */
    first_buy_fill_price: number | null;
  };
  /** Fault tolerance percentage (0-20) applied to this run. 0 = baseline. */
  fault_tolerance: number;
}

// ---------------------------------------------------------------------------
//  DB row types
// ---------------------------------------------------------------------------
interface TradeDecisionRow {
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

interface SeqRow {
  seq_id: number;
  fault_tolerance: string | number | null;
}

interface InfoRow {
  total_buy_cost: string | number | null;
  first_buy_date: string | null;
  first_buy_fill_price: string | number | null;
  total_realized_pnl: string | number;
  total_abs_pnl: string | number;
  n_sells: number;
  n_buys: number;
}

interface DailyRow {
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
// Algos registered in strategy.factors_and_algos (must match the Python
// ALGO_REGISTRY). The DB strategy_name is EITHER an algo name (binary mode:
// bollinger_bands / macd / ma_spread) OR a portfolio name (mixed mode:
// portfolio:bb*0.5+macd*0.5). Default: macd (binary).
export const STRATEGY_ALGOS = ["bollinger_bands", "macd", "ma_spread"] as const;
export type StrategyAlgo = (typeof STRATEGY_ALGOS)[number];
export const DEFAULT_STRATEGY_NAME = "macd";

// When scenario is NULL → return the PARENT seq (parent_seq_id IS NULL).
// When scenario is provided → return the CHILD seq for that scenario.
// $4 = strategy_name (algo name for binary mode, or portfolio:bb*0.5+macd*0.5 for mixed).
const SEQ_SQL = `
  SELECT seq_id, fault_tolerance
  FROM strategy.strategy_identity
  WHERE sec_type = $1 AND code = $2 AND strategy_name = $4
    AND (($3::text IS NULL AND parent_seq_id IS NULL)
         OR ($3::text IS NOT NULL AND scenario = $3))
  ORDER BY seq_no DESC
  LIMIT 1
`;

const INFO_SQL = `
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

const DECISIONS_SQL = `
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
const DAILY_SQL = `
  SELECT trade_date, unrealized_pnl, total_pnl, realized_pnl_cum,
         total_qty, position_value, normalized_mean_buy_period,
         return_rate,
         sharpe_ratio, sharpe_ratio_255d, sharpe_ratio_500d
  FROM strategy.strategy_daily
  WHERE seq_id = $1
  ORDER BY trade_date ASC
`;

// ---------------------------------------------------------------------------
//  Helpers
// ---------------------------------------------------------------------------
function mapDecision(r: TradeDecisionRow): StrategyDecision {
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

function mapDaily(r: DailyRow): StrategyDailyRow {
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

// ---------------------------------------------------------------------------
//  Main entry — reads pre-computed backtest from DB + OHLC from mov-ave-spreads
// ---------------------------------------------------------------------------
export async function runSingletonBacktest(
  rawCode: string,
  rawSecType: string | undefined | null,
  scenario: string | undefined | null = null,
  strategyName: string = DEFAULT_STRATEGY_NAME,
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
  //    When scenario is provided, fetch the child seq for that scenario;
  //    otherwise fetch the parent seq (parent_seq_id IS NULL).
  const seqRows = await queryRows<SeqRow>(SEQ_SQL, [secType, rawCode, scenario, strategyName]);

  // No backtest run found — return chart-only response with empty decisions.
  if (seqRows.length === 0) {
    return {
      code: chart.code,
      name: chart.name,
      sec_type: secType,
      ohlc,
      decisions: [],
      daily: [],
      summary: {
        n_buys: 0, n_sells: 0, realized_pnl: 0,
        final_cash: 0, total_return_pct: 0, total_buy_cost: 0,
        first_buy_date: null, first_buy_fill_price: null,
      },
      fault_tolerance: 0,
    };
  }

  const { seq_id, fault_tolerance: ftRaw } = seqRows[0];
  const faultTolerance = toNum(ftRaw) ?? 0;

  // 3. Fetch the 1:1 strategy_results row (run RESULTS: total_buy_cost, first-
  //    buy normalization anchor, P&L summary). The summary is sourced from
  //    here rather than recomputed from decisions so it stays consistent
  //    with the Python-written values.
  const infoRows = await queryRows<InfoRow>(INFO_SQL, [seq_id]);
  const info = infoRows[0];
  const totalBuyCost = toNum(info?.total_buy_cost) ?? 0;
  const firstBuyDate = info?.first_buy_date ? formatDate(info.first_buy_date) : null;
  const firstBuyFillPrice = toNum(info?.first_buy_fill_price);
  const nBuys = info?.n_buys ?? 0;
  const nSells = info?.n_sells ?? 0;
  const realizedPnl = toNum(info?.total_realized_pnl) ?? 0;

  // 4. Fetch trade_decision rows for this seq (seq is per-code, so no code
  //    filter needed).
  const decisionRows = await queryRows<TradeDecisionRow>(
    DECISIONS_SQL, [seq_id],
  );
  const decisions = decisionRows.map(mapDecision);

  // 5. Fetch strategy_daily rows (daily portfolio state with unrealized_pnl).
  const dailyRows = await queryRows<DailyRow>(DAILY_SQL, [seq_id]);
  const daily = dailyRows.map(mapDaily);

  // 6. final_cash = cash_after of the last decision (cash starts at 0; goes
  //    negative on BUY = borrowing, comes back on SELL). Can be negative if
  //    the strategy is still invested. Sourced from the last decision row
  //    (not stored on strategy_results).
  const finalCash = decisions.length > 0
    ? decisions[decisions.length - 1].cash_after
    : 0;
  // MTM equity = final_cash + position_value of any open position at the
  // last close. When fully closed (total_qty_after = 0), position_value = 0
  // so equity = final_cash = realized_pnl. When open, equity reflects the
  // unrealized MTM gain/loss — the true portfolio value.
  const lastPositionValue = daily.length > 0
    ? daily[daily.length - 1].position_value
    : 0;
  const finalEquity = finalCash + lastPositionValue;
  // Total Return = MTM equity / total_buy_cost (percentage return on peak
  // capital deployed).
  const totalReturnPct = totalBuyCost > 0
    ? (finalEquity / totalBuyCost) * 100
    : 0;

  return {
    code: chart.code,
    name: chart.name,
    sec_type: secType,
    ohlc,
    decisions,
    daily,
    summary: {
      n_buys: nBuys,
      n_sells: nSells,
      realized_pnl: Math.round(realizedPnl * 100) / 100,
      final_cash: Math.round(finalEquity * 100) / 100,
      total_return_pct: Math.round(totalReturnPct * 100) / 100,
      total_buy_cost: Math.round(totalBuyCost * 100) / 100,
      first_buy_date: firstBuyDate,
      first_buy_fill_price: firstBuyFillPrice,
    },
    fault_tolerance: faultTolerance,
  };
}

// ===========================================================================
//  Risk metrics — reads pre-computed risk rows from strategy.strategy_risks
//  + strategy.strategy_risk_period (computed by `python -m strategy._risks`).
// ===========================================================================

interface RiskSeqRow {
  seq_id: number;
  code: string;
  total_realized_pnl: string | number;
  total_abs_pnl: string | number;
  n_sells: number;
  n_buys: number;
  pnl_gain_1st_decision_no: number | null;
  pnl_gain_2nd_decision_no: number | null;
  pnl_gain_3rd_decision_no: number | null;
  pnl_loss_1st_decision_no: number | null;
  pnl_loss_2nd_decision_no: number | null;
  pnl_loss_3rd_decision_no: number | null;
  confidence_buy_1st_decision_no: number | null;
  confidence_buy_2nd_decision_no: number | null;
  confidence_buy_3rd_decision_no: number | null;
  // Derived via LEFT JOIN to trade_decision (1st gain / 1st loss details)
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
  drawdown_1st_date: string | null;
  drawdown_2nd_date: string | null;
  drawdown_3rd_date: string | null;
  drawdown_1st_val: string | number | null;
  drawdown_2nd_val: string | number | null;
  drawdown_3rd_val: string | number | null;
  risk_score: string | number | null;
  risk_grade: string | null;
  ft_amplified_total_pnl: string | number | null;
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
  ft_amplified_pnl: string | number;
  unrealized_pnl: string | number;
  max_loss_unrealized_pnl: string | number;
  max_gain_unrealized_pnl: string | number;
  end_unrealized_pnl: string | number;
  abs_pnl: string | number;
  period_share: string | number | null;
  is_concentration_hotspot: boolean;
  is_counter_trend: boolean;
}

// When scenario is NULL → return the PARENT seq's risks.
// When scenario is provided → return the CHILD seq's risks for that scenario.
const RISK_SEQ_SQL = `
  SELECT r.seq_id, r.code,
         i.total_realized_pnl, i.total_abs_pnl, i.n_sells, i.n_buys,
         r.pnl_gain_1st_decision_no, r.pnl_gain_2nd_decision_no, r.pnl_gain_3rd_decision_no,
         r.pnl_loss_1st_decision_no, r.pnl_loss_2nd_decision_no, r.pnl_loss_3rd_decision_no,
         r.confidence_buy_1st_decision_no, r.confidence_buy_2nd_decision_no, r.confidence_buy_3rd_decision_no,
         g1.realized_pnl  AS top_gain_pnl,
         g1.exec_date     AS top_gain_exec_date,
         g1.signal_reason AS top_gain_signal_reason,
         l1.realized_pnl  AS top_loss_pnl,
         l1.exec_date     AS top_loss_exec_date,
         l1.signal_reason AS top_loss_signal_reason,
         r.max_30d_abs_pnl, r.concentration_ratio,
         r.concentration_window_start, r.concentration_window_end,
         r.drawdown_1st_date, r.drawdown_2nd_date, r.drawdown_3rd_date,
         r.drawdown_1st_val, r.drawdown_2nd_val, r.drawdown_3rd_val,
         r.risk_score, r.risk_grade,
         r.ft_amplified_total_pnl,
         r.deepest_drop_since_unzero_pos,
         r.deepest_drop_since_unzero_pos_peak_date,
         r.deepest_drop_since_unzero_pos_trough_date,
         r.deepest_drop_since_last_buy,
         r.deepest_drop_since_last_buy_peak_date,
         r.deepest_drop_since_last_buy_trough_date
  FROM strategy.strategy_risks r
  JOIN strategy.strategy_identity s ON s.seq_id = r.seq_id
  JOIN strategy.strategy_results i ON i.seq_id = r.seq_id
  LEFT JOIN strategy.trade_decision g1
    ON g1.seq_id = r.seq_id AND g1.decision_no = r.pnl_gain_1st_decision_no
  LEFT JOIN strategy.trade_decision l1
    ON l1.seq_id = r.seq_id AND l1.decision_no = r.pnl_loss_1st_decision_no
  WHERE s.sec_type = $1 AND r.code = $2 AND s.strategy_name = $4
    AND (($3::text IS NULL AND s.parent_seq_id IS NULL)
         OR ($3::text IS NOT NULL AND s.scenario = $3))
  ORDER BY s.seq_no DESC
  LIMIT 1
`;

const RISK_PERIODS_SQL = `
  SELECT p.seq_id, p.code, p.period_type, p.period_value,
         p.n_sells, p.n_buys, p.realized_pnl, p.ft_amplified_pnl, p.unrealized_pnl,
         p.max_loss_unrealized_pnl, p.max_gain_unrealized_pnl, p.end_unrealized_pnl,
         p.abs_pnl, p.period_share,
         p.is_concentration_hotspot, p.is_counter_trend
  FROM strategy.strategy_risk_period p
  JOIN strategy.strategy_identity s ON s.seq_id = p.seq_id
  WHERE s.sec_type = $1 AND p.code = $2 AND s.seq_id = $3
  ORDER BY p.period_type, p.period_value
`;

interface RiskFactorRow {
  seq_id: number;
  code: string;
  component: string;
  label: string;
  sub_key: string;
  contribution: string | number;
  raw_value: string | number | null;
  threshold: string | number | null;
  ratio: string | number | null;
}

const RISK_FACTORS_SQL = `
  SELECT f.seq_id, f.code, f.component, f.label, f.sub_key,
         f.contribution, f.raw_value, f.threshold, f.ratio
  FROM strategy.strategy_risk_factors f
  WHERE f.seq_id = $1 AND f.code = $2
  ORDER BY
    CASE f.component
      WHEN 'realized' THEN 1
      WHEN 'unrealized' THEN 2
      WHEN 'streak' THEN 3
      WHEN 'period_asymmetry' THEN 4
      WHEN 'period_tail' THEN 5
      ELSE 9
    END,
    f.sub_key
`;

function mapRiskSeq(r: RiskSeqRow): StrategyRiskSeq {
  return {
    seq_id: r.seq_id,
    code: r.code,
    total_realized_pnl: toNum(r.total_realized_pnl) ?? 0,
    total_abs_pnl: toNum(r.total_abs_pnl) ?? 0,
    n_sells: r.n_sells,
    n_buys: r.n_buys,
    pnl_gain_1st_decision_no: r.pnl_gain_1st_decision_no,
    pnl_gain_2nd_decision_no: r.pnl_gain_2nd_decision_no,
    pnl_gain_3rd_decision_no: r.pnl_gain_3rd_decision_no,
    pnl_loss_1st_decision_no: r.pnl_loss_1st_decision_no,
    pnl_loss_2nd_decision_no: r.pnl_loss_2nd_decision_no,
    pnl_loss_3rd_decision_no: r.pnl_loss_3rd_decision_no,
    confidence_buy_1st_decision_no: r.confidence_buy_1st_decision_no,
    confidence_buy_2nd_decision_no: r.confidence_buy_2nd_decision_no,
    confidence_buy_3rd_decision_no: r.confidence_buy_3rd_decision_no,
    // Derived via LEFT JOIN to trade_decision
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
    drawdown_1st_date: r.drawdown_1st_date ? formatDate(r.drawdown_1st_date) : null,
    drawdown_2nd_date: r.drawdown_2nd_date ? formatDate(r.drawdown_2nd_date) : null,
    drawdown_3rd_date: r.drawdown_3rd_date ? formatDate(r.drawdown_3rd_date) : null,
    drawdown_1st_val: toNum(r.drawdown_1st_val),
    drawdown_2nd_val: toNum(r.drawdown_2nd_val),
    drawdown_3rd_val: toNum(r.drawdown_3rd_val),
    risk_score: toNum(r.risk_score),
    risk_grade: (r.risk_grade as StrategyRiskSeq["risk_grade"]) ?? null,
    ft_amplified_total_pnl: toNum(r.ft_amplified_total_pnl),
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
    ft_amplified_pnl: toNum(r.ft_amplified_pnl) ?? 0,
    unrealized_pnl: toNum(r.unrealized_pnl) ?? 0,
    max_loss_unrealized_pnl: toNum(r.max_loss_unrealized_pnl) ?? 0,
    max_gain_unrealized_pnl: toNum(r.max_gain_unrealized_pnl) ?? 0,
    end_unrealized_pnl: toNum(r.end_unrealized_pnl) ?? 0,
    abs_pnl: toNum(r.abs_pnl) ?? 0,
    period_share: toNum(r.period_share),
    is_concentration_hotspot: r.is_concentration_hotspot,
    is_counter_trend: r.is_counter_trend,
  };
}

function mapRiskFactor(r: RiskFactorRow): StrategyRiskFactor {
  return {
    seq_id: r.seq_id,
    code: r.code,
    component: r.component as StrategyRiskFactor["component"],
    label: r.label,
    sub_key: r.sub_key,
    contribution: toNum(r.contribution) ?? 0,
    raw_value: toNum(r.raw_value),
    threshold: toNum(r.threshold),
    ratio: toNum(r.ratio),
  };
}

export async function fetchStrategyRisks(
  rawCode: string,
  rawSecType: string | undefined | null,
  scenario: string | undefined | null = null,
  strategyName: string = DEFAULT_STRATEGY_NAME,
): Promise<StrategyRiskResponse> {
  const secType = (rawSecType as MaSpreadSecType) ?? "index";

  // 1. Fetch the latest risk_seq row for this (sec_type, code).
  //    When scenario is provided, fetch the child seq's risks; otherwise
  //    fetch the parent seq's risks.
  const seqRows = await queryRows<RiskSeqRow>(RISK_SEQ_SQL, [secType, rawCode, scenario, strategyName]);

  if (seqRows.length === 0) {
    return { code: rawCode, sec_type: secType, risk_seq: null, periods: [], risk_factors: [] };
  }

  const riskSeq = mapRiskSeq(seqRows[0]);

  // 2. Fetch all period rows for the same seq_id.
  const periodRows = await queryRows<RiskPeriodRow>(
    RISK_PERIODS_SQL, [secType, rawCode, riskSeq.seq_id],
  );
  const periods = periodRows.map(mapRiskPeriod);

  // 3. Fetch risk score contribution factors for the same seq_id.
  const factorRows = await queryRows<RiskFactorRow>(
    RISK_FACTORS_SQL, [riskSeq.seq_id, rawCode],
  );
  const riskFactors = factorRows.map(mapRiskFactor);

  return {
    code: rawCode,
    sec_type: secType,
    risk_seq: riskSeq,
    periods,
    risk_factors: riskFactors,
  };
}

// ===========================================================================
//  Forecast-only decisions — lightweight endpoint for scenario switching.
//  Returns ONLY the 20 forecast SELL decisions from the child seq + the
//  child's summary (total_realized_pnl, n_sells, etc.). The UI merges these
//  with the CACHED parent backtest (OHLC + actual decisions + actual daily)
//  to avoid a full reload when switching forecast scenarios.
// ===========================================================================

// Fetch ONLY forecast SELL decisions (signal_reason LIKE 'FORECAST SELL%')
// from the child seq for the given scenario. Skips actual decisions (which
// are identical to the parent's and already cached on the client).
const FC_DECISIONS_SQL = `
  SELECT decision_no, side, exec_date,
         qty, fill_price, normalized_fill_price, normalized_mean_buy_price,
         position_before, position_after, cash_before, cash_after,
         total_qty_before, total_qty_after,
         realized_pnl, slippage, fee, signal_value, signal_reason
  FROM strategy.trade_decision
  WHERE seq_id = $1 AND signal_reason LIKE 'FORECAST SELL%'
  ORDER BY decision_no ASC
`;

export interface ForecastScenarioResponse {
  code: string;
  sec_type: MaSpreadSecType;
  scenario: string;
  /** 20 forecast SELL decisions from the child seq. */
  forecast_decisions: StrategyDecision[];
  /** Child seq summary (for the summary chips). */
  summary: {
    n_buys: number;
    n_sells: number;
    realized_pnl: number;
    final_cash: number;
    total_return_pct: number;
    total_buy_cost: number;
    first_buy_date: string | null;
    first_buy_fill_price: number | null;
  };
}

export async function fetchForecastScenarioDecisions(
  rawCode: string,
  rawSecType: string | undefined | null,
  scenario: string,
  strategyName: string = DEFAULT_STRATEGY_NAME,
): Promise<ForecastScenarioResponse | null> {
  const secType = (rawSecType as MaSpreadSecType) ?? "index";

  // Find the child seq for this scenario.
  const seqRows = await queryRows<SeqRow>(SEQ_SQL, [secType, rawCode, scenario, strategyName]);
  if (seqRows.length === 0) return null;

  const { seq_id } = seqRows[0];

  const [infoRows, decisionRows] = await Promise.all([
    queryRows<InfoRow>(INFO_SQL, [seq_id]),
    queryRows<TradeDecisionRow>(FC_DECISIONS_SQL, [seq_id]),
  ]);
  const info = infoRows[0];
  const forecastDecisions = decisionRows.map(mapDecision);

  const totalBuyCost = toNum(info?.total_buy_cost) ?? 0;
  const firstBuyDate = info?.first_buy_date ? formatDate(info.first_buy_date) : null;
  const firstBuyFillPrice = toNum(info?.first_buy_fill_price);
  const nBuys = info?.n_buys ?? 0;
  const nSells = info?.n_sells ?? 0;
  const realizedPnl = toNum(info?.total_realized_pnl) ?? 0;
  const finalCash = forecastDecisions.length > 0
    ? forecastDecisions[forecastDecisions.length - 1].cash_after
    : 0;
  const totalReturnPct = totalBuyCost > 0 ? (finalCash / totalBuyCost) * 100 : 0;

  return {
    code: rawCode,
    sec_type: secType,
    scenario,
    forecast_decisions: forecastDecisions,
    summary: {
      n_buys: nBuys,
      n_sells: nSells,
      realized_pnl: Math.round(realizedPnl * 100) / 100,
      final_cash: Math.round(finalCash * 100) / 100,
      total_return_pct: Math.round(totalReturnPct * 100) / 100,
      total_buy_cost: Math.round(totalBuyCost * 100) / 100,
      first_buy_date: firstBuyDate,
      first_buy_fill_price: firstBuyFillPrice,
    },
  };
}

// ===========================================================================
//  Run strategy script — spawns the Python backtest via the shared py-runner
//  service and waits for it to exit. The frontend calls this when the user
//  clicks "Run Strategy", then reloads data from DB on success.
// ===========================================================================

/** Result of running a strategy script (re-exported from py-runner service). */
export type RunStrategyResult = RunScriptResult;

/**
 * Run the backtest (`strategy.singleton_trading`) for one
 * (sec_type, code). The backtest script also computes + upserts risk metrics
 * internally (via `strategy._risks.compute_and_upsert_risks`), so no separate
 * risk command is needed. Runs with --force so existing seq rows are replaced.
 *
 * When `faultTolerance` is in (0, 20], passes `--fault-tolerance <ft>` to
 * Python, which runs a two-pass stress test: baseline run finds decision
 * dates, then OHLC is adversely perturbed on those dates (BUY up, SELL down)
 * by `ft%` of `|delta_close|`, indicators are recomputed, and the algo
 * re-runs on stressed data. The strategy_name gets an `_ft{N}` suffix so
 * the FT variant is a distinct strategy in the DB.
 *
 * Returns after the process exits. The frontend then invalidates cache and
 * reloads from DB to pick up the fresh results.
 */
export async function runStrategyScript(
  rawCode: string,
  rawSecType: string | undefined | null,
  forecast: boolean = true,
  serializedAlgo: string = DEFAULT_STRATEGY_NAME,
  faultTolerance: number = 0,
): Promise<RunStrategyResult> {
  const secType = (rawSecType as MaSpreadSecType) ?? "index";
  const code = rawCode.trim();
  // serializedAlgo is either an algo name ("macd") or a serialized selection
  // ("bollinger_bands:0.5,macd:0.5") — both are understood by Python's
  // _parse_algo_arg in strategy/singleton_trading/__main__.py.
  const args = ["--algo", serializedAlgo, "--sec-type", secType, "--codes", code, "--force"];
  if (!forecast) args.push("--no-forecast");
  if (faultTolerance && faultTolerance > 0) {
    // Clamp to the supported range (0-20) for safety.
    const ft = Math.max(0, Math.min(20, Number(faultTolerance) || 0));
    if (ft > 0) args.push("--fault-tolerance", String(ft));
  }
  return runPythonModule("strategy.singleton_trading", args);
}

// ===========================================================================
//  1-month forward sell-confidence forecast — reads pre-computed rows from
//  strategy.forecast_1m + forecast_1m_stats (computed by
//  `python -m strategy._1m_forcast`). The forecast replaces the single
//  last-day FINAL LIQUIDATION SELL with a 20-trading-day SELL confidence
//  schedule (8 mirror/flip/random curves + 1 computed mean that drives the
//  persisted trade_decision rows).
// ===========================================================================

interface Forecast1mDbRow {
  scenario: string;
  forecast_day: number;
  open_price: string | number;
  high_price: string | number;
  low_price: string | number;
  close_price: string | number;
  daily_return: string | number;
  trading_amt: string | number | null;
  rsi: string | number | null;
  sell_fraction: string | number;
  sell_confidence: string | number;
  realized_pnl_forecast: string | number;
  scenario_weight: string | number | null;
}

interface Forecast1mStatsDbRow {
  forecast_date: string | Date;
  sigma_daily: string | number;
  sigma_255d: string | number;
  oc_gap_mean: string | number;
  oc_gap_std: string | number;
  hl_gap_mean: string | number;
  hl_gap_std: string | number;
  amt_mean: string | number | null;
  amt_std: string | number | null;
  amt_hl_corr: string | number | null;
  rsi_6: string | number | null;
  rsi_10: string | number | null;
  rsi_14: string | number | null;
  rsi_20: string | number | null;
  anchor_close: string | number;
  first_buy_fill_price: string | number | null;
  last_total_pnl: string | number;
}

const FORECAST_1M_SQL = `
  SELECT scenario, forecast_day,
         open_price, high_price, low_price, close_price, daily_return,
         trading_amt, rsi,
         sell_fraction, sell_confidence, realized_pnl_forecast, scenario_weight
  FROM strategy.forecast_1m
  WHERE seq_id = $1
  ORDER BY scenario, forecast_day
`;

const FORECAST_1M_STATS_SQL = `
  SELECT forecast_date, sigma_daily, sigma_255d,
         oc_gap_mean, oc_gap_std, hl_gap_mean, hl_gap_std,
         amt_mean, amt_std, amt_hl_corr,
         rsi_6, rsi_10, rsi_14, rsi_20,
         anchor_close, first_buy_fill_price, last_total_pnl
  FROM strategy.forecast_1m_stats
  WHERE seq_id = $1
`;

function mapForecastRow(r: Forecast1mDbRow): StrategyForecast1mRow {
  return {
    scenario: r.scenario as StrategyForecast1mRow["scenario"],
    forecast_day: r.forecast_day,
    open_price: toNum(r.open_price) ?? 0,
    high_price: toNum(r.high_price) ?? 0,
    low_price: toNum(r.low_price) ?? 0,
    close_price: toNum(r.close_price) ?? 0,
    daily_return: toNum(r.daily_return) ?? 0,
    trading_amt: toNum(r.trading_amt),
    rsi: toNum(r.rsi),
    sell_fraction: toNum(r.sell_fraction) ?? 0,
    sell_confidence: toNum(r.sell_confidence) ?? 0,
    realized_pnl_forecast: toNum(r.realized_pnl_forecast) ?? 0,
    scenario_weight: toNum(r.scenario_weight),
  };
}

function mapForecastStats(r: Forecast1mStatsDbRow): StrategyForecast1mStats {
  return {
    forecast_date: formatDate(r.forecast_date),
    sigma_daily: toNum(r.sigma_daily) ?? 0,
    sigma_255d: toNum(r.sigma_255d) ?? 0,
    oc_gap_mean: toNum(r.oc_gap_mean) ?? 0,
    oc_gap_std: toNum(r.oc_gap_std) ?? 0,
    hl_gap_mean: toNum(r.hl_gap_mean) ?? 0,
    hl_gap_std: toNum(r.hl_gap_std) ?? 0,
    amt_mean: toNum(r.amt_mean),
    amt_std: toNum(r.amt_std),
    amt_hl_corr: toNum(r.amt_hl_corr),
    rsi_6: toNum(r.rsi_6),
    rsi_10: toNum(r.rsi_10),
    rsi_14: toNum(r.rsi_14),
    rsi_20: toNum(r.rsi_20),
    anchor_close: toNum(r.anchor_close) ?? 0,
    first_buy_fill_price: toNum(r.first_buy_fill_price),
    last_total_pnl: toNum(r.last_total_pnl) ?? 0,
  };
}

/**
 * Read the 1-month forward forecast for the latest singleton_trading run of
 * (code, sec_type). Returns null-typed fields (empty rows + null stats) when
 * no forecast exists for the run — the UI then hides the forecast overlay.
 */
export async function fetchStrategyForecast1m(
  rawCode: string,
  rawSecType: string | undefined | null,
  strategyName: string = DEFAULT_STRATEGY_NAME,
): Promise<StrategyForecast1mResponse> {
  const secType = (rawSecType as MaSpreadSecType) ?? "index";
  const code = rawCode.trim();

  // Reuse the same SEQ_SQL to find the latest PARENT run for this code
  // (forecast_1m rows are attached to the parent seq, not child seqs).
  const seqRows = await queryRows<SeqRow>(SEQ_SQL, [secType, code, null, strategyName]);
  if (seqRows.length === 0) {
    return { code, sec_type: secType, seq_id: 0, forecast_date: "", rows: [], stats: null };
  }
  const { seq_id } = seqRows[0];

  const [forecastRows, statsRows] = await Promise.all([
    queryRows<Forecast1mDbRow>(FORECAST_1M_SQL, [seq_id]),
    queryRows<Forecast1mStatsDbRow>(FORECAST_1M_STATS_SQL, [seq_id]),
  ]);

  return {
    code,
    sec_type: secType,
    seq_id,
    forecast_date: statsRows[0] ? formatDate(statsRows[0].forecast_date) : "",
    rows: forecastRows.map(mapForecastRow),
    stats: statsRows[0] ? mapForecastStats(statsRows[0]) : null,
  };
}
