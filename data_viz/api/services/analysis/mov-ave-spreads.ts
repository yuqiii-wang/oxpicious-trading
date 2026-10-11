/**
 * MA-Spread analysis — listMovAveSpreadCodes + getMovAveSpreadChart (the
 * DEFAULT load: 18 Simple-MA/EMA pair series + one shared per-date
 * tooltip-metrics array) + getMovAveSpreadChartExtras (the on-demand
 * metric groups: amt / ohlc / streaks / pxvol).
 * Extracted from the former analysis.service.ts.
 */
import { queryRows, formatDate, toNum } from "../../lib/db.js";
import type { QueryResultRow } from "pg";
import { stripExchangeSuffix, matchesExchange, codeVariants } from "../../lib/classify-etf.js";
import { stripped } from "./_shared.js";
import { buildStrategyThemesFromRows, matchesClassification } from "../_shared.js";
import { labelDecodeSql, LABEL_CODE } from "../label-codes.js";
import type {
  MaSpreadSecType,
  MovAveSpreadCodeRow,
  MovAveSpreadCodesResponse,
  MovAveSpreadChartResponse,
  MovAveSpreadChartPairsResponse,
  MovAveSpreadChartEmaResponse,
  MovAveSpreadChartSharedResponse,
  MarketRegimeSpan,
  MarketRegimeSpansResponse,
  MovAveSpreadPairRow,
  MovAveSpreadSharedMetricsRow,
  MovAveSpreadAmtRow,
  MovAveSpreadMetric,
  MovAveSpreadBandsRow,
  MovAveSpreadOhlcGapDay,
  MovAveSpreadExtrasResponse,
  MovAveSpreadHighLowStreak,
  MovAveSpreadOhlcRow,
  MovAveSpreadPairSeries,
  MovAveSpreadPairKind,
  MovAveSpreadLatestGap,
  MovAveSpreadPriceVsAmtDay,
  MovAveSpreadPxVolSpeed,
  MovAveSpreadPxVolVolState,
  MovAveSpreadRsiRankDay,
  SectorNode,
  IndustryNode,
  StrategyNode,
} from "../../../shared/types.js";

/** The extras metric groups in canonical order — the /extras route's
 *  `metrics` param is validated against this list. */
const EXTRAS_METRICS: readonly MovAveSpreadMetric[] = [
  "amt", "ohlc", "bands", "streaks", "pxvol", "gaps", "rsiranks",
];

/** Parse + normalize the /extras `metrics` query param (comma-separated).
 *  Unknown names throw; duplicates collapse; output follows the canonical
 *  order. Empty input = no groups (an empty, valid response). */
function parseExtrasMetrics(raw: string | string[] | undefined | null): MovAveSpreadMetric[] {
  const parts = (Array.isArray(raw) ? raw : (raw ?? "").split(","))
    .map((s) => String(s).trim())
    .filter((s) => s.length > 0);
  for (const p of parts) {
    if (!EXTRAS_METRICS.includes(p as MovAveSpreadMetric)) {
      throw new Error(
        `Invalid metric '${p}'. Expected a comma-separated subset of: ${EXTRAS_METRICS.join(", ")}`,
      );
    }
  }
  return EXTRAS_METRICS.filter((m) => parts.includes(m));
}

const PX_VOL_SPEED_NAMES: ReadonlySet<string> = new Set([
  "sharp_up", "slow_up", "flat", "slow_dn", "sharp_dn",
]);
const PX_VOL_VOL_NAMES: ReadonlySet<string> = new Set([
  "heavy", "normal", "shrink",
]);

function isPxVolSpeed(v: string): v is MovAveSpreadPxVolSpeed {
  return PX_VOL_SPEED_NAMES.has(v);
}

function isPxVolVolState(v: string): v is MovAveSpreadPxVolVolState {
  return PX_VOL_VOL_NAMES.has(v);
}

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

/** The stored sec_type value of the analysis.mov_ave* tables: their
 *  stats.label_dim SMALLINT code (the label-code contract). Only the
 *  mov_ave queries bind it — stats.* tables (regime spans,
 *  per-sec_type source tables) keep the label string. */
function secTypeParam(st: MaSpreadSecType): number {
  return LABEL_CODE.sec_type[st];
}

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

// ----------------------------------------------------------------------------
//  DB row types — DEFAULT chart query (18 Simple-MA + EMA pair series +
//  the ONE shared per-date tooltip-metrics array; every other metric group
//  is served by getMovAveSpreadChartExtras on demand).
// ----------------------------------------------------------------------------
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
  // 5 EMA value columns from stats.{sec_type}_tech_stats (alias `t`).
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
  // EMA slope + EMA curvature columns from the EMA detail table — the
  // persisted EMA windows 6/20 (direct diffs of the EMA lines; the
  // 60/120/255-day derivatives were dropped across 2026-09).
  ema6_slope: number | null;
  ema20_slope: number | null;
  ema6_curvature: number | null;
  ema20_curvature: number | null;
  // 5 rolling population σ columns of price (Bollinger band widths) from the
  // EMA detail table (alias `ema`), aliased as ema_std_*days — the long_std
  // source for Price/EMA pair Bollinger envelopes.
  ema_std_5days: number | null;
  ema_std_20days: number | null;
  ema_std_60days: number | null;
  ema_std_120days: number | null;
  ema_std_255days: number | null;
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
}

// ---- DB row types — EXTRAS queries ----------------------------------------

/** One row of the "ohlc" extras group: the full 7-window rolling extrema
 *  (anchors + dates + line slopes + change/days since the last extreme)
 *  from analysis.mov_ave_spreads_roof_and_floor_lines, pivoted back to the
 *  per-window names MovAveSpreadOhlcRow carries. */
interface DbExtremaRow extends QueryResultRow {
  date: Date | string;
  [column: string]: unknown;
}

/** One row of the "amt" extras group: trading amount + high/low price
 *  reference + the 5 trading-amount MA values + their Bollinger σ columns. */
interface DbAmtRow extends QueryResultRow {
  date: Date | string;
  trading_amount: number | null;
  high: number | null;
  low: number | null;
  trading_amt_ma5: number | null;
  trading_amt_ma20: number | null;
  trading_amt_ma60: number | null;
  trading_amt_ma120: number | null;
  trading_amt_ma255: number | null;
  trading_amt_std5: number | null;
  trading_amt_std20: number | null;
  trading_amt_std60: number | null;
  trading_amt_std120: number | null;
  trading_amt_std255: number | null;
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

/** Pick the slope (1st derivative) of MA{window} from a chart row — a
 *  direct diff of the MA line (MA{W}[t] - MA{W}[t-1]). Only the
 *  persisted windows 5/20 return a value; 60/120/255 return null (their
 *  derivative columns were dropped across 2026-09). */
function pickSlope(r: DbChartRow, window: number): number | null {
  switch (window) {
    case 5:   return toNum(r.ma5_slope);
    case 20:  return toNum(r.ma20_slope);
    default:  return null;
  }
}

/** Pick the curvature (2nd derivative) of MA{window} from a chart row.
 *  Only the persisted windows 5/20 return a value (see pickSlope). */
function pickCurvature(r: DbChartRow, window: number): number | null {
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

/** Pick the trading-amount MA value for the given window from an amt
 *  extras row. Used to fill the 5 amt pair series' long_value and the
 *  envelope's MA columns (trading_amt_ma{W}). */
function pickTradingAmtMa(r: DbAmtRow, window: number): number | null {
  switch (window) {
    case 5:   return toNum(r.trading_amt_ma5);
    case 20:  return toNum(r.trading_amt_ma20);
    case 60:  return toNum(r.trading_amt_ma60);
    case 120: return toNum(r.trading_amt_ma120);
    case 255: return toNum(r.trading_amt_ma255);
    default:  return null;
  }
}

/** Pick the trading-amt Bollinger band σ for the given window from an amt
 *  extras row. Columns come from analysis.mov_ave_trading_amt. Used to set
 *  long_std on Amt/MA pair rows for Bollinger-style envelopes (MA ± k×σ). */
function pickTradingAmtStd(r: DbAmtRow, window: number): number | null {
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
 *  row — a direct diff of the EMA line (EMA{W}[t] - EMA{W}[t-1]), from
 *  analysis.mov_ave_spreads_detail_ema. Only the persisted windows 6/20
 *  return a value; 60/120/255 return null (dropped across 2026-09). */
function pickEmaSlope(r: DbChartRow, window: number): number | null {
  switch (window) {
    case 6:   return toNum(r.ema6_slope);
    case 20:  return toNum(r.ema20_slope);
    default:  return null;
  }
}

/** Pick the EMA curvature (2nd derivative) for the given window from a
 *  chart row. Only the persisted windows 6/20 return a value. */
function pickEmaCurvature(r: DbChartRow, window: number): number | null {
  switch (window) {
    case 6:   return toNum(r.ema6_curvature);
    case 20:  return toNum(r.ema20_curvature);
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
  /** Per-code prefetched source tables for the chart query (basic_stats
   *  INNER + adjustment/liquidity_margin/tech_stats LEFT). Each becomes a
   *  MATERIALIZED CTE restricted to the requested codes — the MATERIALIZED
   *  keyword is the optimization fence (the OFFSET 0 anti-flatten marker's
   *  replacement) — then joined on the exact PK columns. Replaces the old
   *  per-row LATERAL probes: those issued 12–14 index descents per history
   *  row (~1600 rows per stock code); the CTE version does ONE index range
   *  scan per table per request and hash-joins the small pre-filtered
   *  sets (55ms → 31ms on stock, and the win grows with history length). */
  chartTables: ChartSourceTable[];
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

/** One per-code prefetched chart source table. `withSecType` marks the
 *  analysis.* tables that carry a sec_type column (the stats.* tables are
 *  already per-sec_type). `inner` drops driving rows with no match — used
 *  for the required basic_stats. */
interface ChartSourceTable {
  alias: string;
  table: string;
  withSecType: boolean;
  inner: boolean;
}

/** analysis.* companions fetched by the DEFAULT chart request (EMA detail
 *  for the EMA pairs' gaps/derivatives/σ + RSI for the shared tooltip
 *  metrics). All carry sec_type. The trading-amount table is extras-only
 *  (TRADING_AMT_TABLE) — the default pair views don't need it. */
const CHART_ANALYSIS_TABLES: ChartSourceTable[] = [
  { alias: "ema", table: "analysis.mov_ave_spreads_detail_ema", withSecType: true, inner: false },
  { alias: "rsi", table: "analysis.mov_ave_rsi", withSecType: true, inner: false },
];

/** The trading-amount table (amt MA Bollinger σ) — fetched only by the
 *  "amt" extras group. */
const TRADING_AMT_TABLE: ChartSourceTable = {
  alias: "ta",
  table: "analysis.mov_ave_trading_amt",
  withSecType: true,
  inner: false,
};

const SEC_SOURCES: Record<MaSpreadSecType, SecSource> = {
  etf: {
    identityTable: "stats.etf_identity",
    chartTables: [
      { alias: "b", table: "stats.etf_basic_stats", withSecType: false, inner: true },
      { alias: "a", table: "stats.etf_adjustment", withSecType: false, inner: false },
      { alias: "lm", table: "stats.etf_liquidity_margin", withSecType: false, inner: false },
      { alias: "t", table: "stats.etf_tech_stats", withSecType: false, inner: false },
    ],
    priceExpr: "COALESCE(a.adj_close, b.close)",
    openExpr: "COALESCE(a.adj_open, b.open)",
    highExpr: "COALESCE(a.adj_high, b.high)",
    lowExpr: "COALESCE(a.adj_low, b.low)",
    tradingAmtExpr: "lm.trading_amount",
  },
  index: {
    identityTable: "stats.index_identity",
    chartTables: [
      { alias: "b", table: "stats.index_basic_stats", withSecType: false, inner: true },
      { alias: "t", table: "stats.index_tech_stats", withSecType: false, inner: false },
    ],
    priceExpr: "b.close",
    openExpr: "b.open",
    highExpr: "b.high",
    lowExpr: "b.low",
    tradingAmtExpr: "b.trading_amount",
  },
  stock: {
    identityTable: "stats.stock_identity",
    chartTables: [
      { alias: "b", table: "stats.stock_basic_stats", withSecType: false, inner: true },
      { alias: "lm", table: "stats.stock_liquidity_margin", withSecType: false, inner: false },
      { alias: "t", table: "stats.stock_tech_stats", withSecType: false, inner: false },
    ],
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
//  sort). Reads the PRECOMPUTED per-code summary table
//  analysis.mov_ave_spreads_codes (maintained by analysis.mov_ave_spread's
//  refresh_codes_summary after every detail write pass). The former live
//  aggregate — GROUP BY code over 18 MIN/MAX columns of the detail table +
//  per-code LATERAL latest-row and name lookups — took 7-14s on the stock
//  universe (~6.8M rows) and gated the page's first open; this is the same
//  result from a small indexed table (~10ms).
// ----------------------------------------------------------------------------
function buildCodesSql(): string {
  return `
    SELECT
      c.code,
      c.name,
      c.first_date,
      c.last_date,
      c.n_dates,
      c.latest_price_vs_ma5   AS price_vs_ma5,
      c.latest_price_vs_ma20  AS price_vs_ma20,
      c.latest_price_vs_ma60  AS price_vs_ma60,
      c.latest_price_vs_ma120 AS price_vs_ma120,
      c.latest_price_vs_ma255 AS price_vs_ma255,
      c.latest_ma5_vs_ma20    AS ma5_vs_ma20,
      c.latest_ma5_vs_ma60    AS ma5_vs_ma60,
      c.latest_ma5_vs_ma120   AS ma5_vs_ma120,
      c.latest_ma5_vs_ma255   AS ma5_vs_ma255,
      GREATEST(c.max_p5, c.max_p20, c.max_p60, c.max_p120, c.max_p255,
               c.max_5_20, c.max_5_60, c.max_5_120, c.max_5_255) AS max_gain,
      LEAST(c.min_p5, c.min_p20, c.min_p60, c.min_p120, c.min_p255,
            c.min_5_20, c.min_5_60, c.min_5_120, c.min_5_255)    AS max_loss,
      (GREATEST(c.max_p5, c.max_p20, c.max_p60, c.max_p120, c.max_p255,
                c.max_5_20, c.max_5_60, c.max_5_120, c.max_5_255)
       - LEAST(c.min_p5, c.min_p20, c.min_p60, c.min_p120, c.min_p255,
               c.min_5_20, c.min_5_60, c.min_5_120, c.min_5_255)) AS max_spread
    FROM analysis.mov_ave_spreads_codes c
    WHERE c.sec_type = $1
    ORDER BY max_spread DESC NULLS LAST, c.code
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

  const rows = await queryRows<DbCodeRow>(buildCodesSql(),
    [secTypeParam(secType)]);

  // When a classification filter is active, fetch the meta rows (same query as
  // listMovAveSpreadThemes) and build a code → classification map so
  // matchesClassification() can decide which codes to include. Industry and
  // strategy filters are mutually exclusive (handled by matchesClassification).
  let classMap: Map<string, DbMaSpreadMetaRow> | null = null;
  if (needMeta) {
    const metaType = MA_SPREAD_META_TYPE[secType];
    const metaRows = await queryRows<DbMaSpreadMetaRow>(MA_SPREAD_META_SQL,
      [secTypeParam(secType), metaType]);
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
//  getMovAveSpreadChart — the DEFAULT chart load for one asset: the 18
//  Simple-MA + EMA pair series (trimmed rows) + ONE shared per-date
//  tooltip-metrics array. The Amt/MA pairs, the OHLC extrema, the
//  band-break streaks, and the px-vol states are served on demand by
//  getMovAveSpreadChartExtras so the panel renders as soon as the pair
//  views are ready.
//
//  JOINs analysis.mov_ave_spreads_detail with the asset-appropriate source
//  tables (per-code prefetched CTEs, see chartCtesSql) to recover:
//    • price (COALESCE(adj_close, close) for ETFs; close for indices/stocks)
//    • ma5 / ma20 / ma60 / ma120 / ma255 + ema6 / ema20 / ema60 / ema120 / ema255
//  …alongside the precomputed gap_value columns. Client-side, each row is
//  fanned out into 18 pair series entries (short_value, long_value,
//  gap_value) plus one shared metrics row.
// ----------------------------------------------------------------------------

// analysis.mov_ave_spreads_roof_and_floor_lines (renamed 2026-10 from
// mov_ave_spreads_detail_ohlc) is LONG format — one row per
// (sec_type, code, date, period) with generic *_over_period columns. Every
// consumer pivots it in ONE prefetched CTE (one index range scan — the PK
// (code, sec_type, date, period) keeps a code's rows contiguous) and aliases
// the generic columns to the per-window names the API serves.
// Entries are [db_column, stem] pairs: most columns follow the
// `${stem}_over_period` naming; the change/days_since_last_high|low
// columns carry no suffix, so the db column is spelled out per stem.
const OHLC_STEMS = [
  ["open_over_period", "open"],
  ["high_over_period", "high"],
  ["high_date_over_period", "high_date"],
  ["low_over_period", "low"],
  ["low_date_over_period", "low_date"],
  ["high_2nd_over_period", "high_2nd"],
  ["high_2nd_date_over_period", "high_2nd_date"],
  ["low_2nd_over_period", "low_2nd"],
  ["low_2nd_date_over_period", "low_2nd_date"],
  ["high_line_slope_over_period", "high_line_slope"],
  ["low_line_slope_over_period", "low_line_slope"],
  ["change_since_last_high", "change_since_last_high"],
  ["days_since_last_high", "days_since_last_high"],
  ["change_since_last_low", "change_since_last_low"],
  ["days_since_last_low", "days_since_last_low"],
] as const;

/** The anchor/slope stems the DEFAULT chart's shared tooltip pivot needs —
 *  the change/days-since-last-high/low columns load only with the "ohlc"
 *  extras group (they belong to the roof/floor readout, which the extras
 *  group feeds). */
const OHLC_SHARED_STEMS = OHLC_STEMS.filter(
  ([, stem]) => !stem.startsWith("change_since_last") && !stem.startsWith("days_since_last"),
);

/** Windows whose rolling OHLC the DEFAULT chart's shared tooltip metrics
 *  carry — the 4 windows a pair's ma_long can take (ma_long ∈ 20/60/120/255;
 *  the tooltip shows them on SMA pairs matching the window). */
const OHLC_SHARED_PERIODS = [20, 60, 120, 255] as const;

/** The roof/floor windows the "ohlc" + "bands" extras groups load (the
 *  panel's Roof/Floor Trendlines row): 60…1275 — the 20d is NOT a
 *  roof/floor window (the shared tooltip block keeps its own 20d pivot
 *  for the Price/MA20 pair readout). */
const ROOF_FLOOR_PERIODS = [60, 120, 255, 500, 750, 1275] as const;

/** WITH-CTE fragment fetching the requested codes' long-format OHLC rows in
 *  one range scan and pivoting them to `{stem}_{p}d` columns per date. */
function ohlcPivotCteSql(
  periods: readonly number[],
  stems: readonly (readonly [string, string])[] = OHLC_STEMS,
): string {
  const pivot = periods.flatMap((p) =>
    stems.map(
      ([db, stem]) =>
        `MAX(o.${db}) FILTER (WHERE o.period = ${p}) AS ${stem}_${p}d`,
    ),
  ).join(",\n        ");
  return `ohlc_raw AS MATERIALIZED (
      SELECT * FROM analysis.mov_ave_spreads_roof_and_floor_lines
      WHERE sec_type = $1
        AND code = ANY($2::text[])
    ),
    ohlc AS MATERIALIZED (
      SELECT
        o.code,
        o.date,
        ${pivot}
      FROM ohlc_raw o
      GROUP BY o.code, o.date
    )`;
}

/** Final-SELECT fragment reading the pivoted `ohlc` CTE columns, aliased
 *  under `prefix` ("sh_" for the chart's shared tooltip metrics, "" for the
 *  ohlc extras group's MovAveSpreadOhlcRow names). */
function ohlcPivotSelectSql(
  periods: readonly number[],
  prefix: string,
  stems: readonly (readonly [string, string])[] = OHLC_STEMS,
): string {
  return periods.flatMap((p) =>
    stems.map(([db, stem]) => `ohlc.${stem}_${p}d AS ${prefix}${stem}_${p}d`),
  ).join(",\n      ");
}

/** WITH-CTE list prefetching every source table for the requested codes —
 *  the MATERIALIZED keyword fences the planner (the OFFSET 0 anti-flatten
 *  marker's replacement) so each table is read as ONE index range scan and
 *  hash-joined, instead of per-row index probes. */
function chartCtesSql(tables: ChartSourceTable[]): string {
  return tables
    .map((t) => {
      const where = t.withSecType
        ? "sec_type = $1 AND code = ANY($2::text[])"
        : "code = ANY($2::text[])";
      return (
        `${t.alias} AS MATERIALIZED (\n` +
        `      SELECT * FROM ${t.table}\n` +
        `      WHERE ${where}\n` +
        `    )`
      );
    })
    .join(",\n    ");
}

/** FROM-clause join list for the prefetched CTEs, on the exact PK equality
 *  (basic_stats INNER — drops driving rows with no price row). */
function chartJoinSql(tables: ChartSourceTable[]): string {
  return tables
    .map((t) => {
      const kw = t.inner ? "JOIN" : "LEFT JOIN";
      const secCond = t.withSecType ? `${t.alias}.sec_type = d.sec_type AND ` : "";
      return `${kw} ${t.alias} ON ${secCond}${t.alias}.code = d.code AND ${t.alias}.date = d.date`;
    })
    .join("\n    ");
}

// ----------------------------------------------------------------------------
//  Chart BLOCK SQL builders — the same CTE-prefetch pattern as the old
//  monolithic chart query, but scoped to ONE block's tables each, so a block
//  request never reads, transfers, or serializes the columns the other
//  blocks own (the monolithic /chart joined ~6 tables into ~90 columns; for
//  a full stock history the Node-side fan-out + JSON of that single response
//  dominated the latency).
// ----------------------------------------------------------------------------

/** REQUIRED pairs block: the stats price/MA tables joined to the detail
 *  gaps/derivatives — everything the default Price/MA5 chart + the 9
 *  Simple-MA chips need. No EMA detail, no RSI, no OHLC pivot. */
function buildChartPairsSql(secType: MaSpreadSecType): string {
  const src = SEC_SOURCES[secType];
  const tables = src.chartTables;
  return `
    WITH d AS MATERIALIZED (
      SELECT * FROM analysis.mov_ave_spreads_detail
      WHERE sec_type = $1
        AND code = ANY($2::text[])
    ),
    ${chartCtesSql(tables)}
    SELECT
      d.date,
      ${src.priceExpr} AS price,
      ${src.openExpr} AS open,
      ${src.highExpr} AS high,
      ${src.lowExpr} AS low,
      ${src.tradingAmtExpr} AS trading_amount,
      t.ma5, t.ma20, t.ma60, t.ma120, t.ma255,
      d.price_vs_ma5, d.price_vs_ma20, d.price_vs_ma60,
      d.price_vs_ma120, d.price_vs_ma255,
      d.ma5_vs_ma20, d.ma5_vs_ma60, d.ma5_vs_ma120, d.ma5_vs_ma255,
      d.price_slope, d.ma5_slope, d.ma20_slope,
      d.price_curvature, d.ma5_curvature, d.ma20_curvature,
      d.std_5days, d.std_20days, d.std_60days, d.std_120days, d.std_255days
    FROM d
    ${chartJoinSql(tables)}
    ORDER BY d.date ASC
  `;
}

/** Best-effort EMA block: the EMA detail table (gaps/derivatives/σ) plus
 *  the price columns the EMA pair rows carry. No RSI, no OHLC pivot. */
function buildChartEmaSql(secType: MaSpreadSecType): string {
  const src = SEC_SOURCES[secType];
  const tables = [...src.chartTables, ...CHART_ANALYSIS_TABLES.filter((t) => t.alias === "ema")];
  return `
    WITH d AS MATERIALIZED (
      SELECT * FROM analysis.mov_ave_spreads_detail
      WHERE sec_type = $1
        AND code = ANY($2::text[])
    ),
    ${chartCtesSql(tables)}
    SELECT
      d.date,
      ${src.priceExpr} AS price,
      ${src.openExpr} AS open,
      ${src.highExpr} AS high,
      ${src.lowExpr} AS low,
      ${src.tradingAmtExpr} AS trading_amount,
      t.ema6,
      d.price_slope, d.price_curvature,
      ema.ema6_slope, ema.ema20_slope,
      ema.ema6_curvature, ema.ema20_curvature,
      ema.price_vs_ema6, ema.price_vs_ema20, ema.price_vs_ema60,
      ema.price_vs_ema120, ema.price_vs_ema255,
      ema.ema6_vs_ema20, ema.ema6_vs_ema60, ema.ema6_vs_ema120, ema.ema6_vs_ema255,
      ema.std_5days AS ema_std_5days, ema.std_20days AS ema_std_20days,
      ema.std_60days AS ema_std_60days, ema.std_120days AS ema_std_120days,
      ema.std_255days AS ema_std_255days
    FROM d
    ${chartJoinSql(tables)}
    ORDER BY d.date ASC
  `;
}

/** Best-effort shared block: the tooltip metrics — RSI table + OHLC pivot +
 *  the detail table's amt slopes / market shares. No stats price tables
 *  (sec_type binds through the $1 parameter at call time). */
function buildChartSharedSql(): string {
  const tables = CHART_ANALYSIS_TABLES.filter((t) => t.alias === "rsi");
  return `
    WITH d AS MATERIALIZED (
      SELECT * FROM analysis.mov_ave_spreads_detail
      WHERE sec_type = $1
        AND code = ANY($2::text[])
    ),
    ${chartCtesSql(tables)},
    ${ohlcPivotCteSql(OHLC_SHARED_PERIODS, OHLC_SHARED_STEMS)}
    SELECT
      d.date,
      d.trading_amt_ma5_slope, d.trading_amt_ma20_slope, d.trading_amt_ma60_slope,
      d.trading_amt_ma120_slope, d.trading_amt_ma255_slope,
      d.trading_amt_market_share_ma5, d.trading_amt_market_share_ma20,
      d.trading_amt_market_share_ma60, d.trading_amt_market_share_ma120,
      d.trading_amt_market_share_ma255,
      rsi.rsi_3days, rsi.rsi_6days, rsi.rsi_10days, rsi.rsi_14days, rsi.rsi_20days,
      rsi.rsi_60days,
      rsi.is_typical_3days, rsi.is_typical_6days, rsi.is_typical_10days,
      rsi.is_typical_14days, rsi.is_typical_20days, rsi.is_typical_60days,
      ${ohlcPivotSelectSql(OHLC_SHARED_PERIODS, "sh_", OHLC_SHARED_STEMS)}
    FROM d
    ${chartJoinSql(tables)}
    LEFT JOIN ohlc ON ohlc.code = d.code AND ohlc.date = d.date
    ORDER BY d.date ASC
  `;
}

/** SQL for the "ohlc" extras group: the FULL 7-window extrema pivot driven
 *  off the detail rows' dates (index-aligned with the chart response;
 *  sec_type binds through the $1 parameter at call time). */
function buildOhlcExtrasSql(): string {
  return `
    WITH d AS MATERIALIZED (
      SELECT * FROM analysis.mov_ave_spreads_detail
      WHERE sec_type = $1
        AND code = ANY($2::text[])
    ),
    ${ohlcPivotCteSql(ROOF_FLOOR_PERIODS)}
    SELECT
      d.date,
      ${ohlcPivotSelectSql(ROOF_FLOOR_PERIODS, "")}
    FROM d
    LEFT JOIN ohlc ON ohlc.code = d.code AND ohlc.date = d.date
    ORDER BY d.date ASC
  `;
}

/** SQL for the "bands" extras group: the roof/floor shade-band pivot (all
 *  7 windows) driven off the detail rows' dates (index-aligned with the
 *  chart response; sec_type binds through the $1 parameter at call time).
 *  Per window: the 4 band edges — the ANCHOR-BAND event DATE arrays are
 *  served by buildBandsEventDatesSql (no NULL-safe array aggregate
 *  exists for a FILTER pivot) and merged per (date, period). */
function buildBandsExtrasSql(): string {
  const periods = ROOF_FLOOR_PERIODS;
  const pivot = periods
    .flatMap((p) => [
      `MAX(b.upper_band_low) FILTER (WHERE b.period = ${p}) AS upper_band_low_${p}d`,
      `MAX(b.upper_band_high) FILTER (WHERE b.period = ${p}) AS upper_band_high_${p}d`,
      `MAX(b.lower_band_low) FILTER (WHERE b.period = ${p}) AS lower_band_low_${p}d`,
      `MAX(b.lower_band_high) FILTER (WHERE b.period = ${p}) AS lower_band_high_${p}d`,
    ])
    .join(",\n        ");
  const select = periods
    .flatMap((p) => [
      `bands.upper_band_low_${p}d`,
      `bands.upper_band_high_${p}d`,
      `bands.lower_band_low_${p}d`,
      `bands.lower_band_high_${p}d`,
    ])
    .join(",\n      ");
  return `
    WITH d AS MATERIALIZED (
      SELECT * FROM analysis.mov_ave_spreads_detail
      WHERE sec_type = $1
        AND code = ANY($2::text[])
    ),
    bands AS MATERIALIZED (
      SELECT
        b.code,
        b.date,
        ${pivot}
      FROM analysis.mov_ave_spreads_roof_and_floor_bands b
      WHERE b.sec_type = $1
        AND b.code = ANY($2::text[])
      GROUP BY b.code, b.date
    )
    SELECT
      d.date,
      ${select}
    FROM d
    LEFT JOIN bands ON bands.code = d.code AND bands.date = d.date
    ORDER BY d.date ASC
  `;
}

/** SQL for the "amt" extras group: the 5 trading-amount pairs' per-date
 *  values (trading amount + high/low price reference + the 5 amt MA values
 *  + their Bollinger σ). */
function buildAmtSql(secType: MaSpreadSecType): string {
  const src = SEC_SOURCES[secType];
  const tables: ChartSourceTable[] = [...src.chartTables, TRADING_AMT_TABLE];
  return `
    WITH d AS MATERIALIZED (
      SELECT * FROM analysis.mov_ave_spreads_detail
      WHERE sec_type = $1
        AND code = ANY($2::text[])
    ),
    ${chartCtesSql(tables)}
    SELECT
      d.date,
      ${src.tradingAmtExpr} AS trading_amount,
      ${src.highExpr} AS high,
      ${src.lowExpr} AS low,
      d.trading_amt_ma5, d.trading_amt_ma20, d.trading_amt_ma60,
      d.trading_amt_ma120, d.trading_amt_ma255,
      ta.trading_amt_std5, ta.trading_amt_std20, ta.trading_amt_std60,
      ta.trading_amt_std120, ta.trading_amt_std255
    FROM d
    ${chartJoinSql(tables)}
    ORDER BY d.date ASC
  `;
}

/** SQL for the market-regime SPANS of one (sec_type, code): one row
 *  per CONTIGUOUS same-regime run, straight from the
 *  stats.market_regime_spans TABLE (the contiguous runs builds.market_regimes
 *  materializes over the daily stats.market_regimes registry — formerly
 *  a per-query gaps-and-islands VIEW). Replaces the retired
 *  mov_ave_market_hypes episode query; the UI shades the spans per
 *  regime (hot keeps the retired purple). */
function buildMarketRegimeSpansSql(): string {
  return `
    SELECT
      ${labelDecodeSql("regime", "s.regime")} AS regime,
      s.start_date,
      s.end_date,
      s.span_days::int AS span_days
    FROM stats.market_regime_spans s
    WHERE s.sec_type = $1
      AND s.code = ANY($2::text[])
    ORDER BY s.regime, s.start_date
  `;
}

/** SQL for one code's per-date Price × Trading-Amount state registry
 *  rows (analysis.mov_ave_price_vs_amt — the px_vol family's
 *  date-level source of truth, built by the mov_ave_spread pipeline).
 *  Every state-valid day joins exactly ONE of the 15
 *  px_speed × vol_state categories; the UI shades the matching dates
 *  per picked combo. */
/** SQL for one (sec_type, code)'s daily OHLC gap anatomy + pattern
 *  block (analysis.mov_ave_spreads_detail_ohlc_gaps — PK
 *  (code, sec_type, date) on HASH(code) partitions; estimated rows are
 *  excluded at build time). Serves the Top-10% OHLC Days group. */
function buildOhlcGapsSql(): string {
  return `
    SELECT
      g.date,
      g.gap_open::float8  AS gap_open,
      g.intra::float8     AS intra,
      g.uw::float8        AS uw,
      g.lw::float8        AS lw,
      g.uw_top10,
      g.lw_top10,
      g.pattern
    FROM analysis.mov_ave_spreads_detail_ohlc_gaps g
    WHERE g.sec_type = $1
      AND g.code = ANY($2::text[])
    ORDER BY g.date
  `;
}

/** DB row → API shape (fraction legs to num, booleans as-is, dates
 *  ISO-formatted). */
function toOhlcGapDay(r: DbOhlcGapRow): MovAveSpreadOhlcGapDay {
  return {
    date: formatDate(r.date),
    gap_open: toNum(r.gap_open),
    intra: toNum(r.intra),
    uw: toNum(r.uw),
    lw: toNum(r.lw),
    uw_top10: r.uw_top10 === true,
    lw_top10: r.lw_top10 === true,
    pattern: r.pattern ?? null,
  };
}

function buildPriceVsAmtSql(): string {
  return `
    SELECT
      p.date,
      ${labelDecodeSql("px_speed", "p.px_speed")} AS px_speed,
      ${labelDecodeSql("vol_state", "p.vol_state")} AS vol_state
    FROM analysis.mov_ave_price_vs_amt p
    WHERE p.sec_type = $1
      AND p.code = ANY($2::text[])
    ORDER BY p.date
  `;
}

/** SQL for the "rsiranks" extras group: one code's per-date RSI history
 *  ranks (analysis.mov_ave_rsi_ranks — rank 1 = the code's highest RSI
 *  reading within its own history, dense 1..N, NULL where that window's
 *  RSI is NULL). The client derives each window's top-% cutoff from the
 *  max rank over the rows (dense ranks ⇒ max = the count of ranked
 *  days), so no totals row is fetched. */
function buildRsiRanksSql(): string {
  return `
    SELECT
      r.date,
      r.rank_rsi_3days,
      r.rank_rsi_6days,
      r.rank_rsi_10days,
      r.rank_rsi_14days,
      r.rank_rsi_20days,
      r.rank_rsi_60days
    FROM analysis.mov_ave_rsi_ranks r
    WHERE r.sec_type = $1
      AND r.code = ANY($2::text[])
    ORDER BY r.date
  `;
}

/** DB row → API shape of the "rsiranks" extras group (ranks arrive as
 *  real int4 / NULL). */
function toRsiRankDay(r: DbRsiRankRow): MovAveSpreadRsiRankDay {
  return {
    date: formatDate(r.date),
    rank_rsi_3days: toNum(r.rank_rsi_3days),
    rank_rsi_6days: toNum(r.rank_rsi_6days),
    rank_rsi_10days: toNum(r.rank_rsi_10days),
    rank_rsi_14days: toNum(r.rank_rsi_14days),
    rank_rsi_20days: toNum(r.rank_rsi_20days),
    rank_rsi_60days: toNum(r.rank_rsi_60days),
  };
}

/** SQL for the high/low band-BREAK excursion streaks of one (sec_type,
 *  code), straight from analysis.mov_ave_high_low_pct_streaks (PK
 *  (sec_type, code, date_year_month, period, pct_type, start_date,
 *  end_date)). The streak SIDE (high leg vs low leg) is not stored — it is
 *  derived here by joining the band row of the streak's END month
 *  (date_trunc('month', s.end_date)) and comparing the streak's end close
 *  against that band: each streak day is tested against its OWN month's
 *  band (so the end-month band row is guaranteed to exist) and a streak
 *  never switches sides, so the end day's own-month band decides exactly.
 *  Fetched once per chart request for ALL (period, pct_type) combos — the
 *  client filters by its nested period→pct selection. */
function buildStreaksSql(): string {
  return `
    SELECT
      s.period,
      s.pct_type,
      s.start_date,
      s.end_date,
      s.open,
      s.close,
      s.high,
      s.low,
      b.high_val AS band_high,
      b.low_val AS band_low,
      s.day_count,
      s.std_dev,
      s.daily_ave_trading_amt,
      CASE
        WHEN s.close > b.high_val THEN 'high'
        WHEN s.close < b.low_val THEN 'low'
      END AS side
    FROM analysis.mov_ave_high_low_pct_streaks s
    JOIN analysis.mov_ave_high_low_pct b
      ON b.sec_type = s.sec_type
      AND b.code = s.code
      AND b.date_year_month = date_trunc('month', s.end_date)::date
      AND b.period = s.period
      AND b.pct_type = s.pct_type
    WHERE s.sec_type = $1
      AND s.code = ANY($2::text[])
    ORDER BY s.period, s.pct_type, s.start_date
  `;
}

function buildNameSql(secType: MaSpreadSecType): string {
  const src = SEC_SOURCES[secType];
  // DISTINCT ON (code) + ORDER BY code, date DESC walks the (code, date) PK
  // backwards PER CODE — one index probe per variant. The naive
  // `ORDER BY date DESC LIMIT 1` form drove a date-index backward scan that
  // filtered EVERY row of every touched partition before finding a match
  // (~470k rows/partition, 4.4s measured 2026-09-29); the subselect wrapper
  // then picks the overall-latest variant's row.
  return `
    SELECT name
    FROM (
      SELECT DISTINCT ON (code) code, name, date
      FROM ${src.identityTable}
      WHERE code = ANY($1::text[])
      ORDER BY code, date DESC
    ) latest
    ORDER BY date DESC
    LIMIT 1
  `;
}

/** Map one DB extrema row to a top-level ohlc row (all 7 windows).
 *  Used to build the "ohlc" extras group — ONE copy per date shared by all
 *  pair series (instead of fanning the extrema out into every pair's rows,
 *  which would multiply the payload by the pair count). */
/** One pivoted row of the "bands" extras group (pg returns NUMERIC as
 *  string; DATE[] columns come back as Date[] | null). */
interface DbBandsRow extends QueryResultRow {
  date: string | Date;
  [k: string]: unknown;
}

function toBandsRow(r: DbBandsRow): MovAveSpreadBandsRow {
  const out: Record<string, unknown> = { date: formatDate(r.date) };
  for (const p of ROOF_FLOOR_PERIODS) {
    out[`upper_band_low_${p}d`] = toNum(r[`upper_band_low_${p}d`]);
    out[`upper_band_high_${p}d`] = toNum(r[`upper_band_high_${p}d`]);
    out[`lower_band_low_${p}d`] = toNum(r[`lower_band_low_${p}d`]);
    out[`lower_band_high_${p}d`] = toNum(r[`lower_band_high_${p}d`]);
    // Event date arrays merged per (date, period) by
    // mergeBandsEventDates; default null until then.
    out[`upper_breach_dates_${p}d`] = null;
    out[`lower_breach_dates_${p}d`] = null;
    out[`upper_bounce_dates_${p}d`] = null;
    out[`lower_bounce_dates_${p}d`] = null;
  }
  return out as unknown as MovAveSpreadBandsRow;
}

/** One plain (date, period) row of the bands table's event arrays. */
interface DbBandsEventDatesRow extends QueryResultRow {
  date: string | Date;
  period: number;
  upper_breach_dates: unknown;
  lower_breach_dates: unknown;
  upper_bounce_dates: unknown;
  lower_bounce_dates: unknown;
}

/** SQL for the bands table's ANCHOR-BAND event DATE arrays: one plain
 *  (date, period) row, no FILTER pivot — ARRAY_AGG over an empty
 *  filter raises "cannot accumulate empty arrays" for dates lacking
 *  that period's row, and arrays have no NULL-safe pivot aggregate
 *  (MAX works for the numeric edges only). The service merges these
 *  into the pivoted bands rows by (date, period). */
function buildBandsEventDatesSql(): string {
  return `
    SELECT
      b.date,
      b.period,
      b.upper_band_breach_dates AS upper_breach_dates,
      b.lower_band_breach_dates AS lower_breach_dates,
      b.upper_band_bounce_dates AS upper_bounce_dates,
      b.lower_band_bounce_dates AS lower_bounce_dates
    FROM analysis.mov_ave_spreads_roof_and_floor_bands b
    WHERE b.sec_type = $1
      AND b.code = ANY($2::text[])
    ORDER BY b.date
  `;
}

/** Merge the (date, period) event-date arrays into the pivoted bands
 *  rows — the anchor row's stored breach/bounce days of its OWN band,
 *  which the UI reads directly as its marker set. */
function mergeBandsEventDates(
  bandsRows: MovAveSpreadBandsRow[],
  eventRows: DbBandsEventDatesRow[],
): MovAveSpreadBandsRow[] {
  if (bandsRows.length === 0) return bandsRows;
  const byDate = new Map<string, Record<string, unknown>>();
  for (const r of bandsRows) byDate.set(r.date, r as unknown as Record<string, unknown>);
  const dateArr = (v: unknown): string[] | null =>
    Array.isArray(v) ? v.map((d) => formatDate(d)) : null;
  const periods = ROOF_FLOOR_PERIODS as readonly number[];
  for (const e of eventRows) {
    if (!periods.includes(e.period)) continue;
    const row = byDate.get(formatDate(e.date));
    if (row == null) continue;
    row[`upper_breach_dates_${e.period}d`] = dateArr(e.upper_breach_dates);
    row[`lower_breach_dates_${e.period}d`] = dateArr(e.lower_breach_dates);
    row[`upper_bounce_dates_${e.period}d`] = dateArr(e.upper_bounce_dates);
    row[`lower_bounce_dates_${e.period}d`] = dateArr(e.lower_bounce_dates);
  }
  return bandsRows;
}

function toOhlcExtremaRow(r: DbExtremaRow): MovAveSpreadOhlcRow {
  const d = (v: unknown): string | null =>
    v != null ? formatDate(v) : null;
  return {
    date: formatDate(r.date),
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
    change_since_last_high_60d: toNum(r.change_since_last_high_60d),
    days_since_last_high_60d: toNum(r.days_since_last_high_60d),
    change_since_last_low_60d: toNum(r.change_since_last_low_60d),
    days_since_last_low_60d: toNum(r.days_since_last_low_60d),
    change_since_last_high_120d: toNum(r.change_since_last_high_120d),
    days_since_last_high_120d: toNum(r.days_since_last_high_120d),
    change_since_last_low_120d: toNum(r.change_since_last_low_120d),
    days_since_last_low_120d: toNum(r.days_since_last_low_120d),
    change_since_last_high_255d: toNum(r.change_since_last_high_255d),
    days_since_last_high_255d: toNum(r.days_since_last_high_255d),
    change_since_last_low_255d: toNum(r.change_since_last_low_255d),
    days_since_last_low_255d: toNum(r.days_since_last_low_255d),
    change_since_last_high_500d: toNum(r.change_since_last_high_500d),
    days_since_last_high_500d: toNum(r.days_since_last_high_500d),
    change_since_last_low_500d: toNum(r.change_since_last_low_500d),
    days_since_last_low_500d: toNum(r.days_since_last_low_500d),
    change_since_last_high_750d: toNum(r.change_since_last_high_750d),
    days_since_last_high_750d: toNum(r.days_since_last_high_750d),
    change_since_last_low_750d: toNum(r.change_since_last_low_750d),
    days_since_last_low_750d: toNum(r.days_since_last_low_750d),
    change_since_last_high_1275d: toNum(r.change_since_last_high_1275d),
    days_since_last_high_1275d: toNum(r.days_since_last_high_1275d),
    change_since_last_low_1275d: toNum(r.change_since_last_low_1275d),
    days_since_last_low_1275d: toNum(r.days_since_last_low_1275d),
  };
}

/** One span row from stats.market_regime_spans (see
 *  buildMarketRegimeSpansSql). */
interface DbMarketRegimeSpanRow {
  regime: string;
  start_date: Date | string;
  end_date: Date | string;
  span_days: number;
}

/** Group the span rows into the response's per-regime map
 *  (regime → spans ascending by startDate; regimes with no spans are
 *  absent — calm spans are served too, the UI decides what to shade). */
function toMarketRegimeSpans(
  rows: DbMarketRegimeSpanRow[],
): Record<string, MarketRegimeSpan[]> {
  const out: Record<string, MarketRegimeSpan[]> = {};
  for (const r of rows) {
    (out[r.regime] ??= []).push({
      regime: r.regime as MarketRegimeSpan["regime"],
      startDate: formatDate(r.start_date),
      endDate: formatDate(r.end_date),
      spanDays: Number(r.span_days),
    });
  }
  return out;
}

/** One state-registry row from analysis.mov_ave_price_vs_amt (see
 *  buildPriceVsAmtSql). */
interface DbPriceVsAmtRow {
  date: Date | string;
  px_speed: string;
  vol_state: string;
}

/** One analysis.mov_ave_rsi_ranks row (see buildRsiRanksSql) — int4
 *  ranks arrive as numbers, NULLs as null. */
interface DbRsiRankRow {
  date: Date | string;
  rank_rsi_3days: number | null;
  rank_rsi_6days: number | null;
  rank_rsi_10days: number | null;
  rank_rsi_14days: number | null;
  rank_rsi_20days: number | null;
  rank_rsi_60days: number | null;
}

/** Map the registry rows into the response's per-date state array
 *  (ascending by date). Unknown category names (a future rebuild with
 *  extended states) are dropped — the UI shades only known cells. */
function toPriceVsAmtDays(rows: DbPriceVsAmtRow[]): MovAveSpreadPriceVsAmtDay[] {
  const out: MovAveSpreadPriceVsAmtDay[] = [];
  for (const r of rows) {
    if (
      !isPxVolSpeed(r.px_speed) || !isPxVolVolState(r.vol_state)
    ) {
      continue;
    }
    out.push({
      date: formatDate(r.date),
      speed: r.px_speed,
      vol: r.vol_state,
    });
  }
  return out;
}

/** One streak row from analysis.mov_ave_high_low_pct_streaks joined with
 *  its end-month band (see buildStreaksSql). side is NULL only if the
 *  end close fell exactly on a band boundary — never expected. */
/** One analysis.mov_ave_spreads_detail_ohlc_gaps row as pg hands it
 *  over (boolean flags arrive as real bools; int8/numeric through ::float8). */
interface DbOhlcGapRow {
  date: Date | string;
  gap_open: number | null;
  intra: number | null;
  uw: number | null;
  lw: number | null;
  uw_top10: boolean | null;
  lw_top10: boolean | null;
  pattern: string | null;
}

interface DbStreakRow {
  period: number;
  pct_type: number;
  start_date: Date | string;
  end_date: Date | string;
  open: number | string | null;
  close: number | string | null;
  high: number | string | null;
  low: number | string | null;
  band_high: number | string | null;
  band_low: number | string | null;
  day_count: number;
  std_dev: number | string | null;
  daily_ave_trading_amt: number | string | null;
  side: string | null;
}

/** Map the streak rows into the response's FLAT per-streak array
 *  (ascending by period, pctType, startDate; rows with an unusable side
 *  are dropped). */
function toStreaks(rows: DbStreakRow[]): MovAveSpreadHighLowStreak[] {
  const out: MovAveSpreadHighLowStreak[] = [];
  for (const r of rows) {
    if (r.side !== "high" && r.side !== "low") continue;
    out.push({
      period: r.period,
      pctType: r.pct_type,
      startDate: formatDate(r.start_date),
      endDate: formatDate(r.end_date),
      side: r.side,
      open: toNum(r.open) ?? 0,
      close: toNum(r.close) ?? 0,
      high: toNum(r.high) ?? 0,
      low: toNum(r.low) ?? 0,
      bandHigh: toNum(r.band_high) ?? 0,
      bandLow: toNum(r.band_low) ?? 0,
      dayCount: r.day_count,
      stdDev: toNum(r.std_dev) ?? 0,
      dailyAveTradingAmt: toNum(r.daily_ave_trading_amt) ?? 0,
    });
  }
  return out;
}

/** Initialize the 9 pair series of one family (Simple MA or EMA) in
 *  canonical order — shared by the pairs/ema block mappers below. */
function initPairSeries(
  order: readonly (readonly [number, number, ...unknown[]])[],
  kind: MovAveSpreadPairKind,
  prefix: string,
): Map<string, MovAveSpreadPairSeries> {
  const byPair = new Map<string, MovAveSpreadPairSeries>();
  for (const [ms, ml] of order) {
    byPair.set(`${prefix}${ms}/${ml}`, {
      ma_short: ms,
      ma_long: ml,
      pair_label: pairLabel(ms, ml, kind),
      kind,
      rows: [],
    });
  }
  return byPair;
}

/** Pairs-block mapper: fan each date out into the 9 Simple-MA pair rows. */
function mapPricePairRows(chartRows: DbChartRow[]): MovAveSpreadPairSeries[] {
  const byPair = initPairSeries(PAIR_ORDER, "price", "price-");
  for (const r of chartRows) {
    const dateStr = formatDate(r.date);
    const price = toNum(r.price);
    const ma5 = toNum(r.ma5);
    const open = toNum(r.open);
    const high = toNum(r.high);
    const low = toNum(r.low);
    const tradingAmount = toNum(r.trading_amount);
    for (const [maShort, maLong, gapCol] of PAIR_ORDER) {
      const series = byPair.get(`price-${maShort}/${maLong}`);
      if (!series) continue;
      const shortVal = maShort === 0 ? price : ma5;
      // Derivatives: the DB-stored direct diffs (value[t] - value[t-1])
      // — price_slope/price_curvature for the price leg, ma{5,20}_* for
      // the MA legs; windows beyond 20 have no stored derivatives (null).
      const row: MovAveSpreadPairRow = {
        date: dateStr,
        short_value: shortVal,
        long_value: pickLong(r, maLong),
        gap_value: toNum(r[gapCol as keyof DbChartRow]),
        short_slope: maShort === 0 ? toNum(r.price_slope) : pickSlope(r, maShort),
        short_curvature: maShort === 0 ? toNum(r.price_curvature) : pickCurvature(r, maShort),
        long_slope: pickSlope(r, maLong),
        long_curvature: pickCurvature(r, maLong),
        long_std: pickStd(r, maLong),
        open,
        high,
        low,
        trading_amount: tradingAmount,
      };
      series.rows.push(row);
    }
  }
  return PAIR_ORDER.map(([ms, ml]) => byPair.get(`price-${ms}/${ml}`)!);
}

/** Ema-block mapper: fan each date out into the 9 EMA pair rows.
 *  short = price (ma_short=0) or ema6 (ma_short=6); long = emaW.
 *  gap_value / slope / curvature come from the EMA detail table (alias
 *  `ema`); long_std is the rolling price σ for the long EMA's window. */
function mapEmaPairRows(chartRows: DbChartRow[]): MovAveSpreadPairSeries[] {
  const byPair = initPairSeries(EMA_PAIR_ORDER, "ema", "ema-");
  for (const r of chartRows) {
    const dateStr = formatDate(r.date);
    const price = toNum(r.price);
    const ema6 = toNum(r.ema6);
    const open = toNum(r.open);
    const high = toNum(r.high);
    const low = toNum(r.low);
    const tradingAmount = toNum(r.trading_amount);
    for (const [maShort, maLong, gapCol] of EMA_PAIR_ORDER) {
      const series = byPair.get(`ema-${maShort}/${maLong}`);
      if (!series) continue;
      const row: MovAveSpreadPairRow = {
        date: dateStr,
        short_value: maShort === 0 ? price : ema6,
        long_value: pickEmaLong(r, maLong),
        gap_value: toNum(r[gapCol as keyof DbChartRow]),
        // Derivatives: price legs carry the SMA table's price_slope /
        // price_curvature; EMA legs the EMA table's stored ema{6,20}_*
        // direct diffs (windows beyond 20 null).
        short_slope: maShort === 0 ? toNum(r.price_slope) : pickEmaSlope(r, maShort),
        short_curvature: maShort === 0 ? toNum(r.price_curvature) : pickEmaCurvature(r, maShort),
        long_slope: pickEmaSlope(r, maLong),
        long_curvature: pickEmaCurvature(r, maLong),
        long_std: pickEmaStd(r, maLong),
        open,
        high,
        low,
        trading_amount: tradingAmount,
      };
      series.rows.push(row);
    }
  }
  return EMA_PAIR_ORDER.map(([ms, ml]) => byPair.get(`ema-${ms}/${ml}`)!);
}

/** Shared-block mapper: the per-date tooltip-metrics rows (RSI /
 *  trading-amt slope+share / rolling-window OHLC). */
function mapSharedRows(chartRows: DbChartRow[]): MovAveSpreadSharedMetricsRow[] {
  return chartRows.map((r) => ({
    date: formatDate(r.date),
    rsi_3days: toNum(r.rsi_3days),
    rsi_6days: toNum(r.rsi_6days),
    rsi_10days: toNum(r.rsi_10days),
    rsi_14days: toNum(r.rsi_14days),
    rsi_20days: toNum(r.rsi_20days),
    rsi_60days: toNum(r.rsi_60days),
    is_typical_3days: toNum(r.is_typical_3days),
    is_typical_6days: toNum(r.is_typical_6days),
    is_typical_10days: toNum(r.is_typical_10days),
    is_typical_14days: toNum(r.is_typical_14days),
    is_typical_20days: toNum(r.is_typical_20days),
    is_typical_60days: toNum(r.is_typical_60days),
    trading_amt_ma5_slope: toNum(r.trading_amt_ma5_slope),
    trading_amt_ma20_slope: toNum(r.trading_amt_ma20_slope),
    trading_amt_ma60_slope: toNum(r.trading_amt_ma60_slope),
    trading_amt_ma120_slope: toNum(r.trading_amt_ma120_slope),
    trading_amt_ma255_slope: toNum(r.trading_amt_ma255_slope),
    trading_amt_market_share_ma5: toNum(r.trading_amt_market_share_ma5),
    trading_amt_market_share_ma20: toNum(r.trading_amt_market_share_ma20),
    trading_amt_market_share_ma60: toNum(r.trading_amt_market_share_ma60),
    trading_amt_market_share_ma120: toNum(r.trading_amt_market_share_ma120),
    trading_amt_market_share_ma255: toNum(r.trading_amt_market_share_ma255),
    open_20d: toNum(r.sh_open_20d),
    high_20d: toNum(r.sh_high_20d),
    low_20d: toNum(r.sh_low_20d),
    open_60d: toNum(r.sh_open_60d),
    high_60d: toNum(r.sh_high_60d),
    low_60d: toNum(r.sh_low_60d),
    open_120d: toNum(r.sh_open_120d),
    high_120d: toNum(r.sh_high_120d),
    low_120d: toNum(r.sh_low_120d),
    open_255d: toNum(r.sh_open_255d),
    high_255d: toNum(r.sh_high_255d),
    low_255d: toNum(r.sh_low_255d),
  }));
}

/** REQUIRED chart block — the 9 Simple-MA pair series + the display name.
 *  Drives the default Price/MA5 chart, the MA chip grid, and the shared
 *  date axis; its landing unblocks the panel's first render. */
export async function getMovAveSpreadChartPairs(
  rawCode: string,
  rawSecType: string | undefined | null,
): Promise<MovAveSpreadChartPairsResponse> {
  const secType = normalizeSecType(rawSecType);
  const target = stripped(rawCode);
  const variants = codeVariants(target);
  const [chartRows, nameRows] = await Promise.all([
    queryRows<DbChartRow>(buildChartPairsSql(secType),
      [secTypeParam(secType), variants]),
    queryRows<{ name: string | null }>(buildNameSql(secType), [variants]),
  ]);
  return {
    code: target,
    name: nameRows[0]?.name ?? "",
    pairs: mapPricePairRows(chartRows),
  };
}

/** Best-effort chart block — the 9 EMA pair series (EMA detail table). The
 *  EMA chip grid populates when this lands; a failure just leaves those
 *  chips absent (the frontend guards on pair presence). */
export async function getMovAveSpreadChartEma(
  rawCode: string,
  rawSecType: string | undefined | null,
): Promise<MovAveSpreadChartEmaResponse> {
  const secType = normalizeSecType(rawSecType);
  const target = stripped(rawCode);
  const variants = codeVariants(target);
  const chartRows = await queryRows<DbChartRow>(buildChartEmaSql(secType),
      [secTypeParam(secType), variants]);
  return { code: target, pairs: mapEmaPairRows(chartRows) };
}

/** Best-effort chart block — the per-date tooltip metrics (RSI table +
 *  OHLC pivot + detail amt slopes/market shares). The chart's tooltip
 *  formatters already tolerate its absence. */
export async function getMovAveSpreadChartShared(
  rawCode: string,
  rawSecType: string | undefined | null,
): Promise<MovAveSpreadChartSharedResponse> {
  const secType = normalizeSecType(rawSecType);
  const target = stripped(rawCode);
  const variants = codeVariants(target);
  const chartRows = await queryRows<DbChartRow>(buildChartSharedSql(),
      [secTypeParam(secType), variants]);
  return { code: target, shared: mapSharedRows(chartRows) };
}

/** The DEFAULT full chart response — composition of the three blocks (kept
 *  for the monolithic /chart endpoint and any non-panel consumer; the panel
 *  itself fetches the blocks directly for progressive rendering). */
export async function getMovAveSpreadChart(
  rawCode: string,
  rawSecType: string | undefined | null,
): Promise<MovAveSpreadChartResponse> {
  const [pairs, ema, shared] = await Promise.all([
    getMovAveSpreadChartPairs(rawCode, rawSecType),
    getMovAveSpreadChartEma(rawCode, rawSecType),
    getMovAveSpreadChartShared(rawCode, rawSecType),
  ]);
  return {
    code: pairs.code,
    name: pairs.name,
    pairs: [...pairs.pairs, ...ema.pairs],
    shared: shared.shared,
  };
}

/** Build the 5 Amt/MA pair series from the "amt" extras rows. The envelope
 *  draws all 5 trading_amt_ma lines; long_std carries trading_amt_stdW (the
 *  amt Bollinger band); gap_value is computed here (no pre-computed amt gap
 *  column exists). Slopes / market shares for the tooltip come from the
 *  chart response's shared metrics — not duplicated per row. */
function toAmtPairs(rows: DbAmtRow[]): MovAveSpreadPairSeries[] {
  const byPair = new Map<string, MovAveSpreadPairSeries>();
  for (const [ms, ml] of AMT_PAIR_ORDER) {
    byPair.set(`amt-${ms}/${ml}`, {
      ma_short: ms,
      ma_long: ml,
      pair_label: pairLabel(ms, ml),
      kind: "amt" as MovAveSpreadPairKind,
      rows: [],
    });
  }
  for (const r of rows) {
    const dateStr = formatDate(r.date);
    const amt = toNum(r.trading_amount);
    const high = toNum(r.high);
    const low = toNum(r.low);
    for (const [maShort, maLong] of AMT_PAIR_ORDER) {
      const series = byPair.get(`amt-${maShort}/${maLong}`);
      if (!series) continue;
      const longVal = pickTradingAmtMa(r, maLong);
      const amtRow: MovAveSpreadAmtRow = {
        date: dateStr,
        short_value: amt,
        long_value: longVal,
        gap_value:
          amt != null && longVal != null && longVal !== 0
            ? (amt - longVal) / longVal
            : null,
        short_slope: null,
        short_curvature: null,
        long_slope: null,
        long_curvature: null,
        long_std: pickTradingAmtStd(r, maLong),
        open: null,
        high,
        low,
        trading_amount: amt,
        trading_amt_ma5: pickTradingAmtMa(r, 5),
        trading_amt_ma20: pickTradingAmtMa(r, 20),
        trading_amt_ma60: pickTradingAmtMa(r, 60),
        trading_amt_ma120: pickTradingAmtMa(r, 120),
        trading_amt_ma255: pickTradingAmtMa(r, 255),
      };
      series.rows.push(amtRow);
    }
  }
  return AMT_PAIR_ORDER.map(([ms, ml]) => byPair.get(`amt-${ms}/${ml}`)!);
}

/**
 * getMovAveSpreadChartExtras — the on-demand metric groups for one asset.
 * Served by GET /extras?sec_type=…&code=…&metrics=amt,ohlc,streaks,pxvol.
 * Only the requested groups are queried (in parallel) and present in the
 * response — the MA-Spread panel fetches a group the first time one of its
 * control buttons is picked.
 */
export async function getMovAveSpreadChartExtras(
  rawCode: string,
  rawSecType: string | undefined | null,
  rawMetrics: string | string[] | undefined | null,
): Promise<MovAveSpreadExtrasResponse> {
  const secType = normalizeSecType(rawSecType);
  const target = stripped(rawCode);
  const variants = codeVariants(target);
  const wanted = parseExtrasMetrics(rawMetrics);

  const response: MovAveSpreadExtrasResponse = {
    code: target,
    sec_type: secType,
    metrics: wanted,
  };

  await Promise.all(
    wanted.map(async (metric) => {
      switch (metric) {
        case "amt": {
          const rows = await queryRows<DbAmtRow>(buildAmtSql(secType),
            [secTypeParam(secType), variants]);
          response.amtPairs = toAmtPairs(rows);
          break;
        }
        case "ohlc": {
          const rows = await queryRows<DbExtremaRow>(
            buildOhlcExtrasSql(),
            [secTypeParam(secType), variants],
          );
          response.ohlc = rows.map(toOhlcExtremaRow);
          break;
        }
        case "bands": {
          const [rows, eventRows] = await Promise.all([
            queryRows<DbBandsRow>(buildBandsExtrasSql(), [secTypeParam(secType), variants]),
            queryRows<DbBandsEventDatesRow>(buildBandsEventDatesSql(), [secTypeParam(secType), variants]),
          ]);
          response.roofFloorBands = mergeBandsEventDates(rows.map(toBandsRow), eventRows);
          break;
        }
        case "streaks": {
          const rows = await queryRows<DbStreakRow>(buildStreaksSql(),
            [secTypeParam(secType), variants]);
          response.highLowStreaks = toStreaks(rows);
          break;
        }
        case "pxvol": {
          const rows = await queryRows<DbPriceVsAmtRow>(buildPriceVsAmtSql(),
            [secTypeParam(secType), variants]);
          response.priceVsAmt = toPriceVsAmtDays(rows);
          break;
        }
        case "gaps": {
          const rows = await queryRows<DbOhlcGapRow>(buildOhlcGapsSql(),
            [secTypeParam(secType), variants]);
          response.ohlcGaps = rows.map(toOhlcGapDay);
          break;
        }
        case "rsiranks": {
          const rows = await queryRows<DbRsiRankRow>(
            buildRsiRanksSql(), [secTypeParam(secType), variants],
          );
          response.rsiRanks = rows.map(toRsiRankDay);
          break;
        }
      }
    }),
  );

  return response;
}

// ----------------------------------------------------------------------------
//  getMarketRegimeSpans — the market-regime SPANS of one (sec_type,
//  code) from the stats.market_regime_spans table (contiguous same-regime
//  runs materialized by builds.market_regimes over the daily
//  stats.market_regimes registry). Replaces the retired
//  getMarketHypeEpisodes (mov_ave_market_hypes episodes): the
//  shared CodeTrendChart's Regimes toggle and the MA-Spread panel's
//  regime shading fetch spans on demand via GET
//  /api/analysis/market-regimes.
// ----------------------------------------------------------------------------
export async function getMarketRegimeSpans(
  rawCode: string,
  rawSecType: string | undefined | null,
): Promise<MarketRegimeSpansResponse> {
  const secType = normalizeSecType(rawSecType);
  const target = stripped(rawCode);
  const variants = codeVariants(target);
  const rows = await queryRows<DbMarketRegimeSpanRow>(
    buildMarketRegimeSpansSql(),
    [secType, variants],
  );
  return { secType, code: target, spans: toMarketRegimeSpans(rows) };
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
 *  listMovAveSpreadStrategyThemes(). Returns one row per code WITH analysis
 *  rows (from the precomputed per-code summary table — the former
 *  `SELECT DISTINCT code` over the detail table scanned all ~6.8M stock
 *  rows per request) with its precomputed L1/L2 classification from
 *  stats.sec_classification. is_industry_not_strategy distinguishes
 *  industry-primary (TRUE) from strategy-primary (FALSE) rows. */
const MA_SPREAD_META_SQL = `
  WITH spread_codes AS (
    SELECT code
    FROM analysis.mov_ave_spreads_codes
    WHERE sec_type = $1
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
  const rows = await queryRows<DbMaSpreadMetaRow>(MA_SPREAD_META_SQL,
    [secTypeParam(secType), metaType]);

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
  const rows = await queryRows<DbMaSpreadMetaRow>(MA_SPREAD_META_SQL,
    [secTypeParam(secType), metaType]);

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
