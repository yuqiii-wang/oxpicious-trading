/**
 * MA-Spread analysis - listMovAveSpreadCodes + getMovAveSpreadChart.
 * Extracted from the former analysis.service.ts.
 */
import { queryRows, formatDate, toNum } from "../../lib/db.js";
import type { QueryResultRow } from "pg";
import { stripExchangeSuffix, matchesExchange } from "../../lib/classify-etf.js";
import { stripped } from "./_shared.js";
import { buildStrategyThemesFromRows, matchesClassification } from "../_shared.js";
import type {
  MaSpreadSecType,
  MovAveSpreadCodeRow,
  MovAveSpreadCodesResponse,
  MovAveSpreadChartResponse,
  MovAveSpreadDetailRow,
  MovAveSpreadHypeEpisodes,
  MovAveSpreadOhlcRow,
  MovAveSpreadPairSeries,
  MovAveSpreadPairKind,
  MovAveSpreadLatestGap,
  SectorNode,
  IndustryNode,
  StrategyNode,
} from "../../../shared/types.js";

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
type PairSpec = [ma_short: number, ma_long: number, gap_column: string];

const PAIR_ORDER: PairSpec[] = [
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
const AMT_PAIR_ORDER: PairSpec[] = [
  [-1, 5,   ""],
  [-1, 20,  ""],
  [-1, 60,  ""],
  [-1, 120, ""],
  [-1, 255, ""],
];

/** 9 Exponential MA pairs (EMA). gap_column is the EMA detail table column.
 *  Mirrors PAIR_ORDER but for EMAs: 5 Price/EMA pairs (ma_short=0) + 4
 *  EMA6/EMA pairs (ma_short=6). Windows are 6/20/60/120/255 (EMA6 replaces
 *  MA5 — EMAs use 6 instead of 5 as the short window). Source:
 *  analysis.mov_ave_spreads_detail_ema. */
const EMA_PAIR_ORDER: PairSpec[] = [
  [0, 6,   "price_vs_ema6"],
  [0, 20,  "price_vs_ema20"],
  [0, 60,  "price_vs_ema60"],
  [0, 120, "price_vs_ema120"],
  [0, 255, "price_vs_ema255"],
  [6, 20,  "ema6_vs_ema20"],
  [6, 60,  "ema6_vs_ema60"],
  [6, 120, "ema6_vs_ema120"],
  [6, 255, "ema6_vs_ema255"],
];

const VALID_SEC_TYPES: ReadonlySet<MaSpreadSecType> = new Set(["etf", "index", "stock"]);

function normalizeSecType(raw: string | undefined | null): MaSpreadSecType {
  const v = (raw ?? "").trim().toLowerCase();
  if (!VALID_SEC_TYPES.has(v as MaSpreadSecType)) {
    throw new Error(`Invalid sec_type: ${raw!}. Expected 'etf', 'index', or 'stock'.`);
  }
  return v as MaSpreadSecType;
}

// ----------------------------------------------------------------------------
//  DB row types
// ----------------------------------------------------------------------------
interface DbCodeRow extends QueryResultRow {
  code: string;
  name: string;
  first_date: Date | string;
  last_date: Date | string;
  n_dates: number;
  // 9 latest gap columns from the wide detail row at MAX(date).
  price_vs_ma5: number | null;
  price_vs_ma20: number | null;
  price_vs_ma60: number | null;
  price_vs_ma120: number | null;
  price_vs_ma255: number | null;
  ma5_vs_ma20: number | null;
  ma5_vs_ma60: number | null;
  ma5_vs_ma120: number | null;
  ma5_vs_ma255: number | null;
  // All-time max gain / max loss across all 9 pairs (fractional).
  max_gain: number | null;
  max_loss: number | null;
  max_spread: number | null;
}

interface DbChartRow extends QueryResultRow {
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
  // 10 slope/curvature columns from the detail row.
  price_slope: number | null;
  ma5_slope: number | null;
  ma20_slope: number | null;
  ma60_slope: number | null;
  ma120_slope: number | null;
  ma255_slope: number | null;
  ma5_curvature: number | null;
  ma20_curvature: number | null;
  ma60_curvature: number | null;
  ma120_curvature: number | null;
  ma255_curvature: number | null;
  price_curvature: number | null;
  // 5 rolling population σ columns (Bollinger band widths) from the detail row.
  std_5days: number | null;
  std_20days: number | null;
  std_60days: number | null;
  std_120days: number | null;
  std_255days: number | null;
  // Last-extreme columns from analysis.mov_ave_rsi (joined on
  // sec_type + code + date). date_of_last_extreme is a DATE column.
  date_of_last_extreme: Date | string | null;
  gap_since_last_extreme: number | null;
  days_since_last_extreme: number | null;
  // Wilder RSI columns (0..100, NULL until N periods) from
  // analysis.mov_ave_rsi — surfaced in the chart tooltip.
  rsi_6days: number | null;
  rsi_10days: number | null;
  rsi_14days: number | null;
  rsi_20days: number | null;
  // 5 trading-amount MA columns (yuan, NUMERIC(24,4)) from the detail row.
  // Used to render the trading-amt envelope when an Amt/MA pair is selected.
  trading_amt_ma5: number | null;
  trading_amt_ma20: number | null;
  trading_amt_ma60: number | null;
  trading_amt_ma120: number | null;
  trading_amt_ma255: number | null;
  // 5 trading-amount MA SLOPE columns (fractional daily change, NUMERIC(10,4))
  // from the detail row. Surfaced in the chart tooltip when trading-amt
  // display is enabled.
  trading_amt_ma5_slope: number | null;
  trading_amt_ma20_slope: number | null;
  trading_amt_ma60_slope: number | null;
  trading_amt_ma120_slope: number | null;
  trading_amt_ma255_slope: number | null;
  // 5 trading-amount MARKET-SHARE MA columns (dimensionless ratio 0..1,
  // NUMERIC(24,4)) from the detail row. Surfaced in the chart tooltip as a
  // percentage when trading-amt display is enabled.
  trading_amt_market_share_ma5: number | null;
  trading_amt_market_share_ma20: number | null;
  trading_amt_market_share_ma60: number | null;
  trading_amt_market_share_ma120: number | null;
  trading_amt_market_share_ma255: number | null;
  // 5 trading-amount Bollinger band σ columns (yuan, NUMERIC(24,4)) from
  // analysis.mov_ave_trading_amt. Rolling population σ (ddof=0) of
  // trading_amt_maW over W days. Used to draw Bollinger-style envelopes
  // (MA ± k×σ) around each trading-amount MA line on Amt/MA pair charts.
  trading_amt_std5: number | null;
  trading_amt_std20: number | null;
  trading_amt_std60: number | null;
  trading_amt_std120: number | null;
  trading_amt_std255: number | null;
  // 5 EMA value columns from stats.{sec_type}_tech_stats (alias `t`).
  // Used to render EMA pair charts (short=price/ema6, long=emaW).
  ema6: number | null;
  ema20: number | null;
  ema60: number | null;
  ema120: number | null;
  ema255: number | null;
  // 9 EMA gap columns from analysis.mov_ave_spreads_detail_ema (alias `ema`).
  price_vs_ema6: number | null;
  price_vs_ema20: number | null;
  price_vs_ema60: number | null;
  price_vs_ema120: number | null;
  price_vs_ema255: number | null;
  ema6_vs_ema20: number | null;
  ema6_vs_ema60: number | null;
  ema6_vs_ema120: number | null;
  ema6_vs_ema255: number | null;
  // 5 EMA slope columns (1st derivative) from the EMA detail table.
  ema6_slope: number | null;
  ema20_slope: number | null;
  ema60_slope: number | null;
  ema120_slope: number | null;
  ema255_slope: number | null;
  // 5 EMA curvature columns (2nd derivative) from the EMA detail table.
  ema6_curvature: number | null;
  ema20_curvature: number | null;
  ema60_curvature: number | null;
  ema120_curvature: number | null;
  ema255_curvature: number | null;
  // 5 rolling population σ columns (Bollinger band widths) from the EMA
  // detail table (alias `ema`). Same source data as the SMA detail table's
  // std_*days (σ of price over W days, ddof=0) — populated from the parent
  // pipeline's compute_rolling_stds. Aliased as ema_std_*days to avoid
  // name collisions with d.std_*days. Used to draw Bollinger bands around
  // the long EMA on Price/EMA pair charts.
  ema_std_5days: number | null;
  ema_std_20days: number | null;
  ema_std_60days: number | null;
  ema_std_120days: number | null;
  ema_std_255days: number | null;
  // Rolling OHLC columns from analysis.mov_ave_spreads_detail_ohlc (alias ohlc).
  // Shows the Open, High, Low for the selected MA's window.
  open_20d: number | null;
  high_20d: number | null;
  low_20d: number | null;
  open_60d: number | null;
  high_60d: number | null;
  low_60d: number | null;
  open_120d: number | null;
  high_120d: number | null;
  low_120d: number | null;
  open_255d: number | null;
  high_255d: number | null;
  low_255d: number | null;
  open_500d: number | null;
  high_500d: number | null;
  low_500d: number | null;
  open_750d: number | null;
  high_750d: number | null;
  low_750d: number | null;
  // Rolling-window OHLC extrema from analysis.mov_ave_spreads_detail_ohlc
  // (alias ohlc) — used by the top-level `ohlc` array (roof/floor trendline
  // overlay). Per window W: the date of the window max high, the second
  // local-max peak + date, the date of the window min low, and the second
  // local-min trough + date. DATE columns arrive as Date | string.
  high_date_20d: Date | string | null;
  high_2nd_20d: number | null;
  high_2nd_date_20d: Date | string | null;
  low_date_20d: Date | string | null;
  low_2nd_20d: number | null;
  low_2nd_date_20d: Date | string | null;
  high_date_60d: Date | string | null;
  high_2nd_60d: number | null;
  high_2nd_date_60d: Date | string | null;
  low_date_60d: Date | string | null;
  low_2nd_60d: number | null;
  low_2nd_date_60d: Date | string | null;
  high_date_120d: Date | string | null;
  high_2nd_120d: number | null;
  high_2nd_date_120d: Date | string | null;
  low_date_120d: Date | string | null;
  low_2nd_120d: number | null;
  low_2nd_date_120d: Date | string | null;
  high_date_255d: Date | string | null;
  high_2nd_255d: number | null;
  high_2nd_date_255d: Date | string | null;
  low_date_255d: Date | string | null;
  low_2nd_255d: number | null;
  low_2nd_date_255d: Date | string | null;
  high_date_500d: Date | string | null;
  high_2nd_500d: number | null;
  high_2nd_date_500d: Date | string | null;
  low_date_500d: Date | string | null;
  low_2nd_500d: number | null;
  low_2nd_date_500d: Date | string | null;
  high_date_750d: Date | string | null;
  high_2nd_750d: number | null;
  high_2nd_date_750d: Date | string | null;
  low_date_750d: Date | string | null;
  low_2nd_750d: number | null;
  low_2nd_date_750d: Date | string | null;
  high_date_1275d: Date | string | null;
  high_2nd_1275d: number | null;
  high_2nd_date_1275d: Date | string | null;
  low_date_1275d: Date | string | null;
  low_2nd_1275d: number | null;
  low_2nd_date_1275d: Date | string | null;
  open_1275d: number | null;
  high_1275d: number | null;
  low_1275d: number | null;
  // Roof/floor line slopes through the two anchors (price units per
  // trading day) from the OHLC extrema table — surfaced in the chart
  // tooltip alongside the (top, 2nd) anchor points.
  high_line_slope_20d: number | null;
  low_line_slope_20d: number | null;
  high_line_slope_60d: number | null;
  low_line_slope_60d: number | null;
  high_line_slope_120d: number | null;
  low_line_slope_120d: number | null;
  high_line_slope_255d: number | null;
  low_line_slope_255d: number | null;
  high_line_slope_500d: number | null;
  low_line_slope_500d: number | null;
  high_line_slope_750d: number | null;
  low_line_slope_750d: number | null;
  high_line_slope_1275d: number | null;
  low_line_slope_1275d: number | null;
}

// ----------------------------------------------------------------------------
//  Helpers
// ----------------------------------------------------------------------------

/** Build the display label for a (ma_short, ma_long) pair.
 *  ma_short = 0 → "Price/MA{long}" or "Price/EMA{long}" (price pair)
 *  ma_short = -1 → "Amt/MA{long}" (trading-amount pair)
 *  else → "MA{short}/MA{long}" or "EMA{short}/EMA{long}" (MA/MA pair)
 *  kind = "ema" switches the label prefix from MA to EMA. */
function pairLabel(maShort: number, maLong: number, kind: MovAveSpreadPairKind = "price"): string {
  if (kind === "ema") {
    if (maShort === 0) return `Price/EMA${maLong}`;
    return `EMA${maShort}/EMA${maLong}`;
  }
  if (maShort === 0) return `Price/MA${maLong}`;
  if (maShort === -1) return `Amt/MA${maLong}`;
  return `MA${maShort}/MA${maLong}`;
}

/** Pick the long-MA value for a chart row given the ma_long window. */
function pickLong(r: DbChartRow, maLong: number): number | null {
  switch (maLong) {
    case 5:   return toNum(r.ma5);
    case 20:  return toNum(r.ma20);
    case 60:  return toNum(r.ma60);
    case 120: return toNum(r.ma120);
    case 255: return toNum(r.ma255);
    default:  return null;
  }
}

/** Pick the slope (1st derivative) of MA{window} from a chart row. */
function pickSlope(r: DbChartRow, window: number): number | null {
  switch (window) {
    case 5:   return toNum(r.ma5_slope);
    case 20:  return toNum(r.ma20_slope);
    case 60:  return toNum(r.ma60_slope);
    case 120: return toNum(r.ma120_slope);
    case 255: return toNum(r.ma255_slope);
    default:  return null;
  }
}

/** Pick the curvature (2nd derivative) of MA{window} from a chart row. */
function pickCurvature(r: DbChartRow, window: number): number | null {
  switch (window) {
    case 5:   return toNum(r.ma5_curvature);
    case 20:  return toNum(r.ma20_curvature);
    case 60:  return toNum(r.ma60_curvature);
    case 120: return toNum(r.ma120_curvature);
    case 255: return toNum(r.ma255_curvature);
    default:  return null;
  }
}

/** Pick the rolling population σ (Bollinger band width) for the given
 *  MA window from a chart row. The σ columns are stored on the detail row
 *  as std_{W}days (W = window). Used to draw the ±k×σ envelope around the
 *  long MA on Price/MA pair charts. */
function pickStd(r: DbChartRow, window: number): number | null {
  switch (window) {
    case 5:   return toNum(r.std_5days);
    case 20:  return toNum(r.std_20days);
    case 60:  return toNum(r.std_60days);
    case 120: return toNum(r.std_120days);
    case 255: return toNum(r.std_255days);
    default:  return null;
  }
}

/** Pick the trading-amount MA value for the given window from a chart row.
 *  The columns are stored on the detail row as trading_amt_ma{W}.
 *  Used to render the trading-amt envelope (5 MA lines forming a band
 *  around trading_amount) when an Amt/MA pair is selected. */
function pickTradingAmtMa(r: DbChartRow, window: number): number | null {
  switch (window) {
    case 5:   return toNum(r.trading_amt_ma5);
    case 20:  return toNum(r.trading_amt_ma20);
    case 60:  return toNum(r.trading_amt_ma60);
    case 120: return toNum(r.trading_amt_ma120);
    case 255: return toNum(r.trading_amt_ma255);
    default:  return null;
  }
}

/** Pick the SLOPE (fractional daily change) of trading_amt_ma{window} from
 *  a chart row. Surfaced in the chart tooltip when trading-amt display is
 *  enabled. */
function pickTradingAmtMaSlope(r: DbChartRow, window: number): number | null {
  switch (window) {
    case 5:   return toNum(r.trading_amt_ma5_slope);
    case 20:  return toNum(r.trading_amt_ma20_slope);
    case 60:  return toNum(r.trading_amt_ma60_slope);
    case 120: return toNum(r.trading_amt_ma120_slope);
    case 255: return toNum(r.trading_amt_ma255_slope);
    default:  return null;
  }
}

/** Pick the MARKET-SHARE MA (dimensionless ratio 0..1) for the given
 *  window from a chart row. Surfaced in the chart tooltip as a percentage
 *  when trading-amt display is enabled. */
function pickTradingAmtMarketShare(r: DbChartRow, window: number): number | null {
  switch (window) {
    case 5:   return toNum(r.trading_amt_market_share_ma5);
    case 20:  return toNum(r.trading_amt_market_share_ma20);
    case 60:  return toNum(r.trading_amt_market_share_ma60);
    case 120: return toNum(r.trading_amt_market_share_ma120);
    case 255: return toNum(r.trading_amt_market_share_ma255);
    default:  return null;
  }
}

/** Pick the trading-amt Bollinger band σ for the given window from a
 *  chart row. Columns come from analysis.mov_ave_trading_amt (aliased
 *  as `ta` in the chart SQL). Used to set long_std on Amt/MA pair
 *  rows for Bollinger-style envelopes (MA ± k×σ). */
function pickTradingAmtStd(r: DbChartRow, window: number): number | null {
  switch (window) {
    case 5:   return toNum(r.trading_amt_std5);
    case 20:  return toNum(r.trading_amt_std20);
    case 60:  return toNum(r.trading_amt_std60);
    case 120: return toNum(r.trading_amt_std120);
    case 255: return toNum(r.trading_amt_std255);
    default:  return null;
  }
}

/** Pick the long EMA value for a chart row given the ma_long window.
 *  EMA values come from stats.{sec_type}_tech_stats (aliased as `t` in
 *  the chart SQL). Windows: 6/20/60/120/255 (EMA6 replaces MA5). */
function pickEmaLong(r: DbChartRow, maLong: number): number | null {
  switch (maLong) {
    case 6:   return toNum(r.ema6);
    case 20:  return toNum(r.ema20);
    case 60:  return toNum(r.ema60);
    case 120: return toNum(r.ema120);
    case 255: return toNum(r.ema255);
    default:  return null;
  }
}

/** Pick the EMA slope (1st derivative) for the given window from a chart
 *  row. EMA slopes come from analysis.mov_ave_spreads_detail_ema (aliased
 *  as `ema` in the chart SQL). */
function pickEmaSlope(r: DbChartRow, window: number): number | null {
  switch (window) {
    case 6:   return toNum(r.ema6_slope);
    case 20:  return toNum(r.ema20_slope);
    case 60:  return toNum(r.ema60_slope);
    case 120: return toNum(r.ema120_slope);
    case 255: return toNum(r.ema255_slope);
    default:  return null;
  }
}

/** Pick the EMA curvature (2nd derivative) for the given window from a
 *  chart row. EMA curvatures come from analysis.mov_ave_spreads_detail_ema. */
function pickEmaCurvature(r: DbChartRow, window: number): number | null {
  switch (window) {
    case 6:   return toNum(r.ema6_curvature);
    case 20:  return toNum(r.ema20_curvature);
    case 60:  return toNum(r.ema60_curvature);
    case 120: return toNum(r.ema120_curvature);
    case 255: return toNum(r.ema255_curvature);
    default:  return null;
  }
}

/** Pick the rolling population σ (Bollinger band width) for the given EMA
 *  window from a chart row. The σ columns are stored on the EMA detail row
 *  as ema_std_{W}days (aliased from ema.std_{W}days in the chart SQL).
 *
 *  Window mapping: EMA6 → ema_std_5days (5-day σ, closest available window
 *  to 6), EMA20 → ema_std_20days, EMA60 → ema_std_60days, etc. Same source
 *  data as the SMA detail table's std_*days (σ of price over W days, ddof=0).
 *  Used to draw the ±k×σ envelope around the long EMA on Price/EMA charts. */
function pickEmaStd(r: DbChartRow, window: number): number | null {
  switch (window) {
    case 6:   return toNum(r.ema_std_5days);
    case 20:  return toNum(r.ema_std_20days);
    case 60:  return toNum(r.ema_std_60days);
    case 120: return toNum(r.ema_std_120days);
    case 255: return toNum(r.ema_std_255days);
    default:  return null;
  }
}

// ----------------------------------------------------------------------------
//  Per-sec_type source-table config — used to branch the chart JOINs and
//  the name lookup. ETFs use etf_basic_stats + etf_adjustment + etf_tech_stats
//  (price = COALESCE(adj_close, close)); indices use index_basic_stats +
//  index_tech_stats (price = close, no adjustment table).
// ----------------------------------------------------------------------------
interface SecSource {
  /** Schema-qualified identity table for the asset name lookup. */
  identityTable: string;
  /** FROM clause for the chart query — already includes the JOINs needed to
   *  recover price + all 5 MAs alongside the 9 gap columns. The detail
   *  table alias is `d` and is filtered by `d.sec_type = $2`. */
  chartFromClause: string;
  /** SQL expression for the per-row price column. */
  priceExpr: string;
  /** SQL expression for the open column. */
  openExpr: string;
  /** SQL expression for the high column. */
  highExpr: string;
  /** SQL expression for the low column. */
  lowExpr: string;
  /** SQL expression for the trading_amount column. */
  tradingAmtExpr: string;
}

const SEC_SOURCES: Record<MaSpreadSecType, SecSource> = {
  etf: {
    identityTable: "stats.etf_identity",
    chartFromClause:
      "FROM analysis.mov_ave_spreads_detail d\n" +
      "  JOIN stats.etf_basic_stats   b ON b.date = d.date AND b.code = d.code\n" +
      "  LEFT JOIN stats.etf_adjustment a ON a.date = d.date AND a.code = d.code\n" +
      "  LEFT JOIN stats.etf_liquidity_margin lm ON lm.date = d.date AND lm.code = d.code\n" +
      "  LEFT JOIN stats.etf_tech_stats  t ON t.date = d.date AND t.code = d.code",
    priceExpr: "COALESCE(a.adj_close, b.close)",
    openExpr: "COALESCE(a.adj_open, b.open)",
    highExpr: "COALESCE(a.adj_high, b.high)",
    lowExpr: "COALESCE(a.adj_low, b.low)",
    tradingAmtExpr: "lm.trading_amount",
  },
  index: {
    identityTable: "stats.index_identity",
    chartFromClause:
      "FROM analysis.mov_ave_spreads_detail d\n" +
      "  JOIN stats.index_basic_stats b ON b.date = d.date AND b.code = d.code\n" +
      "  LEFT JOIN stats.index_tech_stats t ON t.date = d.date AND t.code = d.code",
    priceExpr: "b.close",
    openExpr: "b.open",
    highExpr: "b.high",
    lowExpr: "b.low",
    tradingAmtExpr: "b.trading_amount",
  },
  stock: {
    identityTable: "stats.stock_identity",
    chartFromClause:
      "FROM analysis.mov_ave_spreads_detail d\n" +
      "  JOIN stats.stock_basic_stats b ON b.date = d.date AND b.code = d.code\n" +
      "  LEFT JOIN stats.stock_liquidity_margin lm ON lm.date = d.date AND lm.code = d.code\n" +
      "  LEFT JOIN stats.stock_tech_stats t ON t.date = d.date AND t.code = d.code",
    priceExpr: "b.close",
    openExpr: "b.open",
    highExpr: "b.high",
    lowExpr: "b.low",
    tradingAmtExpr: "lm.trading_amount",
  },
};

// ----------------------------------------------------------------------------
//  listMovAveSpreadCodes — one row per asset code with first/last date,
//  n_dates, and the latest snapshot of all 9 gap_values (for sparkline /
//  sort). Server-side: DISTINCT ON (code) picks the latest wide detail row
//  per code; the 9 gap columns are passed through to TypeScript, which
//  assembles them into the latest_gaps array.
// ----------------------------------------------------------------------------
function buildCodesSql(secType: MaSpreadSecType): string {
  const src = SEC_SOURCES[secType];
  return `
    WITH latest_name AS (
      SELECT DISTINCT ON (code) code, name
      FROM ${src.identityTable}
      ORDER BY code, date DESC
    ),
    code_dates AS (
      SELECT
        code,
        MIN(date) AS first_date,
        MAX(date) AS last_date,
        COUNT(DISTINCT date) AS n_dates
      FROM analysis.mov_ave_spreads_detail
      WHERE sec_type = $1
      GROUP BY code
    ),
    latest_row AS (
      SELECT DISTINCT ON (code) *
      FROM analysis.mov_ave_spreads_detail
      WHERE sec_type = $1
      ORDER BY code, date DESC
    ),
    code_ranges AS (
      SELECT
        code,
        GREATEST(
          MAX(price_vs_ma5), MAX(price_vs_ma20), MAX(price_vs_ma60),
          MAX(price_vs_ma120), MAX(price_vs_ma255),
          MAX(ma5_vs_ma20), MAX(ma5_vs_ma60), MAX(ma5_vs_ma120), MAX(ma5_vs_ma255)
        ) AS max_gain,
        LEAST(
          MIN(price_vs_ma5), MIN(price_vs_ma20), MIN(price_vs_ma60),
          MIN(price_vs_ma120), MIN(price_vs_ma255),
          MIN(ma5_vs_ma20), MIN(ma5_vs_ma60), MIN(ma5_vs_ma120), MIN(ma5_vs_ma255)
        ) AS max_loss
      FROM analysis.mov_ave_spreads_detail
      WHERE sec_type = $1
      GROUP BY code
    )
    SELECT
      cd.code,
      COALESCE(n.name, '')   AS name,
      cd.first_date,
      cd.last_date,
      cd.n_dates,
      lr.price_vs_ma5, lr.price_vs_ma20, lr.price_vs_ma60,
      lr.price_vs_ma120, lr.price_vs_ma255,
      lr.ma5_vs_ma20, lr.ma5_vs_ma60, lr.ma5_vs_ma120, lr.ma5_vs_ma255,
      cr.max_gain,
      cr.max_loss,
      (cr.max_gain - cr.max_loss) AS max_spread
    FROM code_dates cd
    LEFT JOIN latest_name n  ON n.code  = cd.code
    LEFT JOIN latest_row lr ON lr.code = cd.code
    LEFT JOIN code_ranges cr ON cr.code = cd.code
    ORDER BY (cr.max_gain - cr.max_loss) DESC NULLS LAST, cd.code
  `;
}

export async function listMovAveSpreadCodes(
  rawSecType: string | undefined | null,
  sector?: string | null,
  industry?: string | null,
  strategy?: string | null,
  theme?: string | null,
  rawExchange?: string | null,
): Promise<MovAveSpreadCodesResponse> {
  const secType = normalizeSecType(rawSecType);
  const sectorFilter = (sector ?? "").trim();
  const industryFilter = (industry ?? "").trim();
  const strategyFilter = (strategy ?? "").trim();
  const themeFilter = (theme ?? "").trim();
  const hasClassFilter = !!(sectorFilter || industryFilter || strategyFilter || themeFilter);
  const exFilter = (rawExchange ?? "").trim() || null;
  // Build the meta map when EITHER a classification filter or an exchange
  // filter is active — both need the sec_classification row to decide.
  const needMeta = hasClassFilter || !!exFilter;

  const rows = await queryRows<DbCodeRow>(buildCodesSql(secType), [secType]);

  // When a classification filter is active, fetch the meta rows (same query as
  // listMovAveSpreadThemes) and build a code → classification map so
  // matchesClassification() can decide which codes to include. Industry and
  // strategy filters are mutually exclusive (handled by matchesClassification).
  let classMap: Map<string, DbMaSpreadMetaRow> | null = null;
  if (needMeta) {
    const metaType = MA_SPREAD_META_TYPE[secType];
    const metaRows = await queryRows<DbMaSpreadMetaRow>(MA_SPREAD_META_SQL, [secType, metaType]);
    classMap = new Map<string, DbMaSpreadMetaRow>();
    for (const m of metaRows) {
      const code = stripExchangeSuffix(m.code);
      if (!code) continue;
      classMap.set(code, m);
    }
  }

  const codes: MovAveSpreadCodeRow[] = [];
  for (const r of rows) {
    const code = stripped(r.code);
    if (classMap) {
      const meta = classMap.get(code);
      if (hasClassFilter && (!meta || !matchesClassification(meta, sectorFilter, industryFilter, strategyFilter, themeFilter))) {
        continue;
      }
      // Exchange filter: codes without a sec_classification row (meta is null)
      // have no exchange info and are excluded when a filter is active — same
      // behavior as listIndexThemes (COALESCE(exchange, '') fails the match).
      if (exFilter && (!meta || !matchesExchange(meta.exchange, exFilter))) {
        continue;
      }
    }
    // Build the latest_gaps array from the 9 wide gap columns.
    const latestGaps: MovAveSpreadLatestGap[] = PAIR_ORDER.map(
      ([maShort, maLong, gapCol]) => ({
        ma_short: maShort,
        ma_long: maLong,
        gap_value: toNum(r[gapCol as keyof DbCodeRow]),
      }),
    );
    codes.push({
      code,
      name: r.name ?? "",
      first_date: formatDate(r.first_date),
      last_date: formatDate(r.last_date),
      n_dates: Number(r.n_dates) || 0,
      latest_gaps: latestGaps,
      max_gain: toNum(r.max_gain),
      max_loss: toNum(r.max_loss),
      max_spread: toNum(r.max_spread),
    });
  }
  return { codes };
}

// ----------------------------------------------------------------------------
//  getMovAveSpreadChart — all 9 pair time series for one asset.
//
//  JOINs analysis.mov_ave_spreads_detail with the asset-appropriate source
//  tables (etf_basic_stats + etf_adjustment + etf_tech_stats for ETFs;
//  index_basic_stats + index_tech_stats for indices) to recover:
//    • price (COALESCE(adj_close, close) for ETFs; close for indices)
//    • ma5 / ma20 / ma60 / ma120 / ma255
//  …alongside the 9 precomputed gap_value columns. Client-side, we fan each
//  row out into 9 pair series entries (short_value, long_value, gap_value).
// ----------------------------------------------------------------------------
function buildChartSql(secType: MaSpreadSecType): string {
  const src = SEC_SOURCES[secType];
  return `
    SELECT
      d.date,
      ${src.priceExpr} AS price,
      ${src.openExpr} AS open,
      ${src.highExpr} AS high,
      ${src.lowExpr} AS low,
      ${src.tradingAmtExpr} AS trading_amount,
      t.ma5, t.ma20, t.ma60, t.ma120, t.ma255,
      t.ema6, t.ema20, t.ema60, t.ema120, t.ema255,
      d.price_vs_ma5, d.price_vs_ma20, d.price_vs_ma60,
      d.price_vs_ma120, d.price_vs_ma255,
      d.ma5_vs_ma20, d.ma5_vs_ma60, d.ma5_vs_ma120, d.ma5_vs_ma255,
      d.price_slope, d.ma5_slope, d.ma20_slope, d.ma60_slope, d.ma120_slope, d.ma255_slope,
      d.price_curvature, d.ma5_curvature, d.ma20_curvature, d.ma60_curvature,
      d.ma120_curvature, d.ma255_curvature,
      d.std_5days, d.std_20days, d.std_60days, d.std_120days, d.std_255days,
      d.trading_amt_ma5, d.trading_amt_ma20, d.trading_amt_ma60,
      d.trading_amt_ma120, d.trading_amt_ma255,
      d.trading_amt_ma5_slope, d.trading_amt_ma20_slope, d.trading_amt_ma60_slope,
      d.trading_amt_ma120_slope, d.trading_amt_ma255_slope,
      d.trading_amt_market_share_ma5, d.trading_amt_market_share_ma20,
      d.trading_amt_market_share_ma60, d.trading_amt_market_share_ma120,
      d.trading_amt_market_share_ma255,
      ta.trading_amt_std5, ta.trading_amt_std20, ta.trading_amt_std60,
      ta.trading_amt_std120, ta.trading_amt_std255,
      ema.price_vs_ema6, ema.price_vs_ema20, ema.price_vs_ema60,
      ema.price_vs_ema120, ema.price_vs_ema255,
      ema.ema6_vs_ema20, ema.ema6_vs_ema60, ema.ema6_vs_ema120, ema.ema6_vs_ema255,
      ema.ema6_slope, ema.ema20_slope, ema.ema60_slope, ema.ema120_slope, ema.ema255_slope,
      ema.ema6_curvature, ema.ema20_curvature, ema.ema60_curvature,
      ema.ema120_curvature, ema.ema255_curvature,
      ema.std_5days AS ema_std_5days, ema.std_20days AS ema_std_20days,
      ema.std_60days AS ema_std_60days, ema.std_120days AS ema_std_120days,
      ema.std_255days AS ema_std_255days,
      rsi.date_of_last_extreme,
      rsi.gap_since_last_extreme,
      rsi.days_since_last_extreme,
      rsi.rsi_6days, rsi.rsi_10days, rsi.rsi_14days, rsi.rsi_20days,
      ohlc.open_20d, ohlc.high_20d, ohlc.low_20d,
      ohlc.open_60d, ohlc.high_60d, ohlc.low_60d,
      ohlc.open_120d, ohlc.high_120d, ohlc.low_120d,
      ohlc.open_255d, ohlc.high_255d, ohlc.low_255d,
      ohlc.open_500d, ohlc.high_500d, ohlc.low_500d,
      ohlc.open_750d, ohlc.high_750d, ohlc.low_750d,
      ohlc.high_date_20d, ohlc.high_2nd_20d, ohlc.high_2nd_date_20d,
      ohlc.low_date_20d, ohlc.low_2nd_20d, ohlc.low_2nd_date_20d,
      ohlc.high_date_60d, ohlc.high_2nd_60d, ohlc.high_2nd_date_60d,
      ohlc.low_date_60d, ohlc.low_2nd_60d, ohlc.low_2nd_date_60d,
      ohlc.high_date_120d, ohlc.high_2nd_120d, ohlc.high_2nd_date_120d,
      ohlc.low_date_120d, ohlc.low_2nd_120d, ohlc.low_2nd_date_120d,
      ohlc.high_date_255d, ohlc.high_2nd_255d, ohlc.high_2nd_date_255d,
      ohlc.low_date_255d, ohlc.low_2nd_255d, ohlc.low_2nd_date_255d,
      ohlc.high_date_500d, ohlc.high_2nd_500d, ohlc.high_2nd_date_500d,
      ohlc.low_date_500d, ohlc.low_2nd_500d, ohlc.low_2nd_date_500d,
      ohlc.high_date_750d, ohlc.high_2nd_750d, ohlc.high_2nd_date_750d,
      ohlc.low_date_750d, ohlc.low_2nd_750d, ohlc.low_2nd_date_750d,
      ohlc.high_date_1275d, ohlc.high_2nd_1275d, ohlc.high_2nd_date_1275d,
      ohlc.low_date_1275d, ohlc.low_2nd_1275d, ohlc.low_2nd_date_1275d,
      ohlc.open_1275d, ohlc.high_1275d, ohlc.low_1275d,
      ohlc.high_line_slope_20d, ohlc.low_line_slope_20d,
      ohlc.high_line_slope_60d, ohlc.low_line_slope_60d,
      ohlc.high_line_slope_120d, ohlc.low_line_slope_120d,
      ohlc.high_line_slope_255d, ohlc.low_line_slope_255d,
      ohlc.high_line_slope_500d, ohlc.low_line_slope_500d,
      ohlc.high_line_slope_750d, ohlc.low_line_slope_750d,
      ohlc.high_line_slope_1275d, ohlc.low_line_slope_1275d
    ${src.chartFromClause}
    LEFT JOIN analysis.mov_ave_trading_amt ta
      ON ta.sec_type = d.sec_type AND ta.code = d.code AND ta.date = d.date
    LEFT JOIN analysis.mov_ave_spreads_detail_ema ema
      ON ema.sec_type = d.sec_type AND ema.code = d.code AND ema.date = d.date
    LEFT JOIN analysis.mov_ave_rsi rsi
      ON rsi.sec_type = d.sec_type AND rsi.code = d.code AND rsi.date = d.date
    LEFT JOIN analysis.mov_ave_spreads_detail_ohlc ohlc
      ON ohlc.sec_type = d.sec_type AND ohlc.code = d.code AND ohlc.date = d.date
    WHERE d.sec_type = $2
      AND REGEXP_REPLACE(d.code, '\\.(SZ|SS|BJ|HK)$', '') = $1::text
    ORDER BY d.date ASC
  `;
}

/** SQL for the market-hype EPISODES of one (sec_type, code): one row per
 *  CONCATENATED hype episode per check-in window (span bucketed into
 *  [min_checkin_period, next window)), straight from
 *  analysis.mov_ave_market_hypes (PK (sec_type, code, start_date,
 *  end_date, min_checkin_period)). Fetched once per chart request — far
 *  cheaper than pivoting the episodes back into per-date flags inside the
 *  chart query. */
function buildHypeEpisodesSql(): string {
  return `
    SELECT
      h.min_checkin_period,
      h.start_date,
      h.end_date,
      h.hype_days,
      h.trading_amt_hype_days,
      h.std_hype_days
    FROM analysis.mov_ave_market_hypes h
    WHERE h.sec_type = $2
      AND REGEXP_REPLACE(h.code, '\\.(SZ|SS|BJ|HK)$', '') = $1::text
    ORDER BY h.min_checkin_period, h.start_date
  `;
}

function buildNameSql(secType: MaSpreadSecType): string {
  const src = SEC_SOURCES[secType];
  return `
    SELECT DISTINCT ON (code) code, name
    FROM ${src.identityTable}
    WHERE REGEXP_REPLACE(code, '\\.(SZ|SS|BJ|HK)$', '') = $1::text
    ORDER BY code, date DESC
  `;
}

/** Map one DB chart row to a top-level ohlc extrema row (all 7 windows).
 *  Used to build response.ohlc — ONE copy per date shared by all pair series
 *  (instead of fanning the extrema out into every pair's rows, which would
 *  multiply the payload by the pair count). */
function toOhlcExtremaRow(r: DbChartRow): MovAveSpreadOhlcRow {
  const d = (v: Date | string | null): string | null =>
    v != null ? formatDate(v) : null;
  return {
    date: formatDate(r.date),
    open_20d: toNum(r.open_20d),
    high_20d: toNum(r.high_20d),
    high_date_20d: d(r.high_date_20d),
    high_2nd_20d: toNum(r.high_2nd_20d),
    high_2nd_date_20d: d(r.high_2nd_date_20d),
    low_20d: toNum(r.low_20d),
    low_date_20d: d(r.low_date_20d),
    low_2nd_20d: toNum(r.low_2nd_20d),
    low_2nd_date_20d: d(r.low_2nd_date_20d),
    open_60d: toNum(r.open_60d),
    high_60d: toNum(r.high_60d),
    high_date_60d: d(r.high_date_60d),
    high_2nd_60d: toNum(r.high_2nd_60d),
    high_2nd_date_60d: d(r.high_2nd_date_60d),
    low_60d: toNum(r.low_60d),
    low_date_60d: d(r.low_date_60d),
    low_2nd_60d: toNum(r.low_2nd_60d),
    low_2nd_date_60d: d(r.low_2nd_date_60d),
    open_120d: toNum(r.open_120d),
    high_120d: toNum(r.high_120d),
    high_date_120d: d(r.high_date_120d),
    high_2nd_120d: toNum(r.high_2nd_120d),
    high_2nd_date_120d: d(r.high_2nd_date_120d),
    low_120d: toNum(r.low_120d),
    low_date_120d: d(r.low_date_120d),
    low_2nd_120d: toNum(r.low_2nd_120d),
    low_2nd_date_120d: d(r.low_2nd_date_120d),
    open_255d: toNum(r.open_255d),
    high_255d: toNum(r.high_255d),
    high_date_255d: d(r.high_date_255d),
    high_2nd_255d: toNum(r.high_2nd_255d),
    high_2nd_date_255d: d(r.high_2nd_date_255d),
    low_255d: toNum(r.low_255d),
    low_date_255d: d(r.low_date_255d),
    low_2nd_255d: toNum(r.low_2nd_255d),
    low_2nd_date_255d: d(r.low_2nd_date_255d),
    open_500d: toNum(r.open_500d),
    high_500d: toNum(r.high_500d),
    high_date_500d: d(r.high_date_500d),
    high_2nd_500d: toNum(r.high_2nd_500d),
    high_2nd_date_500d: d(r.high_2nd_date_500d),
    low_500d: toNum(r.low_500d),
    low_date_500d: d(r.low_date_500d),
    low_2nd_500d: toNum(r.low_2nd_500d),
    low_2nd_date_500d: d(r.low_2nd_date_500d),
    open_750d: toNum(r.open_750d),
    high_750d: toNum(r.high_750d),
    high_date_750d: d(r.high_date_750d),
    high_2nd_750d: toNum(r.high_2nd_750d),
    high_2nd_date_750d: d(r.high_2nd_date_750d),
    low_750d: toNum(r.low_750d),
    low_date_750d: d(r.low_date_750d),
    low_2nd_750d: toNum(r.low_2nd_750d),
    low_2nd_date_750d: d(r.low_2nd_date_750d),
    open_1275d: toNum(r.open_1275d),
    high_1275d: toNum(r.high_1275d),
    high_date_1275d: d(r.high_date_1275d),
    high_2nd_1275d: toNum(r.high_2nd_1275d),
    high_2nd_date_1275d: d(r.high_2nd_date_1275d),
    low_1275d: toNum(r.low_1275d),
    low_date_1275d: d(r.low_date_1275d),
    low_2nd_1275d: toNum(r.low_2nd_1275d),
    low_2nd_date_1275d: d(r.low_2nd_date_1275d),
    high_line_slope_20d: toNum(r.high_line_slope_20d),
    low_line_slope_20d: toNum(r.low_line_slope_20d),
    high_line_slope_60d: toNum(r.high_line_slope_60d),
    low_line_slope_60d: toNum(r.low_line_slope_60d),
    high_line_slope_120d: toNum(r.high_line_slope_120d),
    low_line_slope_120d: toNum(r.low_line_slope_120d),
    high_line_slope_255d: toNum(r.high_line_slope_255d),
    low_line_slope_255d: toNum(r.low_line_slope_255d),
    high_line_slope_500d: toNum(r.high_line_slope_500d),
    low_line_slope_500d: toNum(r.low_line_slope_500d),
    high_line_slope_750d: toNum(r.high_line_slope_750d),
    low_line_slope_750d: toNum(r.low_line_slope_750d),
    high_line_slope_1275d: toNum(r.high_line_slope_1275d),
    low_line_slope_1275d: toNum(r.low_line_slope_1275d),
  };
}

/** One episode row from analysis.mov_ave_market_hypes (see
 *  buildHypeEpisodesSql). trading_amt_hype_days / std_hype_days may be
 *  NULL on rows built before those columns existed. */
interface DbHypeEpisodeRow {
  min_checkin_period: number;
  start_date: Date | string;
  end_date: Date | string;
  hype_days: number;
  trading_amt_hype_days: number | null;
  std_hype_days: number | null;
}

/** Group the episode rows into the response's per-window map
 *  (check-in window → episodes ascending by startDate; windows with no
 *  episodes are absent). */
function toHypeEpisodes(rows: DbHypeEpisodeRow[]): MovAveSpreadHypeEpisodes {
  const out: MovAveSpreadHypeEpisodes = {};
  for (const r of rows) {
    (out[r.min_checkin_period] ??= []).push({
      startDate: formatDate(r.start_date),
      endDate: formatDate(r.end_date),
      hypeDays: r.hype_days,
      tradingAmtHypeDays: r.trading_amt_hype_days ?? undefined,
      stdHypeDays: r.std_hype_days ?? undefined,
    });
  }
  return out;
}

export async function getMovAveSpreadChart(
  rawCode: string,
  rawSecType: string | undefined | null,
): Promise<MovAveSpreadChartResponse> {
  const secType = normalizeSecType(rawSecType);
  const target = stripped(rawCode);

  // Fetch chart rows + name + market-hype episodes in parallel.
  const [chartRows, nameRows, hypeEpisodeRows] = await Promise.all([
    queryRows<DbChartRow>(buildChartSql(secType), [target, secType]),
    queryRows<{ name: string | null }>(buildNameSql(secType), [target]),
    queryRows<DbHypeEpisodeRow>(buildHypeEpisodesSql(), [target, secType]),
  ]);

  const name = nameRows[0]?.name ?? "";

  // Initialize the 9 price pair series + 9 EMA pair series + 5 amt pair
  // series in canonical order.
  const byPair = new Map<string, MovAveSpreadPairSeries>();
  for (const [ms, ml] of PAIR_ORDER) {
    const key = `price-${ms}/${ml}`;
    byPair.set(key, {
      ma_short: ms,
      ma_long: ml,
      pair_label: pairLabel(ms, ml),
      kind: "price" as MovAveSpreadPairKind,
      rows: [],
    });
  }
  for (const [ms, ml] of EMA_PAIR_ORDER) {
    const key = `ema-${ms}/${ml}`;
    byPair.set(key, {
      ma_short: ms,
      ma_long: ml,
      pair_label: pairLabel(ms, ml, "ema"),
      kind: "ema" as MovAveSpreadPairKind,
      rows: [],
    });
  }
  for (const [ms, ml] of AMT_PAIR_ORDER) {
    const key = `amt-${ms}/${ml}`;
    byPair.set(key, {
      ma_short: ms,
      ma_long: ml,
      pair_label: pairLabel(ms, ml),
      kind: "amt" as MovAveSpreadPairKind,
      rows: [],
    });
  }

  // Fan each chart row out into 9 price pair entries + 5 amt pair entries.
  for (const r of chartRows) {
    const dateStr = formatDate(r.date);
    const price = toNum(r.price);
    const ma5 = toNum(r.ma5);
    const open = toNum(r.open);
    const high = toNum(r.high);
    const low = toNum(r.low);
    const tradingAmount = toNum(r.trading_amount);
    // 5 trading-amount MA values — shared across all 5 amt pairs for a given
    // date. Used by the frontend to render the amt envelope (all 5 MA lines
    // form a band around trading_amount).
    const amtMa5   = pickTradingAmtMa(r, 5);
    const amtMa20  = pickTradingAmtMa(r, 20);
    const amtMa60  = pickTradingAmtMa(r, 60);
    const amtMa120 = pickTradingAmtMa(r, 120);
    const amtMa255 = pickTradingAmtMa(r, 255);
    // 5 trading-amount MA SLOPE values (fractional daily change) — shared
    // across all pairs for a given date. Surfaced in the chart tooltip when
    // trading-amt display is enabled.
    const amtSlope5   = pickTradingAmtMaSlope(r, 5);
    const amtSlope20  = pickTradingAmtMaSlope(r, 20);
    const amtSlope60  = pickTradingAmtMaSlope(r, 60);
    const amtSlope120 = pickTradingAmtMaSlope(r, 120);
    const amtSlope255 = pickTradingAmtMaSlope(r, 255);
    // 5 trading-amount MARKET-SHARE MA values (ratio 0..1) — shared across
    // all pairs for a given date. Surfaced in the chart tooltip as a pct.
    const amtShare5   = pickTradingAmtMarketShare(r, 5);
    const amtShare20  = pickTradingAmtMarketShare(r, 20);
    const amtShare60  = pickTradingAmtMarketShare(r, 60);
    const amtShare120 = pickTradingAmtMarketShare(r, 120);
    const amtShare255 = pickTradingAmtMarketShare(r, 255);
    // 5 trading-amount Bollinger band σ values — shared across all pairs.
    const amtStd5   = pickTradingAmtStd(r, 5);
    const amtStd20  = pickTradingAmtStd(r, 20);
    const amtStd60  = pickTradingAmtStd(r, 60);
    const amtStd120 = pickTradingAmtStd(r, 120);
    const amtStd255 = pickTradingAmtStd(r, 255);
    // Last-extreme fields (from analysis.mov_ave_rsi) — shared across all 9
    // pairs for a given date. date_of_last_extreme is a DATE column.
    const dateOfLastExtreme = r.date_of_last_extreme != null
      ? formatDate(r.date_of_last_extreme)
      : null;
    const gapSinceLastExtreme = toNum(r.gap_since_last_extreme);
    const daysSinceLastExtreme = toNum(r.days_since_last_extreme);
    // Wilder RSI (6/10/14/20 days) — shared across all 9 pairs for a given
    // date (describes the price curve, not a specific MA pair).
    const rsi6 = toNum(r.rsi_6days);
    const rsi10 = toNum(r.rsi_10days);
    const rsi14 = toNum(r.rsi_14days);
    const rsi20 = toNum(r.rsi_20days);
    // Rolling OHLC columns from analysis.mov_ave_spreads_detail_ohlc — shared
    // across all pairs for a given date. Shows the Open, High, Low for each
    // MA window (e.g., high_60d = max high over last 60 days).
    const ohlcOpen20 = toNum(r.open_20d);
    const ohlcHigh20 = toNum(r.high_20d);
    const ohlcLow20 = toNum(r.low_20d);
    const ohlcOpen60 = toNum(r.open_60d);
    const ohlcHigh60 = toNum(r.high_60d);
    const ohlcLow60 = toNum(r.low_60d);
    const ohlcOpen120 = toNum(r.open_120d);
    const ohlcHigh120 = toNum(r.high_120d);
    const ohlcLow120 = toNum(r.low_120d);
    const ohlcOpen255 = toNum(r.open_255d);
    const ohlcHigh255 = toNum(r.high_255d);
    const ohlcLow255 = toNum(r.low_255d);
    const ohlcOpen500 = toNum(r.open_500d);
    const ohlcHigh500 = toNum(r.high_500d);
    const ohlcLow500 = toNum(r.low_500d);
    const ohlcOpen750 = toNum(r.open_750d);
    const ohlcHigh750 = toNum(r.high_750d);
    const ohlcLow750 = toNum(r.low_750d);

    // ---- 9 price (Simple MA) pairs ----
    for (const [maShort, maLong, gapCol] of PAIR_ORDER) {
      const series = byPair.get(`price-${maShort}/${maLong}`);
      if (!series) continue;
      const shortVal = maShort === 0 ? price : ma5;
      const longVal = pickLong(r, maLong);
      const gapVal = toNum(r[gapCol as keyof DbChartRow]);
      // slope/curvature: when ma_short = 0 the short series is price, so use
      // price_slope / price_curvature; otherwise use the short MA's derivatives.
      const shortSlope = maShort === 0 ? toNum(r.price_slope) : pickSlope(r, maShort);
      const shortCurv  = maShort === 0 ? toNum(r.price_curvature) : pickCurvature(r, maShort);
      const row: MovAveSpreadDetailRow = {
        date: dateStr,
        short_value: shortVal,
        long_value: longVal,
        gap_value: gapVal,
        short_slope: shortSlope,
        short_curvature: shortCurv,
        long_slope: pickSlope(r, maLong),
        long_curvature: pickCurvature(r, maLong),
        long_std: pickStd(r, maLong),
        open,
        high,
        low,
        trading_amount: tradingAmount,
        date_of_last_extreme: dateOfLastExtreme,
        gap_since_last_extreme: gapSinceLastExtreme,
        days_since_last_extreme: daysSinceLastExtreme,
        rsi_6days: rsi6,
        rsi_10days: rsi10,
        rsi_14days: rsi14,
        rsi_20days: rsi20,
        trading_amt_ma5: amtMa5,
        trading_amt_ma20: amtMa20,
        trading_amt_ma60: amtMa60,
        trading_amt_ma120: amtMa120,
        trading_amt_ma255: amtMa255,
        trading_amt_ma5_slope: amtSlope5,
        trading_amt_ma20_slope: amtSlope20,
        trading_amt_ma60_slope: amtSlope60,
        trading_amt_ma120_slope: amtSlope120,
        trading_amt_ma255_slope: amtSlope255,
        trading_amt_market_share_ma5: amtShare5,
        trading_amt_market_share_ma20: amtShare20,
        trading_amt_market_share_ma60: amtShare60,
        trading_amt_market_share_ma120: amtShare120,
        trading_amt_market_share_ma255: amtShare255,
        trading_amt_std5: amtStd5,
        trading_amt_std20: amtStd20,
        trading_amt_std60: amtStd60,
        trading_amt_std120: amtStd120,
        trading_amt_std255: amtStd255,
        open_20d: ohlcOpen20,
        high_20d: ohlcHigh20,
        low_20d: ohlcLow20,
        open_60d: ohlcOpen60,
        high_60d: ohlcHigh60,
        low_60d: ohlcLow60,
        open_120d: ohlcOpen120,
        high_120d: ohlcHigh120,
        low_120d: ohlcLow120,
        open_255d: ohlcOpen255,
        high_255d: ohlcHigh255,
        low_255d: ohlcLow255,
        open_500d: ohlcOpen500,
        high_500d: ohlcHigh500,
        low_500d: ohlcLow500,
        open_750d: ohlcOpen750,
        high_750d: ohlcHigh750,
        low_750d: ohlcLow750,
      };
      series.rows.push(row);
    }

    // ---- 9 EMA (Exponential MA) pairs ----
    // short = price (ma_short=0) or ema6 (ma_short=6); long = emaW.
    // gap_value, slope, and curvature come from the EMA detail table
    // (alias `ema` in the SQL). long_std is the rolling population σ of
    // price over the long EMA's window (aliased as ema_std_{W}days in
    // the SQL), used to draw the Bollinger envelope around the long EMA.
    const ema6 = toNum(r.ema6);
    for (const [maShort, maLong, gapCol] of EMA_PAIR_ORDER) {
      const series = byPair.get(`ema-${maShort}/${maLong}`);
      if (!series) continue;
      const shortVal = maShort === 0 ? price : ema6;
      const longVal = pickEmaLong(r, maLong);
      const gapVal = toNum(r[gapCol as keyof DbChartRow]);
      // For Price/EMA pairs, short_slope = price_slope (from MA detail);
      // for EMA6/EMA pairs, short_slope = ema6_slope (from EMA detail).
      const shortSlope = maShort === 0 ? toNum(r.price_slope) : pickEmaSlope(r, maShort);
      const shortCurv  = maShort === 0 ? toNum(r.price_curvature) : pickEmaCurvature(r, maShort);
      const row: MovAveSpreadDetailRow = {
        date: dateStr,
        short_value: shortVal,
        long_value: longVal,
        gap_value: gapVal,
        short_slope: shortSlope,
        short_curvature: shortCurv,
        long_slope: pickEmaSlope(r, maLong),
        long_curvature: pickEmaCurvature(r, maLong),
        long_std: pickEmaStd(r, maLong),
        open,
        high,
        low,
        trading_amount: tradingAmount,
        date_of_last_extreme: dateOfLastExtreme,
        gap_since_last_extreme: gapSinceLastExtreme,
        days_since_last_extreme: daysSinceLastExtreme,
        rsi_6days: rsi6,
        rsi_10days: rsi10,
        rsi_14days: rsi14,
        rsi_20days: rsi20,
        trading_amt_ma5: amtMa5,
        trading_amt_ma20: amtMa20,
        trading_amt_ma60: amtMa60,
        trading_amt_ma120: amtMa120,
        trading_amt_ma255: amtMa255,
        trading_amt_ma5_slope: amtSlope5,
        trading_amt_ma20_slope: amtSlope20,
        trading_amt_ma60_slope: amtSlope60,
        trading_amt_ma120_slope: amtSlope120,
        trading_amt_ma255_slope: amtSlope255,
        trading_amt_market_share_ma5: amtShare5,
        trading_amt_market_share_ma20: amtShare20,
        trading_amt_market_share_ma60: amtShare60,
        trading_amt_market_share_ma120: amtShare120,
        trading_amt_market_share_ma255: amtShare255,
        trading_amt_std5: amtStd5,
        trading_amt_std20: amtStd20,
        trading_amt_std60: amtStd60,
        trading_amt_std120: amtStd120,
        trading_amt_std255: amtStd255,
        open_20d: ohlcOpen20,
        high_20d: ohlcHigh20,
        low_20d: ohlcLow20,
        open_60d: ohlcOpen60,
        high_60d: ohlcHigh60,
        low_60d: ohlcLow60,
        open_120d: ohlcOpen120,
        high_120d: ohlcHigh120,
        low_120d: ohlcLow120,
        open_255d: ohlcOpen255,
        high_255d: ohlcHigh255,
        low_255d: ohlcLow255,
        open_500d: ohlcOpen500,
        high_500d: ohlcHigh500,
        low_500d: ohlcLow500,
        open_750d: ohlcOpen750,
        high_750d: ohlcHigh750,
        low_750d: ohlcLow750,
      };
      series.rows.push(row);
    }

    // ---- 5 amt pairs (short = trading_amount, long = trading_amt_maW) ----
    // gap_value = (trading_amount - trading_amt_maW) / trading_amt_maW
    //   (computed here since there is no pre-computed gap column for amt
    //   pairs in the detail table). long_std is set from the Bollinger band
    //   σ column (trading_amt_stdW) so the frontend can draw Bollinger
    //   envelopes around the selected trading-amount MA line.
    for (const [maShort, maLong] of AMT_PAIR_ORDER) {
      const series = byPair.get(`amt-${maShort}/${maLong}`);
      if (!series) continue;
      const shortVal = tradingAmount;
      const longVal = pickTradingAmtMa(r, maLong);
      const gapVal =
        shortVal != null && longVal != null && longVal !== 0
          ? (shortVal - longVal) / longVal
          : null;
      const row: MovAveSpreadDetailRow = {
        date: dateStr,
        short_value: shortVal,
        long_value: longVal,
        gap_value: gapVal,
        short_slope: null,
        short_curvature: null,
        long_slope: null,
        long_curvature: null,
        long_std: pickTradingAmtStd(r, maLong),
        open,
        high,
        low,
        trading_amount: tradingAmount,
        date_of_last_extreme: dateOfLastExtreme,
        gap_since_last_extreme: gapSinceLastExtreme,
        days_since_last_extreme: daysSinceLastExtreme,
        rsi_6days: rsi6,
        rsi_10days: rsi10,
        rsi_14days: rsi14,
        rsi_20days: rsi20,
        trading_amt_ma5: amtMa5,
        trading_amt_ma20: amtMa20,
        trading_amt_ma60: amtMa60,
        trading_amt_ma120: amtMa120,
        trading_amt_ma255: amtMa255,
        trading_amt_ma5_slope: amtSlope5,
        trading_amt_ma20_slope: amtSlope20,
        trading_amt_ma60_slope: amtSlope60,
        trading_amt_ma120_slope: amtSlope120,
        trading_amt_ma255_slope: amtSlope255,
        trading_amt_market_share_ma5: amtShare5,
        trading_amt_market_share_ma20: amtShare20,
        trading_amt_market_share_ma60: amtShare60,
        trading_amt_market_share_ma120: amtShare120,
        trading_amt_market_share_ma255: amtShare255,
        trading_amt_std5: amtStd5,
        trading_amt_std20: amtStd20,
        trading_amt_std60: amtStd60,
        trading_amt_std120: amtStd120,
        trading_amt_std255: amtStd255,
        open_20d: ohlcOpen20,
        high_20d: ohlcHigh20,
        low_20d: ohlcLow20,
        open_60d: ohlcOpen60,
        high_60d: ohlcHigh60,
        low_60d: ohlcLow60,
        open_120d: ohlcOpen120,
        high_120d: ohlcHigh120,
        low_120d: ohlcLow120,
        open_255d: ohlcOpen255,
        high_255d: ohlcHigh255,
        low_255d: ohlcLow255,
        open_500d: ohlcOpen500,
        high_500d: ohlcHigh500,
        low_500d: ohlcLow500,
        open_750d: ohlcOpen750,
        high_750d: ohlcHigh750,
        low_750d: ohlcLow750,
      };
      series.rows.push(row);
    }
  }

  return {
    code: target,
    name,
    pairs: [
      ...PAIR_ORDER.map(([ms, ml]) => byPair.get(`price-${ms}/${ml}`)!),
      ...EMA_PAIR_ORDER.map(([ms, ml]) => byPair.get(`ema-${ms}/${ml}`)!),
      ...AMT_PAIR_ORDER.map(([ms, ml]) => byPair.get(`amt-${ms}/${ml}`)!),
    ],
    // One extrema row per date, index-aligned with every pair's rows.
    ohlc: chartRows.map(toOhlcExtremaRow),
    // Market-hype episodes keyed by check-in window (5/20/60/120/255) —
    // drives the light-purple hyped-period shading (spans, so no
    // index-alignment with the pair rows is needed).
    hypeEpisodes: toHypeEpisodes(hypeEpisodeRows),
  };
}

// ----------------------------------------------------------------------------
//  listMovAveSpreadThemes — two-level L1 sector → L2 industry → items tree for
//  the MA-Spread page's ThemeSelector. Mirrors listPerfAttrThemes() in
//  perf-attribution.ts but draws codes from analysis.mov_ave_spreads_detail
//  (filtered by sec_type) and supports etf / index / stock.
//
//  Labels come precomputed from stats.sec_classification (denormalized onto
//  the table by build_classification.py — no catalog JOIN needed). Codes that
//  don't have a sec_classification row are bucketed under sector 'OTHER' /
//  industry '未分类' so the user still sees them in the selector.
// ----------------------------------------------------------------------------

/** Whitelisted sec_type → sec_classification type discriminator (safe for
 *  string interpolation in the SQL). */
const MA_SPREAD_META_TYPE: Record<MaSpreadSecType, string> = {
  etf: "etf",
  index: "index",
  stock: "stock",
};

interface DbMaSpreadMetaRow extends QueryResultRow {
  code: string;
  name: string;
  sector_id: string;
  sector_label: string;
  industry_id: string;
  industry_label: string;
  industry_slug: string;
  /** When TRUE, sector_id/industry_id hold INDUSTRY classification (industry-
   *  primary row). When FALSE, they hold STRATEGY classification (strategy-
   *  primary row). Used by the parallel strategy/theme selector. */
  is_industry_not_strategy: boolean;
  /** Exchange code from stats.sec_classification (SS/STAR/SZ/GEM/BJ/HK/OVERSEAS).
   *  Used by matchesExchange() to filter the tree by the UI exchange filter. */
  exchange: string;
}

/** Meta SQL shared by listMovAveSpreadThemes() and
 *  listMovAveSpreadStrategyThemes(). Returns one row per code in
 *  analysis.mov_ave_spreads_detail (filtered by sec_type) with its
 *  precomputed L1/L2 classification from stats.sec_classification.
 *  is_industry_not_strategy distinguishes industry-primary (TRUE) from
 *  strategy-primary (FALSE) rows. */
const MA_SPREAD_META_SQL = `
  WITH spread_codes AS (
    SELECT DISTINCT code
    FROM analysis.mov_ave_spreads_detail
    WHERE sec_type = $1::text
  )
  SELECT
    sc.code,
    COALESCE(m.name, '')             AS name,
    COALESCE(m.sector_id,       'OTHER')  AS sector_id,
    COALESCE(m.sector_label,    '其他')   AS sector_label,
    COALESCE(m.industry_id,     'OTHER')  AS industry_id,
    COALESCE(m.industry_label,  '未分类') AS industry_label,
    COALESCE(m.industry_slug,   'other')  AS industry_slug,
    COALESCE(m.is_industry_not_strategy, TRUE) AS is_industry_not_strategy,
    COALESCE(m.exchange, '')               AS exchange
  FROM spread_codes sc
  LEFT JOIN stats.sec_classification m ON m.code = sc.code AND m.type = $2::text
  WHERE COALESCE(m.is_active, TRUE) = TRUE
`;

export async function listMovAveSpreadThemes(
  rawSecType: string | undefined | null,
  rawExchange?: string | null,
): Promise<SectorNode[]> {
  const secType = normalizeSecType(rawSecType);
  const exFilter = (rawExchange ?? "").trim() || null;
  const metaType = MA_SPREAD_META_TYPE[secType];
  const rows = await queryRows<DbMaSpreadMetaRow>(MA_SPREAD_META_SQL, [secType, metaType]);

  const sectorMap = new Map<string, {
    sector_label: string;
    industries: Map<string, IndustryNode>;
  }>();

  for (const r of rows) {
    // LEFT column: only industry-primary securities. Strategy-primary rows
    // (is_industry_not_strategy=FALSE) carry strategy/theme in
    // sector_id/industry_id and belong in the RIGHT column only.
    if (!r.is_industry_not_strategy) continue;
    // Apply exchange filter so the nav tree respects the selected exchange
    // (e.g. HK indices are excluded when "All (primary)" is selected).
    if (exFilter && !matchesExchange(r.exchange, exFilter)) continue;
    // Strip exchange suffix so item codes match the codes returned by
    // listMovAveSpreadCodes (which also strips the suffix).
    const code = stripExchangeSuffix(r.code);
    if (!code) continue;
    const item = { code, name: r.name ?? "" };

    if (!sectorMap.has(r.sector_id)) {
      sectorMap.set(r.sector_id, { sector_label: r.sector_label, industries: new Map() });
    }
    const sector = sectorMap.get(r.sector_id)!;
    if (!sector.industries.has(r.industry_id)) {
      sector.industries.set(r.industry_id, {
        industry_id: r.industry_id,
        industry_label: r.industry_label,
        industry_slug: r.industry_slug,
        count: 0,
        items: [],
      });
    }
    const ind = sector.industries.get(r.industry_id)!;
    ind.items.push(item);
    ind.count++;
  }

  const sectors: SectorNode[] = [];
  for (const [sector_id, sector] of sectorMap) {
    const industries = Array.from(sector.industries.values()).sort((a, b) => {
      if (a.industry_id === "OTHER") return 1;
      if (b.industry_id === "OTHER") return -1;
      return b.count - a.count;
    });
    sectors.push({
      sector_id,
      sector_label: sector.sector_label,
      count: industries.reduce((sum, i) => sum + i.count, 0),
      industries,
    });
  }
  sectors.sort((a, b) => {
    if (a.sector_id === "OTHER") return 1;
    if (b.sector_id === "OTHER") return -1;
    return b.count - a.count;
  });
  return sectors;
}

// ----------------------------------------------------------------------------
//  listMovAveSpreadStrategyThemes — parallel L1 strategy → L2 theme → items
//  tree built from the same MA_SPREAD_META_SQL but using the strategy-primary
//  rows (is_industry_not_strategy=FALSE). sector_id/industry_id on those rows
//  carry the strategy/theme classification. Tree-building is delegated to the
//  shared buildStrategyThemesFromRows helper to avoid duplicating the
//  grouping/sorting logic.
// ----------------------------------------------------------------------------
export async function listMovAveSpreadStrategyThemes(
  rawSecType: string | undefined | null,
  rawExchange?: string | null,
): Promise<StrategyNode[]> {
  const secType = normalizeSecType(rawSecType);
  const exFilter = (rawExchange ?? "").trim() || null;
  const metaType = MA_SPREAD_META_TYPE[secType];
  const rows = await queryRows<DbMaSpreadMetaRow>(MA_SPREAD_META_SQL, [secType, metaType]);

  // Filter by exchange BEFORE building the strategy tree so cross-border
  // securities are excluded when "All (primary)" is selected (same behavior
  // as listMovAveSpreadThemes and listIndexThemes).
  const filteredRows = exFilter
    ? rows.filter((r) => matchesExchange(r.exchange, exFilter))
    : rows;

  const mappedRows = filteredRows.map((r) => ({
    code: stripExchangeSuffix(r.code),
    name: r.name,
    sector_id: r.sector_id,
    sector_label: r.sector_label,
    industry_id: r.industry_id,
    industry_label: r.industry_label,
    industry_slug: r.industry_slug,
    is_industry_not_strategy: r.is_industry_not_strategy,
  }));

  return buildStrategyThemesFromRows(mappedRows);
}
