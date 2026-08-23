/**
 * ECharts option builder for the MA-Spread pair chart (SMA + EMA).
 *
 * Extracted from the former MaSpreadPage.tsx so the panel component stays
 * focused on data fetching + layout.
 *
 * Implementation:
 *   For Price/MA pairs (ma_short === 0):
 *     - OHLC bars (shared ohlcSeries from @/lib/ohlc) — replaces the close line
 *     - Long MA line  (z=5, no stack)
 *     - Gap fill stack (3 series: _base, _pos, _neg)
 *     - Bollinger envelope (optional)
 *     - Trading amount bars on secondary y-axis
 *
 *   For MA/MA pairs (ma_short === 5):
 *     - Short MA line + Long MA line
 *     - Gap fill stack (3 series: _base, _pos, _neg)
 *     - Trading amount bars on secondary y-axis
 */
import { fmtNum, fmtPct, fmtYi } from "@/lib/series";
import { ohlcSeries, type OhlcMode } from "@/lib/ohlc";
import {
  MA5_COLOR,
  MA20_COLOR,
  MA60_COLOR,
  MA120_COLOR,
  MA255_COLOR,
  UP_COLOR,
  DOWN_COLOR,
  SPOT_COLOR,
  BOLL_BAND_COLOR,
  BOLL_BAND_FILL,
  axisColors,
  commonLegend,
  commonGrid,
  commonDataZoom,
} from "@/theme/chart-palette";
import type { ThemeMode } from "@/store/filters";
import type { EChartsOption } from "echarts";
import type {
  MovAveSpreadHypeEpisodes,
  MovAveSpreadOhlcRow,
  MovAveSpreadPairSeries,
} from "@shared/types";
import {
  computeTrendBands,
  trendBandsToMarkArea,
  shortLabel,
  type TrendBand,
  TREND_DOWN_COLOR,
  TREND_FLAT_COLOR,
  TREND_UP_COLOR,
} from "./trendBands";
import {
  hypeEpisodesToMarkArea,
  HYPE_ACCENT_COLOR,
  HYPE_SHADE_COLOR,
} from "./hypeBands";
import { buildPairTooltipFormatter, type TooltipContext } from "./tooltipFormatter";

// Color for the "price" series (ma_short = 0).
const PRICE_COLOR = SPOT_COLOR;

// OHLC-window overlay colors: the roof (resistance trendline through the
// window's top + 2nd highs + its rolling-max envelope) is warm orange; the
// floor (support trendline through the top + 2nd lows + rolling-min envelope)
// is blue. Both stand apart from the green/red gap fills and gray long MA.
const ROOF_COLOR = "#FB8C00";
const FLOOR_COLOR = "#1E88E5";

export type TradingAmtMode = "off" | "lowkey";

export interface BuildPairOptionArgs {
  /** The pair's full time series. */
  pair: MovAveSpreadPairSeries;
  /** Current theme mode (light / dark) for axis + tooltip colors. */
  themeMode: ThemeMode;
  /** Bollinger multiplier k in MA ± k × σ. Default 2. */
  bollingerK?: number;
  /** Trading amount display mode. Defaults to "lowkey". */
  tradingAmtMode?: TradingAmtMode;
  /** Index into pair.rows of the currently hovered date. */
  hoveredIdx?: number | null;
  /** Display mode for price-derived series. */
  ohlcMode?: OhlcMode;
  /** Enabled rolling-OHLC window (trading days) — null/undefined = off.
   *  When set, the window's rolling High/Low envelope is drawn and the
   *  roof/floor trendlines are armed. */
  ohlcWindow?: number | null;
  /** Index into pair.rows of the clicked chart date — the roof/floor
   *  trendlines are drawn from the window's (top, 2nd) extrema at this
   *  date and stop here. */
  ohlcClickIdx?: number | null;
  /** Rolling-window OHLC extrema rows (index-aligned with pair.rows). */
  ohlcRows?: MovAveSpreadOhlcRow[] | null;
  /** Enabled market-hype check-in window (trading days) — null/undefined =
   *  off. When set, hyped date periods are shaded light purple. */
  hypeWindow?: number | null;
  /** Market-hype episodes keyed by check-in window. Source:
   *  analysis.mov_ave_market_hypes — episodes are date spans, so no
   *  index-alignment with pair.rows is required. */
  hypeEpisodes?: MovAveSpreadHypeEpisodes | null;
}

/** Per-window OHLC extrema fields picked from a MovAveSpreadOhlcRow. */
interface OhlcWinFields {
  open: number | null;
  high: number | null;
  highDate: string | null;
  high2nd: number | null;
  high2ndDate: string | null;
  highSlope: number | null;
  low: number | null;
  lowDate: string | null;
  low2nd: number | null;
  low2ndDate: string | null;
  lowSlope: number | null;
}

const EMPTY_OHLC_WIN: OhlcWinFields = {
  open: null,
  high: null,
  highDate: null,
  high2nd: null,
  high2ndDate: null,
  highSlope: null,
  low: null,
  lowDate: null,
  low2nd: null,
  low2ndDate: null,
  lowSlope: null,
};

/** Pick the 11 OHLC extrema fields for window `w` from one extrema row. */
function pickOhlcWinFields(r: MovAveSpreadOhlcRow, w: number): OhlcWinFields {
  switch (w) {
    case 20:
      return {
        open: r.open_20d, high: r.high_20d, highDate: r.high_date_20d,
        high2nd: r.high_2nd_20d, high2ndDate: r.high_2nd_date_20d,
        highSlope: r.high_line_slope_20d,
        low: r.low_20d, lowDate: r.low_date_20d,
        low2nd: r.low_2nd_20d, low2ndDate: r.low_2nd_date_20d,
        lowSlope: r.low_line_slope_20d,
      };
    case 60:
      return {
        open: r.open_60d, high: r.high_60d, highDate: r.high_date_60d,
        high2nd: r.high_2nd_60d, high2ndDate: r.high_2nd_date_60d,
        highSlope: r.high_line_slope_60d,
        low: r.low_60d, lowDate: r.low_date_60d,
        low2nd: r.low_2nd_60d, low2ndDate: r.low_2nd_date_60d,
        lowSlope: r.low_line_slope_60d,
      };
    case 120:
      return {
        open: r.open_120d, high: r.high_120d, highDate: r.high_date_120d,
        high2nd: r.high_2nd_120d, high2ndDate: r.high_2nd_date_120d,
        highSlope: r.high_line_slope_120d,
        low: r.low_120d, lowDate: r.low_date_120d,
        low2nd: r.low_2nd_120d, low2ndDate: r.low_2nd_date_120d,
        lowSlope: r.low_line_slope_120d,
      };
    case 255:
      return {
        open: r.open_255d, high: r.high_255d, highDate: r.high_date_255d,
        high2nd: r.high_2nd_255d, high2ndDate: r.high_2nd_date_255d,
        highSlope: r.high_line_slope_255d,
        low: r.low_255d, lowDate: r.low_date_255d,
        low2nd: r.low_2nd_255d, low2ndDate: r.low_2nd_date_255d,
        lowSlope: r.low_line_slope_255d,
      };
    case 500:
      return {
        open: r.open_500d, high: r.high_500d, highDate: r.high_date_500d,
        high2nd: r.high_2nd_500d, high2ndDate: r.high_2nd_date_500d,
        highSlope: r.high_line_slope_500d,
        low: r.low_500d, lowDate: r.low_date_500d,
        low2nd: r.low_2nd_500d, low2ndDate: r.low_2nd_date_500d,
        lowSlope: r.low_line_slope_500d,
      };
    case 750:
      return {
        open: r.open_750d, high: r.high_750d, highDate: r.high_date_750d,
        high2nd: r.high_2nd_750d, high2ndDate: r.high_2nd_date_750d,
        highSlope: r.high_line_slope_750d,
        low: r.low_750d, lowDate: r.low_date_750d,
        low2nd: r.low_2nd_750d, low2ndDate: r.low_2nd_date_750d,
        lowSlope: r.low_line_slope_750d,
      };
    case 1275:
      return {
        open: r.open_1275d, high: r.high_1275d, highDate: r.high_date_1275d,
        high2nd: r.high_2nd_1275d, high2ndDate: r.high_2nd_date_1275d,
        highSlope: r.high_line_slope_1275d,
        low: r.low_1275d, lowDate: r.low_date_1275d,
        low2nd: r.low_2nd_1275d, low2ndDate: r.low_2nd_date_1275d,
        lowSlope: r.low_line_slope_1275d,
      };
    default:
      return EMPTY_OHLC_WIN;
  }
}

/**
 * Compute a two-point trendline segment through the (top, 2nd) extrema of
 * the window ending at `clickIdx`, drawn from the EARLIER anchor to the
 * clicked date (the line passes through both anchors — two points
 * determining a line — and stops at the clicked date).
 *
 * Returns null when the line cannot be determined (missing extrema, anchor
 * date not found, coincident anchors, or anchor after the clicked date).
 */
function trendlineSegment(
  clickIdx: number,
  i1: number,
  v1: number,
  i2: number,
  v2: number,
): Array<[number, number]> | null {
  if (i1 < 0 || i2 < 0 || i1 === i2) return null;
  // Anchors live inside the window ending at clickIdx, so both are <= it.
  if (clickIdx < Math.max(i1, i2)) return null;
  // Linear extrapolation of the anchor line to the clicked date.
  const vc = v1 + ((v2 - v1) * (clickIdx - i1)) / (i2 - i1);
  const s = Math.min(i1, i2);
  const vs = s === i1 ? v1 : v2;
  return [[s, vs], [clickIdx, vc]];
}

/** Format a yuan amount as 亿元 (100M yuan). */
function fmtAmtYi(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return fmtYi(v, digits);
}

export function buildPairOption({
  pair,
  themeMode,
  bollingerK = 2,
  tradingAmtMode = "lowkey",
  hoveredIdx = null,
  ohlcMode = "absolute",
  ohlcWindow = null,
  ohlcClickIdx = null,
  ohlcRows = null,
  hypeWindow = null,
  hypeEpisodes = null,
}: BuildPairOptionArgs): EChartsOption {
  const c = axisColors(themeMode);
  const rows = pair.rows;
  const n = rows.length;

  const isPricePair = pair.ma_short === 0;

  const dates = rows.map((r) => r.date);
  const shorts = rows.map((r) => r.short_value);
  const longs = rows.map((r) => r.long_value);
  const shortSlopes = rows.map((r) => r.short_slope);
  const shortCurvs = rows.map((r) => r.short_curvature);
  const longSlopes = rows.map((r) => r.long_slope);
  const longCurvs = rows.map((r) => r.long_curvature);
  const longStds = rows.map((r) => r.long_std);
  const opens = rows.map((r) => r.open);
  const highs = rows.map((r) => r.high);
  const lows = rows.map((r) => r.low);
  const tradingAmts = rows.map((r) => r.trading_amount);
  const dateOfLastExtreme = rows.map((r) => r.date_of_last_extreme ?? null);
  const gapSinceLastExtreme = rows.map((r) => r.gap_since_last_extreme ?? null);
  const daysSinceLastExtreme = rows.map((r) => r.days_since_last_extreme ?? null);
  const rsi6 = rows.map((r) => r.rsi_6days ?? null);
  const rsi10 = rows.map((r) => r.rsi_10days ?? null);
  const rsi14 = rows.map((r) => r.rsi_14days ?? null);
  const rsi20 = rows.map((r) => r.rsi_20days ?? null);

  const amtMaSlopeOfLong = rows.map((r) => {
    switch (pair.ma_long) {
      case 5:   return r.trading_amt_ma5_slope ?? null;
      case 20:  return r.trading_amt_ma20_slope ?? null;
      case 60:  return r.trading_amt_ma60_slope ?? null;
      case 120: return r.trading_amt_ma120_slope ?? null;
      case 255: return r.trading_amt_ma255_slope ?? null;
      default:  return null;
    }
  });

  const amtMarketShareOfLong = rows.map((r) => {
    switch (pair.ma_long) {
      case 5:   return r.trading_amt_market_share_ma5 ?? null;
      case 20:  return r.trading_amt_market_share_ma20 ?? null;
      case 60:  return r.trading_amt_market_share_ma60 ?? null;
      case 120: return r.trading_amt_market_share_ma120 ?? null;
      case 255: return r.trading_amt_market_share_ma255 ?? null;
      default:  return null;
    }
  });

  const ohlcOpens = rows.map((r) => {
    switch (pair.ma_long) {
      case 20:  return r.open_20d ?? null;
      case 60:  return r.open_60d ?? null;
      case 120: return r.open_120d ?? null;
      case 255: return r.open_255d ?? null;
      case 500: return r.open_500d ?? null;
      case 750: return r.open_750d ?? null;
      default:  return null;
    }
  });
  const ohlcHighs = rows.map((r) => {
    switch (pair.ma_long) {
      case 20:  return r.high_20d ?? null;
      case 60:  return r.high_60d ?? null;
      case 120: return r.high_120d ?? null;
      case 255: return r.high_255d ?? null;
      case 500: return r.high_500d ?? null;
      case 750: return r.high_750d ?? null;
      default:  return null;
    }
  });
  const ohlcLows = rows.map((r) => {
    switch (pair.ma_long) {
      case 20:  return r.low_20d ?? null;
      case 60:  return r.low_60d ?? null;
      case 120: return r.low_120d ?? null;
      case 255: return r.low_255d ?? null;
      case 500: return r.low_500d ?? null;
      case 750: return r.low_750d ?? null;
      default:  return null;
    }
  });

  const isMA60Pair = pair.ma_long === 60;
  const trendBands = isMA60Pair ? computeTrendBands(shorts, longs, longSlopes, longStds) : [];
  const hasTrendBands = trendBands.length > 0;

  // ---- Last-extreme hover marker ----------------------------------------
  const lastExtremeData: Array<number | null> = new Array(n).fill(null);
  let lastExtremeRising = true;
  if (
    hoveredIdx != null
    && hoveredIdx >= 0
    && hoveredIdx < n
    && dateOfLastExtreme.some((d) => d != null)
  ) {
    const ed = dateOfLastExtreme[hoveredIdx];
    if (ed != null) {
      const sv = shorts[hoveredIdx];
      if (sv != null && Number.isFinite(sv)) {
        lastExtremeData[hoveredIdx] = sv;
      }
      const gap = gapSinceLastExtreme[hoveredIdx];
      lastExtremeRising = !(gap != null && Number.isFinite(gap) && gap < 0);
    }
  }
  const hasLastExtreme = lastExtremeData.some((v) => v != null);

  // ---- Percentage mode base --------------------------------------------
  let baseVal: number | null = null;
  for (let i = 0; i < n; i++) {
    const v = shorts[i];
    if (v != null && Number.isFinite(v) && Math.abs(v) >= 1e-9) {
      baseVal = v;
      break;
    }
  }
  const hasBase = baseVal != null && Number.isFinite(baseVal) && Math.abs(baseVal) >= 1e-9;

  const fmtPctFromBase = (v: number | null | undefined): string => {
    if (v == null || !Number.isFinite(v) || !hasBase || baseVal == null) return "—";
    return fmtPct((v / baseVal - 1) * 100, 2);
  };
  const fmtPrice = (v: number | null | undefined): string =>
    ohlcMode === "percentage" ? fmtPctFromBase(v) : fmtNum(v);

  // OHLC bar data
  const ohlcData: Array<Array<number | null>> = new Array(n).fill(null);
  if (isPricePair) {
    for (let i = 0; i < n; i++) {
      const o = opens[i];
      const cl = shorts[i];
      const l = lows[i];
      const h = highs[i];
      if (o != null && cl != null && l != null && h != null) {
        ohlcData[i] = [o, cl, l, h];
      }
    }
  }

  // Gap fill stack
  const baseData: Array<number | null> = new Array(n).fill(null);
  const posData: Array<number | null> = new Array(n).fill(null);
  const negData: Array<number | null> = new Array(n).fill(null);

  for (let i = 0; i < n; i++) {
    const s = shorts[i];
    const l = longs[i];
    if (s == null || l == null) continue;
    const diff = s - l;
    baseData[i] = Math.min(s, l);
    if (diff >= 0) {
      posData[i] = diff;
      negData[i] = 0;
    } else {
      posData[i] = 0;
      negData[i] = -diff;
    }
  }

  const sColor = PRICE_COLOR;
  const lColor = MA120_COLOR;
  const isEma = pair.kind === "ema";
  const sName = shortLabel(pair.ma_short, pair.kind);
  const lName = isEma ? `EMA${pair.ma_long}` : `MA${pair.ma_long}`;

  // ---- Bollinger envelope ----------------------------------------------
  const showBoll = isPricePair && bollingerK > 0;
  const upperData: Array<number | null> = new Array(n).fill(null);
  const lowerData: Array<number | null> = new Array(n).fill(null);
  const bollBase: Array<number | null> = new Array(n).fill(null);
  const bollDelta: Array<number | null> = new Array(n).fill(null);
  if (showBoll) {
    for (let i = 0; i < n; i++) {
      const l = longs[i];
      const sd = longStds[i];
      if (l == null || sd == null) continue;
      const upper = l + bollingerK * sd;
      const lower = l - bollingerK * sd;
      upperData[i] = upper;
      lowerData[i] = lower;
      bollBase[i] = lower;
      bollDelta[i] = upper - lower;
    }
  }
  const upperName = `Upper (+${bollingerK}σ)`;
  const lowerName = `Lower (−${bollingerK}σ)`;

  // ---- OHLC-window overlay (rolling envelope + roof/floor) -------------
  // Active only when a window button is enabled AND the extrema rows are
  // index-aligned with the pair's rows (all pairs share one date axis, so
  // chartData.ohlc aligns with every pair).
  const ohlcW = ohlcWindow ?? null;
  const ohlcAligned = ohlcW != null && ohlcRows != null && ohlcRows.length === n;
  let ohlcHighLine: Array<number | null> = [];
  let ohlcLowLine: Array<number | null> = [];
  let roofSeg: Array<[number, number]> | null = null;
  let floorSeg: Array<[number, number]> | null = null;
  let ohlcWinArrays: TooltipContext["ohlcWinArrays"] = null;
  // Per-anchor scatter items: filled marker for the top extreme, hollow for
  // the 2nd; triangles point up for peaks and down for troughs.
  const anchorItems: Array<{
    value: [number, number];
    symbol: string;
    symbolRotate: number;
    itemStyle: { color: string; borderColor: string; borderWidth: number };
  }> = [];

  if (ohlcAligned && ohlcW != null && ohlcRows != null) {
    const fields = ohlcRows.map((r) => pickOhlcWinFields(r, ohlcW));
    ohlcHighLine = fields.map((f) => f.high);
    ohlcLowLine = fields.map((f) => f.low);
    ohlcWinArrays = {
      open: fields.map((f) => f.open),
      high: fields.map((f) => f.high),
      highDate: fields.map((f) => f.highDate),
      high2nd: fields.map((f) => f.high2nd),
      high2ndDate: fields.map((f) => f.high2ndDate),
      highSlope: fields.map((f) => f.highSlope),
      low: fields.map((f) => f.low),
      lowDate: fields.map((f) => f.lowDate),
      low2nd: fields.map((f) => f.low2nd),
      low2ndDate: fields.map((f) => f.low2ndDate),
      lowSlope: fields.map((f) => f.lowSlope),
    };

    // Roof/floor trendlines at the clicked date: two points (the window's
    // top and 2nd extrema) determine each line; the segment starts at the
    // earlier anchor, passes through the later one, and stops at the
    // clicked date.
    const clickIdx = ohlcClickIdx ?? null;
    if (clickIdx != null && clickIdx >= 0 && clickIdx < n) {
      const win = fields[clickIdx];
      if (
        win != null && win.high != null && win.highDate != null &&
        win.high2nd != null && win.high2ndDate != null
      ) {
        const i1 = dates.indexOf(win.highDate);
        const i2 = dates.indexOf(win.high2ndDate);
        roofSeg = trendlineSegment(clickIdx, i1, win.high, i2, win.high2nd);
        if (roofSeg != null) {
          anchorItems.push(
            { value: [i1, win.high], symbol: "triangle", symbolRotate: 0, itemStyle: { color: ROOF_COLOR, borderColor: ROOF_COLOR, borderWidth: 1 } },
            { value: [i2, win.high2nd], symbol: "triangle", symbolRotate: 0, itemStyle: { color: "transparent", borderColor: ROOF_COLOR, borderWidth: 1.5 } },
          );
        }
      }
      if (
        win != null && win.low != null && win.lowDate != null &&
        win.low2nd != null && win.low2ndDate != null
      ) {
        const i1 = dates.indexOf(win.lowDate);
        const i2 = dates.indexOf(win.low2ndDate);
        floorSeg = trendlineSegment(clickIdx, i1, win.low, i2, win.low2nd);
        if (floorSeg != null) {
          anchorItems.push(
            { value: [i1, win.low], symbol: "triangle", symbolRotate: 180, itemStyle: { color: FLOOR_COLOR, borderColor: FLOOR_COLOR, borderWidth: 1 } },
            { value: [i2, win.low2nd], symbol: "triangle", symbolRotate: 180, itemStyle: { color: "transparent", borderColor: FLOOR_COLOR, borderWidth: 1.5 } },
          );
        }
      }
    }
  }

  // Vertical "stop" line at the clicked date — the roof/floor trendlines
  // converge toward and stop at this line.
  const ohlcClickMarkLine =
    ohlcAligned && ohlcClickIdx != null && ohlcClickIdx >= 0 && ohlcClickIdx < n
      ? {
          markLine: {
            silent: true as const,
            symbol: ["none", "none"] as [string, string],
            label: { show: false },
            lineStyle: { type: "dashed" as const, color: c.textColor, width: 1, opacity: 0.75 },
            data: [{ xAxis: dates[ohlcClickIdx] }],
          },
        }
      : null;

  // ---- Market-hype shading (light purple over hyped periods) ------------
  // Active only when a hype window button is enabled AND episode data was
  // fetched for this code. Episodes are date spans (first/last satisfied
  // dates of each hyped run), so they apply directly as markArea
  // rectangles — no index-alignment with the pair's rows needed.
  // Attached to the invisible "_base" gap-fill series (z=1) so the shade
  // sits behind the price/MA lines but above the Bollinger fill.
  const hypeW = hypeWindow ?? null;
  const hypeMarkAreaData =
    hypeW != null && hypeEpisodes != null
      ? hypeEpisodesToMarkArea(hypeEpisodes[hypeW])
      : [];

  // Legend data
  const legendData: string[] = [sName, lName];
  if (showBoll) {
    legendData.push(upperName, lowerName);
  }
  if (ohlcAligned && ohlcW != null) {
    legendData.push(`High(${ohlcW}d)`, `Low(${ohlcW}d)`);
    if (roofSeg != null) legendData.push(`Roof(${ohlcW}d)`);
    if (floorSeg != null) legendData.push(`Floor(${ohlcW}d)`);
  }
  if (tradingAmtMode !== "off") {
    legendData.push("Amt Up", "Amt Down");
  }
  if (hypeMarkAreaData.length > 0 && hypeW != null) {
    legendData.push(`Hyped(${hypeW}d)`);
  }
  const trendLegendNames = hasTrendBands ? ["▼ Downward", "▬ Flat", "▲ Upward"] : [];
  if (hasTrendBands) {
    legendData.push(...trendLegendNames);
  }

  const amtBarData: Array<number | null> = tradingAmts.map((v) =>
    v != null && Number.isFinite(v) ? v : null,
  );

  // ---- Build series ----------------------------------------------------
  const echartsSeries: EChartsOption["series"] = [];

  if (isPricePair) {
    echartsSeries.push(ohlcSeries(ohlcData, { name: sName, z: 5 }));
  } else {
    echartsSeries.push({
      type: "line",
      name: sName,
      data: shorts,
      symbol: "none",
      lineStyle: { color: sColor, width: 1.4 },
      z: 5,
    });
  }

  echartsSeries.push({
    type: "line",
    name: lName,
    data: longs,
    symbol: "none",
    lineStyle: { color: lColor, width: 1.4 },
    z: 5,
    ...(hasTrendBands
      ? {
          markArea: {
            silent: true,
            itemStyle: { borderWidth: 0 },
            data: trendBandsToMarkArea(trendBands, dates),
          },
        }
      : {}),
    ...(ohlcClickMarkLine != null ? ohlcClickMarkLine : {}),
  });

  echartsSeries.push({
    type: "line",
    name: "_base",
    data: baseData,
    stack: "gapFill",
    symbol: "none",
    lineStyle: { opacity: 0 },
    z: 1,
    ...(hypeMarkAreaData.length > 0
      ? {
          markArea: {
            silent: true as const,
            itemStyle: { borderWidth: 0 },
            data: hypeMarkAreaData,
          },
        }
      : {}),
  });
  echartsSeries.push({
    type: "line",
    name: "_pos",
    data: posData,
    stack: "gapFill",
    symbol: "none",
    lineStyle: { opacity: 0 },
    areaStyle: { color: UP_COLOR, opacity: 0.4 },
    z: 2,
  });
  echartsSeries.push({
    type: "line",
    name: "_neg",
    data: negData,
    stack: "gapFill",
    symbol: "none",
    lineStyle: { opacity: 0 },
    areaStyle: { color: DOWN_COLOR, opacity: 0.4 },
    z: 2,
  });

  if (showBoll) {
    echartsSeries.push({
      type: "line",
      name: upperName,
      data: upperData,
      symbol: "none",
      lineStyle: { color: BOLL_BAND_COLOR, width: 1, type: "dashed", opacity: 0.7 },
      z: 4,
    });
    echartsSeries.push({
      type: "line",
      name: lowerName,
      data: lowerData,
      symbol: "none",
      lineStyle: { color: BOLL_BAND_COLOR, width: 1, type: "dashed", opacity: 0.7 },
      z: 4,
    });
    echartsSeries.push({
      type: "line",
      name: "_bollBase",
      data: bollBase,
      stack: "bollBand",
      symbol: "none",
      lineStyle: { opacity: 0 },
      areaStyle: { opacity: 0 },
      z: 0,
    });
    echartsSeries.push({
      type: "line",
      name: "_bollDelta",
      data: bollDelta,
      stack: "bollBand",
      symbol: "none",
      lineStyle: { opacity: 0 },
      areaStyle: { color: BOLL_BAND_FILL, opacity: 0.08 },
      z: 0,
    });
  }

  // ---- OHLC-window overlay series ---------------------------------------
  if (ohlcAligned && ohlcW != null) {
    // Rolling window envelope: the max-high / min-low of the window ending
    // at each date (dashed, subtle) — visualizes the enabled period.
    echartsSeries.push({
      type: "line",
      name: `High(${ohlcW}d)`,
      data: ohlcHighLine,
      symbol: "none",
      lineStyle: { color: ROOF_COLOR, width: 1, type: "dashed", opacity: 0.55 },
      z: 4,
      clip: true,
    });
    echartsSeries.push({
      type: "line",
      name: `Low(${ohlcW}d)`,
      data: ohlcLowLine,
      symbol: "none",
      lineStyle: { color: FLOOR_COLOR, width: 1, type: "dashed", opacity: 0.55 },
      z: 4,
      clip: true,
    });

    // Roof trendline — the line determined by the window's top + 2nd highs,
    // drawn from the earlier anchor to the clicked date (stops there).
    if (roofSeg != null) {
      echartsSeries.push({
        type: "line",
        name: `Roof(${ohlcW}d)`,
        data: roofSeg,
        symbol: "none",
        lineStyle: { color: ROOF_COLOR, width: 1.8 },
        z: 12,
        clip: true,
      });
    }
    // Floor trendline — the line determined by the window's top + 2nd lows.
    if (floorSeg != null) {
      echartsSeries.push({
        type: "line",
        name: `Floor(${ohlcW}d)`,
        data: floorSeg,
        symbol: "none",
        lineStyle: { color: FLOOR_COLOR, width: 1.8 },
        z: 12,
        clip: true,
      });
    }
    // Anchor markers: the four extrema that determine the two lines.
    if (anchorItems.length > 0) {
      echartsSeries.push({
        type: "scatter",
        name: "_ohlcAnchors",
        data: anchorItems,
        symbolSize: 9,
        z: 13,
        tooltip: { show: false },
      });
    }
  }

  if (hasLastExtreme) {
    const leColor = lastExtremeRising
      ? { rgb: "67, 160, 71", hex: "#43A047" }
      : { rgb: "229, 57, 53", hex: "#E53935" };
    echartsSeries.push({
      type: "scatter",
      name: "Last Extreme",
      data: lastExtremeData,
      symbol: "triangle",
      symbolSize: 7,
      symbolRotate: lastExtremeRising ? 0 : 180,
      itemStyle: {
        color: `rgba(${leColor.rgb}, 0.55)`,
        borderColor: leColor.hex,
        borderWidth: 0.5,
      },
      z: 19,
      tooltip: { show: false },
    });
  }

  if (hasTrendBands) {
    const trendLegendColors: Record<string, string> = {
      "▼ Downward": TREND_DOWN_COLOR.replace("0.07", "0.6"),
      "▬ Flat": TREND_FLAT_COLOR.replace("0.07", "0.6"),
      "▲ Upward": TREND_UP_COLOR.replace("0.05", "0.6"),
    };
    for (const name of trendLegendNames) {
      echartsSeries.push({
        type: "scatter",
        name,
        data: [null],
        symbol: "circle",
        symbolSize: 7,
        itemStyle: { color: trendLegendColors[name] },
        z: 0,
      });
    }
  }

  // Legend marker for the market-hype shading (light purple rect). Cosmetic
  // only — like the trend-band markers, clicking it toggles nothing.
  if (hypeMarkAreaData.length > 0 && hypeW != null) {
    echartsSeries.push({
      type: "scatter",
      name: `Hyped(${hypeW}d)`,
      data: [null],
      symbol: "rect",
      symbolSize: [10, 8],
      itemStyle: { color: HYPE_ACCENT_COLOR, opacity: 0.45, borderColor: HYPE_SHADE_COLOR },
      z: 0,
    });
  }

  if (tradingAmtMode !== "off") {
    const barOpacity = 0.15;
    const barWidth = "50%";

    const upData: Array<number | null> = new Array(n).fill(null);
    const downData: Array<number | null> = new Array(n).fill(null);

    for (let i = 0; i < n; i++) {
      const amt = amtBarData[i];
      if (amt == null) continue;
      let isUp: boolean;
      if (isPricePair) {
        const o = opens[i];
        const cl = shorts[i];
        isUp = o != null && cl != null ? cl >= o : true;
      } else {
        const s = shorts[i];
        const l = longs[i];
        isUp = s != null && l != null ? s >= l : true;
      }
      if (isUp) {
        upData[i] = amt;
      } else {
        downData[i] = amt;
      }
    }

    echartsSeries.push({
      type: "bar",
      name: "Amt Up",
      data: upData,
      yAxisIndex: 1,
      barWidth,
      itemStyle: { color: UP_COLOR, opacity: barOpacity },
      z: 0,
      stack: "amt",
    });
    echartsSeries.push({
      type: "bar",
      name: "Amt Down",
      data: downData,
      yAxisIndex: 1,
      barWidth,
      itemStyle: { color: DOWN_COLOR, opacity: barOpacity },
      z: 0,
      stack: "amt",
    });
  }

  const grid = commonGrid({ left: 55, right: 55, bottom: 50 });

  // ---- Build tooltip context -------------------------------------------
  const tooltipContext: TooltipContext = {
    dates,
    shorts,
    longs,
    shortSlopes,
    shortCurvs,
    longSlopes,
    longCurvs,
    longStds,
    opens,
    highs,
    lows,
    tradingAmts,
    dateOfLastExtreme,
    gapSinceLastExtreme,
    daysSinceLastExtreme,
    rsi6,
    rsi10,
    rsi14,
    rsi20,
    amtMaSlopeOfLong,
    amtMarketShareOfLong,
    ohlcOpens,
    ohlcHighs,
    ohlcLows,
    ohlcWindow: ohlcAligned ? ohlcW : null,
    ohlcWinArrays,
    trendBands,
    hasTrendBands,
    hasBase,
    baseVal,
    showBoll,
    upperData,
    lowerData,
    bollBase,
    pair,
    isPricePair,
    sName,
    lName,
    tradingAmtMode,
    ohlcMode,
  };

  return {
    backgroundColor: "transparent",
    animation: false,
    grid,
    dataZoom: commonDataZoom(),
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line", snap: true },
      backgroundColor: c.tooltipBg,
      borderColor: c.splitLineColor,
      textStyle: { color: c.textColor, fontSize: 11 },
      formatter: buildPairTooltipFormatter(tooltipContext),
    },
    legend: commonLegend(themeMode, { itemWidth: 12, itemHeight: 7, data: legendData }),
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        formatter: (v: string) => v.slice(0, 7),
        interval: Math.max(1, Math.floor(n / 6)),
      },
      splitLine: { show: false },
    },
    yAxis: [
      {
        type: "value",
        scale: true,
        name: ohlcMode === "percentage" ? "%" : undefined,
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => fmtPrice(v),
        },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
      {
        type: "value" as const,
        scale: true,
        name: "Amt (亿)",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => fmtNum(v / 1e8, 1),
        },
        splitLine: { show: false },
      },
    ],
    series: echartsSeries,
  };
}
