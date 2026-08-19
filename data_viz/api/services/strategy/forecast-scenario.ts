/**
 * Forecast-only decisions — lightweight endpoint for scenario switching.
 * Returns ONLY the 20 forecast SELL decisions from the child seq + the
 * child's summary (total_realized_pnl, n_sells, etc.). The UI merges these
 * with the CACHED parent backtest (OHLC + actual decisions + actual daily)
 * to avoid a full reload when switching forecast scenarios.
 */
import { queryRows, formatDate, toNum } from "../db.service.js";
import type { MaSpreadSecType } from "../../../shared/types.js";
import {
  DEFAULT_STRATEGY_NAME,
  SEQ_SQL,
  INFO_SQL,
  type TradeDecisionRow,
  type SeqRow,
  type InfoRow,
  mapDecision,
} from "./_shared.js";
import type { StrategyDecision } from "./backtest.js";

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
