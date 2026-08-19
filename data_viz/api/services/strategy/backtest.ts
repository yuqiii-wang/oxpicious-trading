/**
 * Singleton backtest — reads pre-computed backtest results from the DB.
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
 * This module reads those pre-computed results and pairs them with the
 * OHLC / MA / trading-amount series from the mov-ave-spreads API so the
 * UI can render the chart + B/S markers + decision table. The chart also
 * rebases its OHLC/MA series off strategy_results.first_buy_fill_price so the
 * whole plot is in the same base-100 frame as the markers.
 *
 * If no backtest run exists for the requested (code, sec_type), an empty
 * decisions array is returned (the UI shows an "info" alert).
 */
import { getMovAveSpreadChart } from "../analysis/mov-ave-spreads.js";
import { queryRows, formatDate, toNum } from "../db.service.js";
import type { MaSpreadSecType, StrategyDailyRow } from "../../../shared/types.js";
import {
  DEFAULT_STRATEGY_NAME,
  SEQ_SQL,
  INFO_SQL,
  DECISIONS_SQL,
  DAILY_SQL,
  type TradeDecisionRow,
  type SeqRow,
  type InfoRow,
  type DailyRow,
  mapDecision,
  mapDaily,
} from "./_shared.js";

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
