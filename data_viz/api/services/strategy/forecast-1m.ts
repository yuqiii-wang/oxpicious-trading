/**
 * 1-month forward sell-confidence forecast — reads pre-computed rows from
 * strategy.forecast_1m + forecast_1m_stats (computed by
 * `python -m strategy._1m_forcast`). The forecast replaces the single
 * last-day FINAL LIQUIDATION SELL with a 20-trading-day SELL confidence
 * schedule (8 mirror/flip/random curves + 1 computed mean that drives the
 * persisted trade_decision rows).
 */
import { queryRows, formatDate, toNum } from "../db.service.js";
import type {
  MaSpreadSecType,
  StrategyForecast1mRow,
  StrategyForecast1mStats,
  StrategyForecast1mResponse,
} from "../../../shared/types.js";
import {
  DEFAULT_STRATEGY_NAME,
  SEQ_SQL,
  type SeqRow,
} from "./_shared.js";

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
