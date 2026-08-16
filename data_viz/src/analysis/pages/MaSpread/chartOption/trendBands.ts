/**
 * Trend classification logic for the MA-Spread chart.
 *
 * downward_trend: close < MA60, with <5 day interruptions bridged.
 * flat_trend:     MA60 slope ∈ [−0.5, +0.5]
 * upward_trend:   everything else
 */

// ---- Trend classification constants (Price/MA60 pair only) --------------
export const TREND_MAX_INTERRUPTION = 5;
export const TREND_FLAT_SLOPE_MIN = -0.5;
export const TREND_FLAT_SLOPE_MAX = 0.5;
export const TREND_DOWN_COLOR = "rgba(229, 57, 53, 0.07)"; // light red
export const TREND_FLAT_COLOR = "rgba(255, 193, 7, 0.07)"; // light amber
export const TREND_UP_COLOR = "rgba(76, 175, 80, 0.05)";   // light green

export type TrendType = "downward" | "flat" | "upward";

export interface TrendBand {
  startIdx: number;
  endIdx: number;
  trend: TrendType;
}

/**
 * Compute trend classification bands from Price/MA60 pair data.
 */
export function computeTrendBands(
  shorts: Array<number | null>,
  longs: Array<number | null>,
  longSlopes: Array<number | null>,
  longStds: Array<number | null>,
): TrendBand[] {
  const n = shorts.length;
  if (n === 0) return [];

  const belowMA60: boolean[] = new Array(n).fill(false);
  for (let i = 0; i < n; i++) {
    const s = shorts[i];
    const l = longs[i];
    if (s != null && l != null && Number.isFinite(s) && Number.isFinite(l)) {
      belowMA60[i] = s < l;
    }
  }

  const isDownward: boolean[] = new Array(n).fill(false);
  let i = 0;
  while (i < n) {
    if (!belowMA60[i]) {
      i++;
      continue;
    }
    let runEnd = i;
    let j = i + 1;
    while (j < n) {
      if (belowMA60[j]) {
        runEnd = j;
        j++;
      } else {
        let gapLen = 0;
        let k = j;
        while (k < n && !belowMA60[k]) {
          gapLen++;
          k++;
        }
        if (gapLen < TREND_MAX_INTERRUPTION && k < n && belowMA60[k]) {
          runEnd = k;
          j = k + 1;
        } else {
          break;
        }
      }
    }
    for (let m = i; m <= runEnd; m++) {
      isDownward[m] = true;
    }
    i = runEnd + 1;
  }

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

  // Weak-rebound reclassification
  const bands = rawBands.map((b) => ({ ...b }));
  for (let bi = 0; bi < bands.length; bi++) {
    if (bands[bi].trend !== "upward") continue;

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
        allWeak = false;
        break;
      }
    }
    if (!allWeak) continue;

    let hasNonFlatBefore = false;
    for (let bj = 0; bj < bi; bj++) {
      if (bands[bj].trend === "downward" || bands[bj].trend === "upward") {
        hasNonFlatBefore = true;
        break;
      }
    }
    if (!hasNonFlatBefore) continue;

    let hasNonFlatAfter = false;
    for (let bj = bi + 1; bj < bands.length; bj++) {
      if (bands[bj].trend === "downward" || bands[bj].trend === "upward") {
        hasNonFlatAfter = true;
        break;
      }
    }
    if (!hasNonFlatAfter) continue;

    bands[bi].trend = "flat";
  }

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
export function trendBandsToMarkArea(
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

/** Short-series label, e.g. "Price", "MA5", or "EMA6". */
export function shortLabel(maShort: number, kind?: string): string {
  if (maShort === 0) return "Price";
  return kind === "ema" ? `EMA${maShort}` : `MA${maShort}`;
}
