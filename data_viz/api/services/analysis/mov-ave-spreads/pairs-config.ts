/**
 * MA-Spread pair configuration — the canonical 9 price pairs + 5 amt pairs
 * + the 6 TYPICAL EMA pairs (2026-10-26 rebase: the EMA vocabulary covers
 * exactly the typical EMA spans {6, 12, 26} — 6 the A-share short, 12/26
 * the classic MACD pair; the SMA-convention windows stay in the Simple-MA
 * section), the pair-label formatter, the DEFAULT chart's DB row type,
 * and the per-window column pickers the pair-series mappers fan out with.
 * Split out of the former monolithic mov-ave-spreads.ts.
 */
import { toNum } from "@api/lib/db";
import type { MovAveSpreadPairKind } from "@shared/types";

// ----------------------------------------------------------------------------
//  Pair configuration — canonical 9 price pairs + 5 amt pairs.
//  Price pairs: ma_short = 0 is the price sentinel; ma_short = 5 uses ma5.
//  gap_column is the detail-table column holding this pair's gap_value.
//
//  Amt pairs: ma_short = -1 is the trading-amount sentinel; ma_long = W
//  selects trading_amt_maW. There is NO pre-computed gap_column for amt
//  pairs — the gap (trading_amount vs trading_amt_maW) is computed
//  client-side at response-build time (simple division). The 5 amt pairs
//  mirror the Price/MA row (5 columns: Amt/MA5 … Amt/MA255) and are
//  shown as a separate row of chips beneath the 9 price pairs when the
//  trading-amt toggle is ON.
// ----------------------------------------------------------------------------
export type PairSpec = [ma_short: number, ma_long: number, gap_column: string];

export const PAIR_ORDER: PairSpec[] = [
  [0, 5,   "price_vs_ma5"],
  [0, 20,  "price_vs_ma20"],
  [0, 60,  "price_vs_ma60"],
  [0, 120, "price_vs_ma120"],
  [0, 255, "price_vs_ma255"],
  [5, 20,  "ma5_vs_ma20"],
  [5, 60,  "ma5_vs_ma60"],
  [5, 120, "ma5_vs_ma120"],
  [5, 255, "ma5_vs_ma255"],
];

/** 5 trading-amount pairs (Amt/MA5 … Amt/MA255). ma_short = -1 is the
 *  trading-amount sentinel. gap_column is "" — amt-pair gap_value is
 *  computed at response-build time, not read from a detail column. */
export const AMT_PAIR_ORDER: PairSpec[] = [
  [-1, 5,   ""],
  [-1, 20,  ""],
  [-1, 60,  ""],
  [-1, 120, ""],
  [-1, 255, ""],
];

/** 6 TYPICAL Exponential MA pairs (EMA). gap_column is the EMA detail table
 *  column. 3 Price/EMA pairs (ma_short=0) + 3 EMA/EMA pairs over the
 *  typical EMA spans {6, 12, 26} — the classic 12/26 spread IS the MACD
 *  line's components. Source: analysis.mov_ave_spreads_detail_ema. */
export const EMA_PAIR_ORDER: PairSpec[] = [
  [0, 6,   "price_vs_ema6"],
  [0, 12,  "price_vs_ema12"],
  [0, 26,  "price_vs_ema26"],
  [6, 12,  "ema6_vs_ema12"],
  [6, 26,  "ema6_vs_ema26"],
  [12, 26, "ema12_vs_ema26"],
];

/** Build the display label for a (ma_short, ma_long) pair.
 *  ma_short = 0 → "Price/MA{long}" or "Price/EMA{long}" (price pair)
 *  ma_short = -1 → "Amt/MA{long}" (trading-amount pair)
 *  else → "MA{short}/MA{long}" or "EMA{short}/EMA{long}" (MA/MA pair)
 *  kind = "ema" switches the label prefix from MA to EMA. */
export function pairLabel(maShort: number, maLong: number, kind: MovAveSpreadPairKind = "price"): string {
  if (kind === "ema") {
    if (maShort === 0) return `Price/EMA${maLong}`;
    return `EMA${maShort}/EMA${maLong}`;
  }
  if (maShort === 0) return `Price/MA${maLong}`;
  if (maShort === -1) return `Amt/MA${maLong}`;
  return `MA${maShort}/MA${maLong}`;
}

// ----------------------------------------------------------------------------
//  DB row types — DEFAULT chart query (9 Simple-MA + 6 typical EMA pair
//  series +
//  the ONE shared per-date tooltip-metrics array; every other metric group
//  is served by getMovAveSpreadChartExtras on demand).
// ----------------------------------------------------------------------------
export interface DbChartRow {
  date: Date | string;
  price: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  trading_amount: number | null;
  ma5: number | null;
  ma20: number | null;
  ma60: number | null;
  ma120: number | null;
  ma255: number | null;
  // 9 gap columns from the detail row.
  price_vs_ma5: number | null;
  price_vs_ma20: number | null;
  price_vs_ma60: number | null;
  price_vs_ma120: number | null;
  price_vs_ma255: number | null;
  ma5_vs_ma20: number | null;
  ma5_vs_ma60: number | null;
  ma5_vs_ma120: number | null;
  ma5_vs_ma255: number | null;
  // Derivative columns: price (the 1-day leg) + the persisted MA windows
  // 5/20 — direct diffs of the series lines (the 60/120/255-day
  // derivatives were dropped across 2026-09).
  price_slope: number | null;
  ma5_slope: number | null;
  ma20_slope: number | null;
  ma5_curvature: number | null;
  ma20_curvature: number | null;
  price_curvature: number | null;
  // 5 rolling population σ columns (Bollinger band widths) from the detail row.
  std_5days: number | null;
  std_20days: number | null;
  std_60days: number | null;
  std_120days: number | null;
  std_255days: number | null;
  // 3 TYPICAL EMA value columns from stats.{sec_type}_tech_stats (alias `t`).
  ema6: number | null;
  ema12: number | null;
  ema26: number | null;
  // 6 EMA gap columns from analysis.mov_ave_spreads_detail_ema (alias `ema`).
  price_vs_ema6: number | null;
  price_vs_ema12: number | null;
  price_vs_ema26: number | null;
  ema6_vs_ema12: number | null;
  ema6_vs_ema26: number | null;
  ema12_vs_ema26: number | null;
  // EMA slope + EMA curvature columns from the EMA detail table — the
  // persisted EMA windows 6/12, the pair set's two shortest legs (direct
  // diffs of the EMA lines; the 26 leg carries none).
  ema6_slope: number | null;
  ema12_slope: number | null;
  ema6_curvature: number | null;
  ema12_curvature: number | null;
  // 3 rolling population σ columns of price (Bollinger band widths) from the
  // EMA detail table (alias `ema`), aliased as ema_std_*days — the long_std
  // source for Price/EMA pair Bollinger envelopes over the typical windows.
  ema_std_5days: number | null;
  ema_std_12days: number | null;
  ema_std_26days: number | null;
  // Wilder RSI columns (0..100) from analysis.mov_ave_rsi — shared tooltip metrics.
  rsi_3days: number | null;
  rsi_6days: number | null;
  rsi_10days: number | null;
  rsi_14days: number | null;
  rsi_20days: number | null;
  rsi_60days: number | null;
  // Tail-episode population flags per window (1 typical / 0 re-entry /
  // null out-of-band — analysis.mov_ave_rsi, steps/rsi_typical.py).
  is_typical_3days: number | null;
  is_typical_6days: number | null;
  is_typical_10days: number | null;
  is_typical_14days: number | null;
  is_typical_20days: number | null;
  is_typical_60days: number | null;
  // 5 trading-amount MA SLOPE + 5 MARKET-SHARE columns (shared tooltip metrics).
  trading_amt_ma5_slope: number | null;
  trading_amt_ma20_slope: number | null;
  trading_amt_ma60_slope: number | null;
  trading_amt_ma120_slope: number | null;
  trading_amt_ma255_slope: number | null;
  trading_amt_market_share_ma5: number | null;
  trading_amt_market_share_ma20: number | null;
  trading_amt_market_share_ma60: number | null;
  trading_amt_market_share_ma120: number | null;
  trading_amt_market_share_ma255: number | null;
  // Rolling OHLC of the pair-matching windows (open/high/low per window)
  // from the OHLC pivot (alias `sh`) — shared tooltip metrics. Only the 4
  // windows a pair's ma_long can take (20/60/120/255); the full 7-window
  // extrema (anchors + line slopes) load on demand via the "ohlc" extras group.
  sh_open_20d: number | null;
  sh_high_20d: number | null;
  sh_low_20d: number | null;
  sh_open_60d: number | null;
  sh_high_60d: number | null;
  sh_low_60d: number | null;
  sh_open_120d: number | null;
  sh_high_120d: number | null;
  sh_low_120d: number | null;
  sh_open_255d: number | null;
  sh_high_255d: number | null;
  sh_low_255d: number | null;
  [column: string]: unknown;
}

/** Pick the long-MA value for a chart row given the ma_long window. */
export function pickLong(r: DbChartRow, maLong: number): number | null {
  switch (maLong) {
    case 5:   return toNum(r.ma5);
    case 20:  return toNum(r.ma20);
    case 60:  return toNum(r.ma60);
    case 120: return toNum(r.ma120);
    case 255: return toNum(r.ma255);
    default:  return null;
  }
}

/** Pick the slope (1st derivative) of MA{window} from a chart row — a
 *  direct diff of the MA line (MA{W}[t] - MA{W}[t-1]). Only the
 *  persisted windows 5/20 return a value; 60/120/255 return null (their
 *  derivative columns were dropped across 2026-09). */
export function pickSlope(r: DbChartRow, window: number): number | null {
  switch (window) {
    case 5:   return toNum(r.ma5_slope);
    case 20:  return toNum(r.ma20_slope);
    default:  return null;
  }
}

/** Pick the curvature (2nd derivative) of MA{window} from a chart row.
 *  Only the persisted windows 5/20 return a value (see pickSlope). */
export function pickCurvature(r: DbChartRow, window: number): number | null {
  switch (window) {
    case 5:   return toNum(r.ma5_curvature);
    case 20:  return toNum(r.ma20_curvature);
    default:  return null;
  }
}

/** Pick the rolling population σ (Bollinger band width) for the given
 *  MA window from a chart row. The σ columns are stored on the detail row
 *  as std_{W}days (W = window). Used to draw the ±k×σ envelope around the
 *  long MA on Price/MA pair charts. */
export function pickStd(r: DbChartRow, window: number): number | null {
  switch (window) {
    case 5:   return toNum(r.std_5days);
    case 20:  return toNum(r.std_20days);
    case 60:  return toNum(r.std_60days);
    case 120: return toNum(r.std_120days);
    case 255: return toNum(r.std_255days);
    default:  return null;
  }
}

/** Pick the long EMA value for a chart row given the ma_long window.
 *  EMA values come from stats.{sec_type}_tech_stats (aliased as `t` in
 *  the chart SQL). Windows: the typical 6/12/26. */
export function pickEmaLong(r: DbChartRow, maLong: number): number | null {
  switch (maLong) {
    case 6:   return toNum(r.ema6);
    case 12:  return toNum(r.ema12);
    case 26:  return toNum(r.ema26);
    default:  return null;
  }
}

/** Pick the EMA slope (1st derivative) for the given window from a chart
 *  row — a direct diff of the EMA line (EMA{W}[t] - EMA{W}[t-1]), from
 *  analysis.mov_ave_spreads_detail_ema. Only the persisted windows 6/12
 *  (the pair set's two shortest legs) return a value; 26 returns null. */
export function pickEmaSlope(r: DbChartRow, window: number): number | null {
  switch (window) {
    case 6:   return toNum(r.ema6_slope);
    case 12:  return toNum(r.ema12_slope);
    default:  return null;
  }
}

/** Pick the EMA curvature (2nd derivative) for the given window from a
 *  chart row. Only the persisted windows 6/12 return a value. */
export function pickEmaCurvature(r: DbChartRow, window: number): number | null {
  switch (window) {
    case 6:   return toNum(r.ema6_curvature);
    case 12:  return toNum(r.ema12_curvature);
    default:  return null;
  }
}

/** Pick the rolling population σ (Bollinger band width) for the given EMA
 *  window from a chart row. The σ columns are stored on the EMA detail row
 *  as ema_std_{W}days (aliased from ema.std_{W}days in the chart SQL).
 *
 *  Window mapping: EMA6 → ema_std_5days (5-day σ, closest available window
 *  to 6), EMA12 → ema_std_12days, EMA26 → ema_std_26days. Same source
 *  data as the SMA detail table's std_*days (σ of price over W days, ddof=0).
 *  Used to draw the ±k×σ envelope around the long EMA on Price/EMA charts. */
export function pickEmaStd(r: DbChartRow, window: number): number | null {
  switch (window) {
    case 6:   return toNum(r.ema_std_5days);
    case 12:  return toNum(r.ema_std_12days);
    case 26:  return toNum(r.ema_std_26days);
    default:  return null;
  }
}
