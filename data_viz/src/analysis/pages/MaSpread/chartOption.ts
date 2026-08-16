/**
 * ECharts option builder for the MA-Spread pair chart.
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
 *
 * Gap fill stack ("gapFill"):
 *   1. Stack base (invisible): min(short, long)
 *   2. Positive delta (green area): max(short - long, 0)
 *   3. Negative delta (red area):   max(long - short, 0)
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
import type { MovAveSpreadPairSeries, MovAveSpreadValleyLow } from "../../../../shared/types";

// Color for the "price" series (ma_short = 0).
const PRICE_COLOR = SPOT_COLOR;

// ---- Trend classification constants (Price/MA60 pair only) --------------
// downward_trend: close < MA60, with <5 day interruptions bridged.
//                 Dominates over flat_trend (close-below-MA60 wins regardless
//                 of MA60 slope).
// flat_trend:     MA60 slope ∈ [−0.5, +0.5] (raw price diff, no unit
//                 conversion — uses existing ma60_slope column as-is).
// upward_trend:   everything else (close >= MA60 AND slope outside flat band).
const TREND_MAX_INTERRUPTION = 5;
const TREND_FLAT_SLOPE_MIN = -0.5;
const TREND_FLAT_SLOPE_MAX = 0.5;
const TREND_DOWN_COLOR = "rgba(229, 57, 53, 0.07)"; // light red
const TREND_FLAT_COLOR = "rgba(255, 193, 7, 0.07)"; // light amber
const TREND_UP_COLOR = "rgba(76, 175, 80, 0.05)";   // light green

type TrendType = "downward" | "flat" | "upward";

interface TrendBand {
  startIdx: number;
  endIdx: number;
  trend: TrendType;
}

/**
 * Compute trend classification bands from Price/MA60 pair data.
 *
 * Algorithm:
 *   1. Raw below_ma60: close (short_value) < MA60 (long_value).
 *   2. Bridge gaps < TREND_MAX_INTERRUPTION consecutive non-below days into
 *      single downward belts (same <5 day logic as the Python belt detector).
 *   3. Bridged below-ma60 periods = downward_trend (dominates).
 *   4. For remaining days: if MA60 slope (long_slope) ∈ [−0.5, +0.5] → flat,
 *      else → upward.
 *   5. Weak-rebound reclassification: an "upward" band where close stays
 *      below MA60 + 1σ for ALL days (weak rebound that never exceeds 1 std
 *      above MA60) AND there exist non-flat trends (downward or upward)
 *      both before AND after the band → reclassify as flat. This catches
 *      brief rebounds within a broader trend context that are really just
 *      flat consolidation, not a genuine upward trend.
 *   6. Merge adjacent bands of the same type after reclassification.
 *
 * Returns a list of contiguous {startIdx, endIdx, trend} bands covering
 * the full date range (or empty array if not a Price/MA60 pair).
 */
function computeTrendBands(
  shorts: Array<number | null>,
  longs: Array<number | null>,
  longSlopes: Array<number | null>,
  longStds: Array<number | null>,
): TrendBand[] {
  const n = shorts.length;
  if (n === 0) return [];

  // Step 1: raw below_ma60 boolean array.
  const belowMA60: boolean[] = new Array(n).fill(false);
  for (let i = 0; i < n; i++) {
    const s = shorts[i];
    const l = longs[i];
    if (s != null && l != null && Number.isFinite(s) && Number.isFinite(l)) {
      belowMA60[i] = s < l;
    }
  }

  // Step 2: bridge gaps < TREND_MAX_INTERRUPTION into downward belts.
  // A day is "downward" if it's belowMA60 OR within a bridged gap between
  // two belowMA60 segments separated by < TREND_MAX_INTERRUPTION days.
  const isDownward: boolean[] = new Array(n).fill(false);
  let i = 0;
  while (i < n) {
    if (!belowMA60[i]) {
      i++;
      continue;
    }
    // Start of a belowMA60 run at index i.
    let runEnd = i;
    let j = i + 1;
    while (j < n) {
      if (belowMA60[j]) {
        runEnd = j;
        j++;
      } else {
        // Check if the gap is bridgeable (< TREND_MAX_INTERRUPTION non-below days).
        let gapLen = 0;
        let k = j;
        while (k < n && !belowMA60[k]) {
          gapLen++;
          k++;
        }
        if (gapLen < TREND_MAX_INTERRUPTION && k < n && belowMA60[k]) {
          // Bridgeable: extend the run to include the gap.
          runEnd = k;
          j = k + 1;
        } else {
          // Not bridgeable: end the run here.
          break;
        }
      }
    }
    // Mark the entire bridged run [i, runEnd] as downward.
    for (let m = i; m <= runEnd; m++) {
      isDownward[m] = true;
    }
    i = runEnd + 1;
  }

  // Step 3: classify each day and merge into contiguous bands.
  const rawBands: TrendBand[] = [];
  let curTrend: TrendType | null = null;
  let curStart = 0;
  for (let idx = 0; idx < n; idx++) {
    let t: TrendType;
    if (isDownward[idx]) {
      t = "downward";
    } else {
      const slope = longSlopes[idx];
      if (slope != null && Number.isFinite(slope) &&
          slope >= TREND_FLAT_SLOPE_MIN && slope <= TREND_FLAT_SLOPE_MAX) {
        t = "flat";
      } else {
        t = "upward";
      }
    }
    if (t === curTrend) {
      // extend current band
    } else {
      if (curTrend !== null) {
        rawBands.push({ startIdx: curStart, endIdx: idx - 1, trend: curTrend });
      }
      curTrend = t;
      curStart = idx;
    }
  }
  if (curTrend !== null) {
    rawBands.push({ startIdx: curStart, endIdx: n - 1, trend: curTrend });
  }

  // Step 4: weak-rebound reclassification.
  // For each "upward" band, check if:
  //   (a) ALL days in [startIdx, endIdx] have close < MA60 + 1×std
  //       (weak rebound — never exceeds 1σ above MA60)
  //   (b) There exists a non-flat band (downward or upward) before this band
  //   (c) There exists a non-flat band (downward or upward) after this band
  // If all three conditions are met → reclassify as flat.
  const bands = rawBands.map((b) => ({ ...b }));
  for (let bi = 0; bi < bands.length; bi++) {
    if (bands[bi].trend !== "upward") continue;

    // Condition (a): all days have close < MA60 + 1×std.
    let allWeak = true;
    for (let idx = bands[bi].startIdx; idx <= bands[bi].endIdx; idx++) {
      const s = shorts[idx];
      const l = longs[idx];
      const sd = longStds[idx];
      if (s == null || l == null || sd == null ||
          !Number.isFinite(s) || !Number.isFinite(l) || !Number.isFinite(sd)) {
        allWeak = false;
        break;
      }
      if (s >= l + sd) {
        // Close exceeds MA60 + 1σ — not a weak rebound.
        allWeak = false;
        break;
      }
    }
    if (!allWeak) continue;

    // Condition (b): non-flat band exists before.
    let hasNonFlatBefore = false;
    for (let bj = 0; bj < bi; bj++) {
      if (bands[bj].trend === "downward" || bands[bj].trend === "upward") {
        hasNonFlatBefore = true;
        break;
      }
    }
    if (!hasNonFlatBefore) continue;

    // Condition (c): non-flat band exists after.
    let hasNonFlatAfter = false;
    for (let bj = bi + 1; bj < bands.length; bj++) {
      if (bands[bj].trend === "downward" || bands[bj].trend === "upward") {
        hasNonFlatAfter = true;
        break;
      }
    }
    if (!hasNonFlatAfter) continue;

    // All conditions met → reclassify as flat.
    bands[bi].trend = "flat";
  }

  // Step 5: merge adjacent bands of the same type after reclassification.
  const merged: TrendBand[] = [];
  for (const b of bands) {
    if (merged.length > 0 && merged[merged.length - 1].trend === b.trend) {
      merged[merged.length - 1].endIdx = b.endIdx;
    } else {
      merged.push({ ...b });
    }
  }
  return merged;
}

/** Convert trend bands to ECharts markArea data format. */
function trendBandsToMarkArea(
  bands: TrendBand[],
  dates: string[],
): Array<[{ xAxis: string; itemStyle: { color: string } }, { xAxis: string }]> {
  const colorMap: Record<TrendType, string> = {
    downward: TREND_DOWN_COLOR,
    flat: TREND_FLAT_COLOR,
    upward: TREND_UP_COLOR,
  };
  return bands.map((b) => [
    {
      xAxis: dates[b.startIdx],
      itemStyle: { color: colorMap[b.trend] },
    },
    { xAxis: dates[b.endIdx] },
  ]);
}

/** Short-series label, e.g. "Price", "MA5", or "EMA6".
 *  kind = "ema" switches the prefix from MA to EMA for non-price shorts. */
export function shortLabel(maShort: number, kind?: string): string {
  if (maShort === 0) return "Price";
  return kind === "ema" ? `EMA${maShort}` : `MA${maShort}`;
}

export type TradingAmtMode = "off" | "lowkey";

export interface BuildPairOptionArgs {
  /** The pair's full time series. */
  pair: MovAveSpreadPairSeries;
  /** Current theme mode (light / dark) for axis + tooltip colors. */
  themeMode: ThemeMode;
  /**
   * Bollinger multiplier k in MA ± k × σ. Default 2 (standard Bollinger).
   * Set to 0 (or any non-positive value) to hide the envelope.
   * Only applies to Price/MA pairs (ma_short === 0); MA5/MA pairs ignore it.
   */
  bollingerK?: number;
  /**
   * Trading amount display mode.
   * - "off": hide trading amount bars entirely.
   * - "lowkey": show bars with low opacity (subtle background reference).
   * Defaults to "lowkey".
   */
  tradingAmtMode?: TradingAmtMode;
  /**
   * Per-extreme-date rows from analysis.mov_ave_peaks_and_floors (one row
   * per mov_ave_peaks_and_floors.date for the selected code). Each entry
   * places a single triangle marker at (date, extreme_val) — green
   * up-triangle for peaks (is_extreme_peak_not_floor=true), red
   * down-triangle for floors. Sourced directly from the peaks_and_floors
   * table — NOT derived from the per-date detail series.
   */
  valleyLows?: MovAveSpreadValleyLow[];
  /**
   * Index into pair.rows of the currently hovered date (driven by the
   * ECharts `updateAxisPointer` event in MaSpreadPanel). When set, a single
   * small lowkey triangle is drawn at the hovered date's
   * `date_of_last_extreme` position on the short series — pointing UP if the
   * price has been rising since the last extreme (gap_since_last_extreme ≥ 0,
   * i.e. last extreme was a MIN) or DOWN if falling (gap < 0, last was a MAX).
   * Only ONE triangle is shown at a time (the one for the hovered date);
   * no triangles are drawn when nothing is hovered.
   */
  hoveredIdx?: number | null;
  /**
   * Display mode for price-derived series.
   * - "absolute": show raw values (default — backward compatible).
   * - "percentage": only the y-axis labels and tooltip values are converted
   *   to % change from the first valid close. The chart data itself stays
   *   in absolute units, so the rendering (OHLC bars, MA lines, Bollinger
   *   band shade, gap fill) is visually identical in both modes.
   */
  ohlcMode?: OhlcMode;
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
  valleyLows = [],
  hoveredIdx = null,
  ohlcMode = "absolute",
}: BuildPairOptionArgs): EChartsOption {
  const c = axisColors(themeMode);
  const rows = pair.rows;
  const n = rows.length;

  const isPricePair = pair.ma_short === 0;

  const dates = rows.map((r) => r.date);
  const shorts = rows.map((r) => r.short_value);
  const longs = rows.map((r) => r.long_value);
  // slope / curvature arrays for the tooltip.
  const shortSlopes = rows.map((r) => r.short_slope);
  const shortCurvs = rows.map((r) => r.short_curvature);
  const longSlopes = rows.map((r) => r.long_slope);
  const longCurvs = rows.map((r) => r.long_curvature);
  const longStds = rows.map((r) => r.long_std);
  // OHLC + trading amount arrays.
  const opens = rows.map((r) => r.open);
  const highs = rows.map((r) => r.high);
  const lows = rows.map((r) => r.low);
  const tradingAmts = rows.map((r) => r.trading_amount);
  // Last-extreme arrays (from analysis.mov_ave_rsi, shared across all 9 pairs
  // for a given date). Used for the green up-triangle markers + tooltip.
  const dateOfLastExtreme = rows.map((r) => r.date_of_last_extreme ?? null);
  const gapSinceLastExtreme = rows.map((r) => r.gap_since_last_extreme ?? null);
  const daysSinceLastExtreme = rows.map((r) => r.days_since_last_extreme ?? null);
  // Wilder RSI arrays (6/10/14/20 days) from analysis.mov_ave_rsi — shared
  // across all 9 pairs for a given date. Surfaced in the tooltip.
  const rsi6 = rows.map((r) => r.rsi_6days ?? null);
  const rsi10 = rows.map((r) => r.rsi_10days ?? null);
  const rsi14 = rows.map((r) => r.rsi_14days ?? null);
  const rsi20 = rows.map((r) => r.rsi_20days ?? null);
  // Trading-amount MA SLOPE for the selected pair's long MA window — the
  // fractional daily change of trading_amt_ma{ma_long}. Surfaced in the
  // tooltip when trading-amt display is enabled.
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
  // Trading-amount MARKET-SHARE MA for the selected pair's long MA window —
  // the W-day MA of (trading_amount / total-market-turnover). Dimensionless
  // ratio 0..1. Surfaced in the tooltip as a percentage when trading-amt
  // display is enabled.
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

  // ---- Valley-low / peak markers (red down / green up triangles) --------
  // Sourced DIRECTLY from analysis.mov_ave_peaks_and_floors (one row per
  // extreme date for this code). Each mov_ave_peaks_and_floors.date is
  // plotted exactly once — we do NOT derive markers from the per-date
  // detail series (which would smear each extreme across every detail
  // date that maps to it via peaks_and_floors_date). Peaks
  // (is_extreme_peak_not_floor=true) render as green up-triangles; floors
  // render as red down-triangles.
  const floorMap = new Map<string, number>(); // dateStr -> extreme_val (floor)
  const peakMap = new Map<string, number>();  // dateStr -> extreme_val (peak)
  for (const v of valleyLows) {
    if (v.extreme_val != null && Number.isFinite(v.extreme_val)) {
      if (v.is_extreme_peak_not_floor) {
        peakMap.set(v.date, v.extreme_val);
      } else {
        floorMap.set(v.date, v.extreme_val);
      }
    }
  }

  // ---- Trend classification bands (any MA60 long pair: Price/MA60 + MA5/MA60)
  // downward_trend dominates (short < MA60, with <5 day bridging); flat_trend
  // when MA60 slope ∈ [−0.5, +0.5]; upward_trend otherwise. Shown as subtle
  // background color bands. For Price/MA60 "short" is close; for MA5/MA60
  // "short" is MA5 (short-term MA vs long-term MA trend signal).
  const isMA60Pair = pair.ma_long === 60;
  const trendBands = isMA60Pair ? computeTrendBands(shorts, longs, longSlopes, longStds) : [];
  const hasTrendBands = trendBands.length > 0;

  // Build scatter data: one marker per unique extreme date, placed at
  // (date, extreme_val) on the chart. Floors → red down-triangle; peaks →
  // green up-triangle. Split into two arrays so each kind gets its own
  // scatter series with the correct symbol rotation + color.
  const valleyLowData: Array<number | null> = new Array(n).fill(null);
  const peakData: Array<number | null> = new Array(n).fill(null);
  for (let i = 0; i < n; i++) {
    const fv = floorMap.get(dates[i]);
    if (fv != null) valleyLowData[i] = fv;
    const pv = peakMap.get(dates[i]);
    if (pv != null) peakData[i] = pv;
  }
  const hasValleyLows = valleyLowData.some((v) => v != null);
  const hasPeaks = peakData.some((v) => v != null);

  // ---- Last-extreme hover marker (single small lowkey triangle) -----------
  // analysis.mov_ave_rsi.date_of_last_extreme gives the biz date of the most
  // recent local turning point (high/low) detected by price_slope sign change.
  // We show only ONE triangle at a time — placed at the HOVERED date's
  // position (mov_ave_rsi.date, i.e. the current row's date) on the short
  // series, indicating the date_of_last_extreme that applies to that row.
  // Points UP (green) if price has been rising since the last extreme
  // (gap_since_last_extreme ≥ 0 ⇒ last extreme was a MIN) or DOWN (red) if
  // falling (gap < 0 ⇒ last was a MAX). Small + lowkey so it doesn't compete
  // with the valley-low markers. The tooltip surfaces the full
  // date_of_last_extreme + gap_since_last_extreme + days_since_last_extreme.
  const lastExtremeData: Array<number | null> = new Array(n).fill(null);
  let lastExtremeRising = true; // default up; flipped per hovered row below
  if (
    hoveredIdx != null
    && hoveredIdx >= 0
    && hoveredIdx < n
    && dateOfLastExtreme.some((d) => d != null)
  ) {
    const ed = dateOfLastExtreme[hoveredIdx];
    if (ed != null) {
      // Place the triangle at the HOVERED date's index (mov_ave_rsi.date),
      // on the short series value at that date — the triangle "points to"
      // the date_of_last_extreme that applies to this hovered row.
      const sv = shorts[hoveredIdx];
      if (sv != null && Number.isFinite(sv)) {
        lastExtremeData[hoveredIdx] = sv;
      }
      // Direction: gap ≥ 0 → rising since a MIN → up triangle;
      // gap < 0 → falling since a MAX → down triangle.
      const gap = gapSinceLastExtreme[hoveredIdx];
      lastExtremeRising = !(gap != null && Number.isFinite(gap) && gap < 0);
    }
  }
  const hasLastExtreme = lastExtremeData.some((v) => v != null);

  // ---- Nearby-extreme bands (light-red horizontal bands) ----------------
  // For each valley low that has a nearby_extreme_date, draw a horizontal
  // light-red band linking the two dates. Upper bound = max of the two
  // days' OHLC highs; lower bound = min of the two days' OHLC lows. Both
  // dates must be present in the (slider-filtered) rows for the band to
  // render — consistent with how valley-low markers respect the slider.
  const NEARBY_BAND_FILL = "rgba(229, 57, 53, 0.12)";
  const NEARBY_BAND_BORDER = "rgba(229, 57, 53, 0.35)";

  interface NearbyBand {
    startDate: string;
    endDate: string;
    startIndex: number;
    endIndex: number;
    lower: number;
    upper: number;
  }

  // date → {high, low, index} lookup from the (filtered) rows.
  const ohlcByDate = new Map<
    string,
    { high: number | null; low: number | null; index: number }
  >();
  for (let i = 0; i < n; i++) {
    ohlcByDate.set(dates[i], { high: highs[i], low: lows[i], index: i });
  }

  const nearbyBands: NearbyBand[] = [];
  for (const v of valleyLows) {
    const nd = v.nearby_extreme_date;
    if (!nd || nd === v.date) continue;
    const o1 = ohlcByDate.get(v.date);
    const o2 = ohlcByDate.get(nd);
    if (!o1 || !o2) continue;
    const h1 = o1.high, l1 = o1.low, h2 = o2.high, l2 = o2.low;
    if (h1 == null || l1 == null || h2 == null || l2 == null) continue;
    if (
      !Number.isFinite(h1) || !Number.isFinite(l1) ||
      !Number.isFinite(h2) || !Number.isFinite(l2)
    ) continue;
    const lower = Math.min(l1, l2);
    const upper = Math.max(h1, h2);
    const i1 = o1.index;
    const i2 = o2.index;
    nearbyBands.push({
      startDate: i1 <= i2 ? v.date : nd,
      endDate: i1 <= i2 ? nd : v.date,
      startIndex: Math.min(i1, i2),
      endIndex: Math.max(i1, i2),
      lower,
      upper,
    });
  }
  const hasNearbyBands = nearbyBands.length > 0;
  type NearbyBandPoint = { coord: [string, number] };
  type NearbyBandMarkAreaItem = [
    { coord: [string, number]; itemStyle: { color: string; borderColor: string; borderWidth: number } },
    NearbyBandPoint,
  ];

  // ---- Percentage mode: base value for y-axis label + tooltip conversion --
  // In percentage mode the chart data stays in absolute units (identical
  // rendering to absolute mode); only the y-axis labels and tooltip values
  // are converted to % change from the first valid close. This keeps the
  // visual style (gap fill, Bollinger band shade, OHLC bars) exactly the
  // same in both modes.
  let baseVal: number | null = null;
  for (let i = 0; i < n; i++) {
    const v = shorts[i];
    if (v != null && Number.isFinite(v) && Math.abs(v) >= 1e-9) {
      baseVal = v;
      break;
    }
  }
  const hasBase = baseVal != null && Number.isFinite(baseVal) && Math.abs(baseVal) >= 1e-9;

  /** Convert an absolute price value to a % change string from baseVal. */
  const fmtPctFromBase = (v: number | null | undefined): string => {
    if (v == null || !Number.isFinite(v) || !hasBase || baseVal == null) return "—";
    return fmtPct((v / baseVal - 1) * 100, 2);
  };
  /** Format an absolute value: % from base in percentage mode, raw in absolute. */
  const fmtPrice = (v: number | null | undefined): string =>
    ohlcMode === "percentage" ? fmtPctFromBase(v) : fmtNum(v);

  // nearbyBandMarkArea — uses absolute bounds (same as absolute mode).
  const nearbyBandMarkArea: NearbyBandMarkAreaItem[] = nearbyBands.map((b) => [
    {
      coord: [b.startDate, b.lower],
      itemStyle: {
        color: NEARBY_BAND_FILL,
        borderColor: NEARBY_BAND_BORDER,
        borderWidth: 0.5,
      },
    },
    { coord: [b.endDate, b.upper] },
  ]) as NearbyBandMarkAreaItem[];

  // OHLC bar data: [open, close, low, high] per date, shared ohlcSeries format.
  // Null for dates missing any OHLC component.
  const ohlcData: Array<Array<number | null>> = new Array(n).fill(null);
  if (isPricePair) {
    for (let i = 0; i < n; i++) {
      const o = opens[i];
      const cl = shorts[i]; // close = short_value for Price/MA pairs
      const l = lows[i];
      const h = highs[i];
      if (o != null && cl != null && l != null && h != null) {
        ohlcData[i] = [o, cl, l, h];
      }
    }
  }

  // Gap fill stack — uses absolute shorts/longs (same as absolute mode).
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

  // ---- Bollinger envelope (Price/MA and Price/EMA pairs) ----
  // Both SMA and EMA detail tables carry std_*days columns (σ of price over
  // W days, ddof=0). For EMA pairs, the σ comes from the EMA detail table
  // (ema.std_*days, aliased as ema_std_*days in the chart SQL); for SMA
  // pairs, from the SMA detail table (d.std_*days). MA5/MA and EMA6/EMA
  // pairs (ma_short !== 0) don't get the envelope (σ is of price, not of
  // an MA-of-MA), matching the SMA pattern.
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

  // Legend data
  const legendData: string[] = [sName, lName];
  if (showBoll) {
    legendData.push(upperName, lowerName);
  }
  if (tradingAmtMode !== "off") {
    legendData.push("Amt Up", "Amt Down");
  }
  if (hasValleyLows) {
    legendData.push("Valley Low");
  }
  // Note: "Last Extreme" is intentionally NOT added to the legend — the
  // triangle is a transient hover-driven marker (one at a time), not a
  // togglable series the user can show/hide.
  if (hasNearbyBands) {
    legendData.push("Nearby Extreme");
  }
  // Trend legend entries are shown as dummy series below (markArea itself
  // doesn't produce legend items).
  const trendLegendNames = hasTrendBands ? ["▼ Downward", "▬ Flat", "▲ Upward"] : [];
  if (hasTrendBands) {
    legendData.push(...trendLegendNames);
  }

  // Trading amount bar data — null-filtered (null where no trading amount).
  const amtBarData: Array<number | null> = tradingAmts.map((v) =>
    v != null && Number.isFinite(v) ? v : null,
  );

  // Build series array
  const echartsSeries: EChartsOption["series"] = [];

  // ---- Price/MA pair: OHLC bars instead of line ----
  if (isPricePair) {
    echartsSeries.push(ohlcSeries(ohlcData, { name: sName, z: 5 }));
  } else {
    // ---- MA/MA pair: short line ----
    echartsSeries.push({
      type: "line",
      name: sName,
      data: shorts,
      symbol: "none",
      lineStyle: { color: sColor, width: 1.4 },
      z: 5,
    });
  }

  // Long MA line (always present). Trend markArea is attached here (only
  // on the Price/MA60 pair) so the background bands render behind the MA line.
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
  });

  // ---- Gap fill stack ----
  echartsSeries.push({
    type: "line",
    name: "_base",
    data: baseData,
    stack: "gapFill",
    symbol: "none",
    lineStyle: { opacity: 0 },
    z: 1,
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

  // ---- Bollinger envelope series ----
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

  // ---- Peak markers (green up triangles) ---------------------------------
  // Plotted on the primary (price) y-axis at (peak_date, peak_val).
  // Local maxima detected by the peaks_and_floors algorithm (close >
  // MA60 + 2σ upper Bollinger band, or close > MA60 for > 20 days;
  // continuous belt with < 5 day interruptions). Peaks have
  // nearby_extreme_date = NULL, so no markArea band is attached.
  if (hasPeaks) {
    echartsSeries.push({
      type: "scatter",
      name: "Peak",
      data: peakData,
      symbol: "triangle",
      symbolSize: 12,
      symbolRotate: 0, // point up
      itemStyle: {
        color: "#43A047", // green
        borderColor: "#1B5E20",
        borderWidth: 0.5,
      },
      z: 20,
    });
  }

  // ---- Valley-low markers (red down triangles) ---------------------------
  // Plotted on the primary (price) y-axis at (valley_low_date, valley_low).
  // Shown on all pair charts as a visual reference for the monthly valley
  // low detected by the peaks_and_floors algorithm (close < MA60 − 2σ
  // Bollinger band, continuous belt with < 5 day interruptions).
  if (hasValleyLows) {
    echartsSeries.push({
      type: "scatter",
      name: "Valley Low",
      data: valleyLowData,
      symbol: "triangle",
      symbolSize: 12,
      symbolRotate: 180, // point down
      itemStyle: {
        color: "#E53935", // red
        borderColor: "#B71C1C",
        borderWidth: 0.5,
      },
      z: 20,
      // Light-red horizontal band linking each valley_low_date with its
      // nearby_extreme_date. Upper/lower bounds come from the two days'
      // OHLC highs/lows. Attached to the valley-low scatter (z=20) so the
      // band renders above price/MA lines as a highlight overlay.
      ...(hasNearbyBands
        ? {
            markArea: {
              silent: true,
              data: nearbyBandMarkArea,
            },
          }
        : {}),
    });
  }

  // ---- Last-extreme hover marker (single small lowkey triangle) -----------
  // Only ONE triangle is drawn at a time — at the hovered date's
  // date_of_last_extreme position on the short series. Points UP (green) when
  // price has been rising since the last extreme (gap ≥ 0, last was a MIN) or
  // DOWN (red) when falling (gap < 0, last was a MAX). Small + semi-transparent
  // so it stays lowkey next to the valley-low markers. Hovering any date
  // surfaces date_of_last_extreme + gap_since_last_extreme +
  // days_since_last_extreme in the tooltip (see formatter below), colored to
  // match the triangle (green MIN / red MAX).
  if (hasLastExtreme) {
    // green for MIN (rising), red for MAX (falling) — matches tooltip color.
    const leColor = lastExtremeRising
      ? { rgb: "67, 160, 71", hex: "#43A047" }   // green
      : { rgb: "229, 57, 53", hex: "#E53935" };  // red
    echartsSeries.push({
      type: "scatter",
      name: "Last Extreme",
      data: lastExtremeData,
      symbol: "triangle",
      symbolSize: 7,
      // ECharts rotates the triangle symbol clockwise. The default triangle
      // points UP; rotate 180° to point DOWN.
      symbolRotate: lastExtremeRising ? 0 : 180,
      itemStyle: {
        color: `rgba(${leColor.rgb}, 0.55)`, // lowkey (semi-transparent)
        borderColor: leColor.hex,
        borderWidth: 0.5,
      },
      z: 19,
      // Keep the marker out of the tooltip's series list — the tooltip
      // already surfaces the last-extreme info explicitly below.
      tooltip: { show: false },
    });
  }

  // ---- Nearby-extreme band legend dummy series --------------------------
  // markArea doesn't produce a legend entry, so add an invisible dummy
  // scatter with one null point — the legend swatch shows the band color.
  if (hasNearbyBands) {
    echartsSeries.push({
      type: "scatter",
      name: "Nearby Extreme",
      data: [null],
      symbol: "rect",
      symbolSize: 7,
      itemStyle: { color: "rgba(229, 57, 53, 0.5)" },
      z: 0,
    });
  }

  // ---- Trend legend dummy series (Price/MA60 pair only) -----------------
  // markArea doesn't produce legend entries, so add 3 invisible dummy
  // scatter series with 1 null point each — the legend item shows the
  // trend color, but nothing renders on the chart.
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

  // ---- Trading amount bars on secondary y-axis (up/down colored) ----
  if (tradingAmtMode !== "off") {
    const barOpacity = 0.15;
    const barWidth = "50%";

    // Split into up (green) and down (red) series for OHLC-based coloring.
    // For Price/MA pairs, direction = close >= open.
    // For MA/MA pairs, direction = short >= long (gap sign).
    const upData: Array<number | null> = new Array(n).fill(null);
    const downData: Array<number | null> = new Array(n).fill(null);

    for (let i = 0; i < n; i++) {
      const amt = amtBarData[i];
      if (amt == null) continue;
      let isUp: boolean;
      if (isPricePair) {
        const o = opens[i];
        const cl = shorts[i]; // close
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

  // Grid with more right margin for the secondary axis label
  const grid = commonGrid({ left: 55, right: 55, bottom: 50 });

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
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          axisValue?: string;
          seriesName?: string;
          value?: number | Array<number | null> | null;
          marker?: string;
        }>;
        if (arr.length === 0) return "";
        const dateStr = (arr[0].axisValue as string) || "";
        let html = `<div style="font-weight:600;margin-bottom:2px">${dateStr}</div>`;
        const idx = dates.indexOf(dateStr);
        if (idx >= 0) {
          const sv = shorts[idx];
          const lv = longs[idx];
          const gv = sv != null && lv != null && lv !== 0 ? (sv - lv) / lv : null;
          const ss = shortSlopes[idx];
          const sc = shortCurvs[idx];
          const ls = longSlopes[idx];
          const lc = longCurvs[idx];

          // OHLC for Price/MA pairs
          if (isPricePair) {
            const o = opens[idx];
            const h = highs[idx];
            const l = lows[idx];
            const cl = shorts[idx];
            html += `<div>O: ${fmtPrice(o)}  H: ${fmtPrice(h)}</div>`;
            html += `<div>L: ${fmtPrice(l)}  C: ${fmtPrice(cl)}</div>`;
          } else {
            html += `<div>${sName}: ${fmtPrice(sv)}</div>`;
          }

          html += `<div>${lName}: ${fmtPrice(lv)}</div>`;
          html += `<div>gap: ${gv != null ? fmtPct(gv * 100, 3) : "—"}</div>`;

          // Trading amount
          const amt = tradingAmts[idx];
          if (amt != null) {
            html += `<div style="margin-top:2px;opacity:0.85">Trading Amt: ${fmtAmtYi(amt)}</div>`;
          }

          // Trading-amount MA slope + exchange market share (only when
          // trading-amt display is enabled). Shows the selected pair's long
          // MA window — e.g. Price/MA60 surfaces trading_amt_ma60_slope and
          // trading_amt_market_share_ma60. The slope is a fractional daily
          // change (signed ratio, shown as %); the market share is a
          // dimensionless ratio 0..1 (shown as % of total-market turnover).
          if (tradingAmtMode !== "off") {
            const aSlope = amtMaSlopeOfLong[idx];
            const aShare = amtMarketShareOfLong[idx];
            if (aSlope != null || aShare != null) {
              const slopeStr = aSlope != null
                ? fmtPct(aSlope * 100, 2)
                : "—";
              const shareStr = aShare != null
                ? fmtPct(aShare * 100, 4)
                : "—";
              const slopeColor = aSlope != null && aSlope < 0
                ? DOWN_COLOR
                : UP_COLOR;
              html += `<div style="opacity:0.85">Amt MA${pair.ma_long} slope: ` +
                `<span style="color:${slopeColor};font-weight:600">${slopeStr}</span>` +
                ` · mkt share: ${shareStr}</div>`;
            }
          }

          // slope + curvature.
          // In percentage mode, slope (1st derivative) and curvature (2nd
          // derivative) are rebased to 100 by scaling with 100/baseVal — i.e.,
          // they become the slope/curvature of the rebased series (where the
          // first valid short value = 100). Tooltip annotates the rebase so
          // the user knows the values are on the rebased scale.
          const isPctSlope = ohlcMode === "percentage" && hasBase && baseVal != null;
          const slopeScale = isPctSlope && baseVal != null ? 100 / baseVal : 1;
          const fmtSlope = (v: number | null | undefined): string =>
            fmtNum(v != null && Number.isFinite(v) ? v * slopeScale : null);
          const rebasedTag = isPctSlope
            ? ' <span style="opacity:0.6;font-size:0.9em">(rebased to 100)</span>'
            : "";
          html += `<div style="margin-top:2px;opacity:0.85">${sName} slope: ${fmtSlope(ss)} · curv: ${fmtSlope(sc)}${rebasedTag}</div>`;
          html += `<div style="opacity:0.85">${lName} slope: ${fmtSlope(ls)} · curv: ${fmtSlope(lc)}${rebasedTag}</div>`;

          // Bollinger band values.
          // Upper/Lower are price levels → use fmtPrice (% change from base
          // in percentage mode). σ and band width are scatter/difference
          // measures → scale linearly by 100/baseVal (same as slope/curv),
          // NOT the % change formula. The previous fmtPrice(uv - lo) was
          // buggy in percentage mode (applied % change to a difference).
          if (showBoll) {
            const uv = upperData[idx];
            const lo = lowerData[idx];
            const sd = longStds[idx];
            const bw = uv != null && lo != null ? uv - lo : null;
            html += `<div style="margin-top:2px;opacity:0.85">Upper: ${fmtPrice(uv)} · Lower: ${fmtPrice(lo)}</div>`;
            html += `<div style="opacity:0.85">σ${pair.ma_long}d: ${fmtSlope(sd)} · band width: ${fmtSlope(bw)}${rebasedTag}</div>`;
          }

          // Peak / valley-low marker info
          const pk = peakData[idx];
          const vl = valleyLowData[idx];
          if (pk != null) {
            html += `<div style="margin-top:2px;color:#43A047;font-weight:600">▲ Peak: ${fmtPrice(pk)}</div>`;
          }
          if (vl != null) {
            html += `<div style="margin-top:2px;color:#E53935;font-weight:600">▼ Valley Low: ${fmtPrice(vl)}</div>`;
          }

          // Wilder RSI (6/10/14/20 days) from analysis.mov_ave_rsi — shared
          // across all 9 pairs for a given date. Values 0..100 (NULL until N
          // periods). Colored amber when overbought (≥70) or green when
          // oversold (≤30) on the classic 14-day window; otherwise neutral.
          const r6 = rsi6[idx], r10 = rsi10[idx], r14 = rsi14[idx], r20 = rsi20[idx];
          if (r6 != null || r10 != null || r14 != null || r20 != null) {
            const fmtRsi = (v: number | null | undefined): string =>
              v != null && Number.isFinite(v) ? v.toFixed(1) : "—";
            const ref = r14 ?? r10 ?? r6 ?? r20;
            let rsiColor = "#9E9E9E"; // neutral grey
            if (ref != null && Number.isFinite(ref)) {
              if (ref >= 70) rsiColor = "#FB8C00"; // amber — overbought
              else if (ref <= 30) rsiColor = "#43A047"; // green — oversold
            }
            html += `<div style="margin-top:2px;color:${rsiColor};opacity:0.9">` +
              `RSI: 6d ${fmtRsi(r6)} · 10d ${fmtRsi(r10)} · 14d ${fmtRsi(r14)} · 20d ${fmtRsi(r20)}` +
              `</div>`;
          }

          // Last-extreme (turning point) info from analysis.mov_ave_rsi.
          // date_of_last_extreme is the biz date of the most recent local
          // high/low; gap_since_last_extreme sign indicates max vs min
          // (positive = last was a MIN → green up triangle; negative = last
          // was a MAX → red down triangle); days_since_last_extreme is the
          // trading-day gap. Tooltip color matches the in-plot triangle.
          const leDate = dateOfLastExtreme[idx];
          if (leDate != null) {
            const leGap = gapSinceLastExtreme[idx];
            const leDays = daysSinceLastExtreme[idx];
            const isMin = !(leGap != null && Number.isFinite(leGap) && leGap < 0);
            const leHex = isMin ? "#43A047" : "#E53935"; // green MIN / red MAX
            // The triangle marker is at the hovered date (mov_ave_rsi.date);
            // report the extreme PRICE by looking up the short value at the
            // date_of_last_extreme (the actual turning-point bar).
            const leMarkIdx = dates.indexOf(leDate);
            const leMark = leMarkIdx >= 0 ? shorts[leMarkIdx] : null;
            const arrow = leGap != null && Number.isFinite(leGap)
              ? (isMin ? "▲ MIN" : "▼ MAX")
              : "▲";
            html += `<div style="margin-top:2px;color:${leHex};font-weight:600">Last Extreme: ${leDate} (${arrow}` +
              (leDays != null && Number.isFinite(leDays) ? `, ${Math.round(leDays)}d` : "") +
              `)</div>`;
            html += `<div style="color:${leHex};opacity:0.9">` +
              `gap_since_last_extreme: ${leGap != null && Number.isFinite(leGap) ? fmtPct(leGap * 100, 2) : "—"} · ` +
              `days_since_last_extreme: ${leDays != null && Number.isFinite(leDays) ? Math.round(leDays) : "—"}` +
              `</div>`;
            if (leMark != null && Number.isFinite(leMark)) {
              html += `<div style="color:${leHex};opacity:0.9">extreme ${sName}: ${fmtPrice(leMark)}</div>`;
            }
          }

          // Nearby-extreme band info
          if (hasNearbyBands) {
            const band = nearbyBands.find(
              (b) => idx >= b.startIndex && idx <= b.endIndex,
            );
            if (band) {
              html += `<div style="margin-top:2px;color:#E53935;opacity:0.9">Nearby Extreme: ${band.startDate} ↔ ${band.endDate} · [${fmtPrice(band.lower)}, ${fmtPrice(band.upper)}]</div>`;
            }
          }

          // Trend classification (Price/MA60 pair only)
          if (hasTrendBands) {
            const band = trendBands.find(
              (b) => idx >= b.startIdx && idx <= b.endIdx,
            );
            if (band) {
              const trendLabels: Record<TrendType, string> = {
                downward: "▼ Downward",
                flat: "▬ Flat",
                upward: "▲ Upward",
              };
              const trendColors: Record<TrendType, string> = {
                downward: "#E53935",
                flat: "#FFB300",
                upward: "#43A047",
              };
              const periodStart = dates[band.startIdx];
              const periodEnd = dates[band.endIdx];
              const nDays = band.endIdx - band.startIdx + 1;
              const periodText = periodStart === periodEnd
                ? `${periodStart} (${nDays}d)`
                : `${periodStart} → ${periodEnd} (${nDays}d)`;
              html += `<div style="margin-top:2px;color:${trendColors[band.trend]};font-weight:600">Trend: ${trendLabels[band.trend]} · ${periodText}</div>`;
            }
          }
        }
        return html;
      },
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
      // Right axis: trading amount in 亿元
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

// ============================================================================
//  Amt Envelope chart — rendered when an Amt/MA pair is selected.
//  Shows trading_amount BARS + a slim envelope of all 5 trading_amt_ma lines
//  on the right y-axis (yuan), with the OHLC/price curve shown lowkey
//  (dimmed) on the left y-axis (price). Bars are up/down colored against
//  the SELECTED MA (above-MA = heavy/green, below-MA = light/red) so the
//  focused pair drives the heavy/light split. The 5 MA lines form the
//  envelope band; the selected one is a touch thicker + brighter, the
//  other 4 are thin and faint. A faint fill between max/min of the 5 MAs
//  shows the envelope range.
// ============================================================================

/** Trading-amount MA windows in canonical order (matches LONG_MA_ORDER). */
const AMT_MA_WINDOWS = [5, 20, 60, 120, 255] as const;

/** Color for each trading_amt_ma window — reuses the price MA palette so
 *  the envelope lines are visually consistent with the price MA lines. */
const AMT_MA_COLORS: Record<number, string> = {
  5:   MA5_COLOR,
  20:  MA20_COLOR,
  60:  MA60_COLOR,
  120: MA120_COLOR,
  255: MA255_COLOR,
};

export interface BuildAmtEnvelopeOptionArgs {
  /** The selected Amt/MA pair's full time series.
   *  pair.ma_long = W (the selected MA window: 5/20/60/120/255).
   *  pair.rows[].short_value = trading_amount (yuan).
   *  pair.rows[].long_value  = trading_amt_maW (yuan).
   *  pair.rows[].trading_amt_ma{5,20,60,120,255} = all 5 MA values (for
   *  the envelope). */
  pair: MovAveSpreadPairSeries;
  /** Current theme mode (light / dark). */
  themeMode: ThemeMode;
  /** Display mode for the lowkey price series (absolute / percentage). */
  ohlcMode?: OhlcMode;
}

/** Build the ECharts option for the Amt Envelope chart.
 *
 *  Layout:
 *    Left y-axis (price, LOWKEY):
 *      - High-low band (faint area, opacity 0.08) + close-proxy line
 *        (opacity 0.2) — the OHLC curve shown lowkey as a price reference.
 *    Right y-axis (amount, PROMINENT):
 *      - Trading amount BARS (up/down colored vs the SELECTED MA,
 *        opacity 0.42, barWidth 60%) — the main visual.
 *      - 5 trading_amt_ma lines (the envelope curves):
 *          Selected MA (pair.ma_long): width 1.5, opacity 0.85
 *          Other 4 MAs: width 0.8, opacity 0.4
 *      - Fill between max/min of all 5 MAs: faint band (envelope range).
 *    Tooltip: date + trading_amount + all 5 MAs + selected pair's gap +
 *      H/L.
 */
export function buildAmtEnvelopeOption({
  pair,
  themeMode,
  ohlcMode = "absolute",
}: BuildAmtEnvelopeOptionArgs): EChartsOption {
  const c = axisColors(themeMode);
  const rows = pair.rows;
  const n = rows.length;
  const selectedWindow = pair.ma_long;

  const dates = rows.map((r) => r.date);
  // Amt pair rows carry open/high/low (price OHLC) + trading_amount (as
  // short_value) + all 5 trading_amt_ma{5,20,60,120,255}. Close is NOT
  // stored on amt pair rows (short_value = trading_amount), so the lowkey
  // price reference uses (high + low) / 2 as a close proxy.
  const highs = rows.map((r) => r.high);
  const lows = rows.map((r) => r.low);
  const tradingAmts = rows.map((r) => r.trading_amount);

  // 5 trading-amount MA arrays for the envelope.
  const amtMaArrays: Record<number, Array<number | null>> = {
    5:   rows.map((r) => r.trading_amt_ma5),
    20:  rows.map((r) => r.trading_amt_ma20),
    60:  rows.map((r) => r.trading_amt_ma60),
    120: rows.map((r) => r.trading_amt_ma120),
    255: rows.map((r) => r.trading_amt_ma255),
  };
  // 5 trading-amount MA SLOPE arrays (fractional daily change) for the
  // tooltip — surfaces the day-over-day % change of each Amt MA line.
  const amtMaSlopeArrays: Record<number, Array<number | null>> = {
    5:   rows.map((r) => r.trading_amt_ma5_slope),
    20:  rows.map((r) => r.trading_amt_ma20_slope),
    60:  rows.map((r) => r.trading_amt_ma60_slope),
    120: rows.map((r) => r.trading_amt_ma120_slope),
    255: rows.map((r) => r.trading_amt_ma255_slope),
  };
  // 5 trading-amount MARKET-SHARE MA arrays (ratio 0..1) for the tooltip —
  // surfaces the W-day MA market share as a percentage.
  const amtMarketShareArrays: Record<number, Array<number | null>> = {
    5:   rows.map((r) => r.trading_amt_market_share_ma5),
    20:  rows.map((r) => r.trading_amt_market_share_ma20),
    60:  rows.map((r) => r.trading_amt_market_share_ma60),
    120: rows.map((r) => r.trading_amt_market_share_ma120),
    255: rows.map((r) => r.trading_amt_market_share_ma255),
  };

  // Envelope band: max and min of all 5 MAs per date.
  const envUpper: Array<number | null> = new Array(n).fill(null);
  const envLower: Array<number | null> = new Array(n).fill(null);
  for (let i = 0; i < n; i++) {
    const vals: number[] = [];
    for (const w of AMT_MA_WINDOWS) {
      const v = amtMaArrays[w][i];
      if (v != null && Number.isFinite(v)) vals.push(v);
    }
    if (vals.length > 0) {
      envUpper[i] = Math.max(...vals);
      envLower[i] = Math.min(...vals);
    }
  }

  // ---- Percentage mode base for price labels ----
  let baseVal: number | null = null;
  for (let i = 0; i < n; i++) {
    const v = highs[i];
    if (v != null && Number.isFinite(v) && Math.abs(v) >= 1e-9) {
      baseVal = v;
      break;
    }
  }
  const hasBase = baseVal != null && Number.isFinite(baseVal);
  const fmtPctFromBase = (v: number | null | undefined): string => {
    if (v == null || !Number.isFinite(v) || !hasBase || baseVal == null) return "—";
    return fmtPct((v / baseVal - 1) * 100, 2);
  };
  const fmtPrice = (v: number | null | undefined): string =>
    ohlcMode === "percentage" ? fmtPctFromBase(v) : fmtNum(v);

  // ---- Build series ----
  const echartsSeries: NonNullable<EChartsOption["series"]> = [];

  // 1) Lowkey high-low band (price reference). Faint stacked area between
  //    low and high gives a sense of the price range without OHLC bars.
  const hlBase: Array<number | null> = lows.slice();
  const hlDelta: Array<number | null> = new Array(n).fill(null);
  for (let i = 0; i < n; i++) {
    const h = highs[i];
    const l = lows[i];
    if (h != null && l != null) {
      hlDelta[i] = h - l;
    }
  }
  echartsSeries.push({
    type: "line",
    name: "_hlBase",
    data: hlBase,
    stack: "hlBand",
    symbol: "none",
    lineStyle: { opacity: 0 },
    yAxisIndex: 0,
    z: 0,
  });
  echartsSeries.push({
    type: "line",
    name: "_hlDelta",
    data: hlDelta,
    stack: "hlBand",
    symbol: "none",
    lineStyle: { opacity: 0 },
    areaStyle: { color: SPOT_COLOR, opacity: 0.08 },
    yAxisIndex: 0,
    z: 0,
  });

  // 2) Lowkey price line (close proxy = (high+low)/2).
  const closeProxy: Array<number | null> = new Array(n).fill(null);
  for (let i = 0; i < n; i++) {
    const h = highs[i];
    const l = lows[i];
    if (h != null && l != null) {
      closeProxy[i] = (h + l) / 2;
    }
  }
  echartsSeries.push({
    type: "line",
    name: "Price",
    data: closeProxy,
    symbol: "none",
    lineStyle: { color: SPOT_COLOR, width: 1, opacity: 0.25 },
    yAxisIndex: 0,
    z: 1,
  });

  // 3) Envelope fill band (max - min of all 5 trading_amt_ma lines).
  echartsSeries.push({
    type: "line",
    name: "_envBase",
    data: envLower,
    stack: "envBand",
    symbol: "none",
    lineStyle: { opacity: 0 },
    yAxisIndex: 1,
    z: 2,
  });
  echartsSeries.push({
    type: "line",
    name: "_envDelta",
    data: envUpper.map((u, i) => {
      const l = envLower[i];
      return u != null && l != null ? u - l : null;
    }),
    stack: "envBand",
    symbol: "none",
    lineStyle: { opacity: 0 },
    areaStyle: { color: BOLL_BAND_FILL, opacity: 0.10 },
    yAxisIndex: 1,
    z: 2,
  });

  // 4) Trading amount bars (up/down colored by amt vs the SELECTED MA).
  //    Bars above the selected MA → "heavy" (UP green); below → "light"
  //    (DOWN red). This ties the bar coloring to the focused pair so the
  //    user can spot above/below-average volume days at a glance. Stacked
  //    under the same "amt" stack so up/down never overlap (only one is
  //    non-null per day).
  const selectedAmtMa = amtMaArrays[selectedWindow];
  const amtUpData: Array<number | null> = new Array(n).fill(null);
  const amtDownData: Array<number | null> = new Array(n).fill(null);
  for (let i = 0; i < n; i++) {
    const amt = tradingAmts[i];
    const ma = selectedAmtMa[i];
    if (amt == null || !Number.isFinite(amt)) continue;
    const isUp = ma != null && Number.isFinite(ma) ? amt >= ma : true;
    if (isUp) amtUpData[i] = amt;
    else amtDownData[i] = amt;
  }
  echartsSeries.push({
    type: "bar",
    name: "Amt Above",
    data: amtUpData,
    yAxisIndex: 1,
    barWidth: "60%",
    stack: "amt",
    itemStyle: { color: UP_COLOR, opacity: 0.42 },
    z: 3,
  });
  echartsSeries.push({
    type: "bar",
    name: "Amt Below",
    data: amtDownData,
    yAxisIndex: 1,
    barWidth: "60%",
    stack: "amt",
    itemStyle: { color: DOWN_COLOR, opacity: 0.42 },
    z: 3,
  });

  // 5) 5 trading_amt_ma lines — the envelope curves. Selected one is a
  //    touch thicker + brighter; the other 4 are thin and faint so they
  //    read as a band rather than competing lines. Kept slim to fit the
  //    trading-amt style (no thick curves).
  for (const w of AMT_MA_WINDOWS) {
    const isSelected = w === selectedWindow;
    echartsSeries.push({
      type: "line",
      name: `Amt MA${w}`,
      data: amtMaArrays[w],
      symbol: "none",
      smooth: 0.2,
      lineStyle: {
        color: AMT_MA_COLORS[w],
        width: isSelected ? 1.5 : 0.8,
        opacity: isSelected ? 0.85 : 0.4,
      },
      yAxisIndex: 1,
      z: isSelected ? 7 : 5,
    });
  }

  // ---- Legend ----
  const legendData: string[] = ["Price", "Amt Above", "Amt Below"];
  for (const w of AMT_MA_WINDOWS) {
    legendData.push(`Amt MA${w}`);
  }

  const grid = commonGrid({ left: 55, right: 65, bottom: 50 });

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
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          axisValue?: string;
        }>;
        if (arr.length === 0) return "";
        const dateStr = (arr[0].axisValue as string) || "";
        let html = `<div style="font-weight:600;margin-bottom:2px">${dateStr}</div>`;
        const idx = dates.indexOf(dateStr);
        if (idx >= 0) {
          const amt = tradingAmts[idx];
          html += `<div style="margin-top:2px">Trading Amt: ${fmtAmtYi(amt)}</div>`;
          for (const w of AMT_MA_WINDOWS) {
            const v = amtMaArrays[w][idx];
            const marker = w === selectedWindow ? " ●" : "";
            html += `<div style="opacity:${w === selectedWindow ? 1 : 0.7}">Amt MA${w}: ${fmtAmtYi(v)}${marker}</div>`;
          }
          const sv = tradingAmts[idx];
          const lv = amtMaArrays[selectedWindow][idx];
          if (sv != null && lv != null && lv !== 0) {
            const gap = (sv - lv) / lv;
            const gapColor = gap >= 0 ? UP_COLOR : DOWN_COLOR;
            html += `<div style="margin-top:2px;color:${gapColor};font-weight:600">gap (Amt vs MA${selectedWindow}): ${fmtPct(gap * 100, 3)}</div>`;
          }
          // Slope + market share for the SELECTED MA window — the day-over-
          // day % change of trading_amt_ma{selectedWindow} and its W-day MA
          // market share. Shown on the amt envelope chart (this tooltip).
          const sl = amtMaSlopeArrays[selectedWindow][idx];
          const sh = amtMarketShareArrays[selectedWindow][idx];
          if (sl != null || sh != null) {
            const slopeStr = sl != null ? fmtPct(sl * 100, 2) : "—";
            const shareStr = sh != null ? fmtPct(sh * 100, 4) : "—";
            const slopeColor = sl != null && sl < 0 ? DOWN_COLOR : UP_COLOR;
            html += `<div style="margin-top:2px;opacity:0.85">Amt MA${selectedWindow} slope: ` +
              `<span style="color:${slopeColor};font-weight:600">${slopeStr}</span>` +
              ` · mkt share: ${shareStr}</div>`;
          }
          const h = highs[idx];
          const l = lows[idx];
          html += `<div style="margin-top:2px;opacity:0.5">H: ${fmtPrice(h)} · L: ${fmtPrice(l)}</div>`;
        }
        return html;
      },
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
        name: ohlcMode === "percentage" ? "%" : "Price",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => fmtPrice(v),
        },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.3 } },
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