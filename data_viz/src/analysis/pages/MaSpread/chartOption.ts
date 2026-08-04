/**
 * ECharts option builder for the MA-Spread pair chart.
 *
 * Extracted from the former MaSpreadPage.tsx so the panel component stays
 * focused on data fetching + layout.
 *
 * Implementation: 5 series in one stack ("gapFill"):
 *   1. Visible short line (z=5, no stack)
 *   2. Visible long line  (z=5, no stack)
 *   3. Stack base (invisible): min(short, long)        — stack 'gapFill'
 *   4. Positive delta (green area): max(short - long, 0) — stack 'gapFill'
 *   5. Negative delta (red area):   max(long - short, 0) — stack 'gapFill'
 *
 * When short > long: base=long, pos=short-long, neg=0 → green fill spans
 *   [long, short] = [min, max].
 * When short < long: base=short, pos=0, neg=long-short → red fill spans
 *   [short, long] = [min, max].
 * When short == long: pos=neg=0 → no fill (lines touch).
 *
 * Bollinger envelope (Price/MA pairs only, ma_short === 0):
 *   When bollingerK > 0 and the pair is a Price/MA pair, 2 additional dashed
 *   lines are drawn around the long MA:
 *     Upper = long_value + k × long_std
 *     Lower = long_value - k × long_std
 *   where long_std is the rolling population σ of price over the long MA's
 *   window (std_5days / std_20days / ... / std_255days, precomputed in the
 *   detail table). A faint fill (opacity 0.08) between Upper and Lower
 *   highlights the envelope region without obscuring the gap fill. The 2
 *   band lines + 2 stack series for the fill are appended after the 5
 *   gap-fill series. MA5/MA pairs do not get the envelope — only Price/MA
 *   pairs do, by convention (the σ is of price, so it is meaningful to
 *   band the price's MA, not an MA of an MA).
 */
import { fmtNum, fmtPct } from "@/lib/series";
import {
  MA120_COLOR,
  UP_COLOR,
  DOWN_COLOR,
  SPOT_COLOR,
  BOLL_BAND_COLOR,
  BOLL_BAND_FILL,
  axisColors,
  commonLegend,
  commonGrid,
} from "@/theme/chart-palette";
import type { ThemeMode } from "@/store/filters";
import type { EChartsOption } from "echarts";
import type { MovAveSpreadPairSeries } from "../../../../shared/types";

// Color for the "price" series (ma_short = 0).
const PRICE_COLOR = SPOT_COLOR;

/** Short-series label, e.g. "Price" or "MA5". */
export function shortLabel(maShort: number): string {
  return maShort === 0 ? "Price" : `MA${maShort}`;
}

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
}

export function buildPairOption({
  pair,
  themeMode,
  bollingerK = 2,
}: BuildPairOptionArgs): EChartsOption {
  const c = axisColors(themeMode);
  const rows = pair.rows;
  const n = rows.length;

  const dates = rows.map((r) => r.date);
  const shorts = rows.map((r) => r.short_value);
  const longs = rows.map((r) => r.long_value);
  // slope / curvature arrays for the tooltip. short_slope / short_curvature
  // are populated for every pair — including Price/MA pairs (ma_short = 0),
  // which carry the 1st/2nd derivative of price itself.
  const shortSlopes = rows.map((r) => r.short_slope);
  const shortCurvs = rows.map((r) => r.short_curvature);
  const longSlopes = rows.map((r) => r.long_slope);
  const longCurvs = rows.map((r) => r.long_curvature);
  const longStds = rows.map((r) => r.long_std);

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
  const sName = shortLabel(pair.ma_short);
  const lName = `MA${pair.ma_long}`;

  // ---- Bollinger envelope (Price/MA pairs only) -------------------------
  // Upper = long + k×σ, Lower = long - k×σ. NULL when long or σ is NULL.
  // Also build the stacked-area fill: bollBase = Lower (invisible base),
  // bollDelta = Upper - Lower = 2k×σ (invisible line, visible area fill).
  // The fill opacity is very low (0.08) so it doesn't compete with the
  // green/red gap fill between the short and long lines.
  const isPricePair = pair.ma_short === 0;
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
      bollDelta[i] = upper - lower; // = 2 * bollingerK * sd
    }
  }
  const upperName = `Upper (+${bollingerK}σ)`;
  const lowerName = `Lower (−${bollingerK}σ)`;

  // Legend data: always show short + long; add Bollinger upper/lower only
  // when the envelope is drawn. The stack helper series (_base/_pos/_neg/
  // _bollBase/_bollDelta) are hidden from the legend.
  const legendData: string[] = [sName, lName];
  if (showBoll) {
    legendData.push(upperName, lowerName);
  }

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 55, right: 18, bottom: 30 }),
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
          html += `<div>${sName}: ${fmtNum(sv)}</div>`;
          html += `<div>${lName}: ${fmtNum(lv)}</div>`;
          html += `<div>gap: ${gv != null ? fmtPct(gv, 3) : "—"}</div>`;
          // slope (1st derivative) + curvature (2nd derivative) of both the
          // short series (price or MA) and the long MA.
          html += `<div style="margin-top:2px;opacity:0.85">${sName} slope: ${fmtNum(ss)} · curv: ${fmtNum(sc)}</div>`;
          html += `<div style="opacity:0.85">${lName} slope: ${fmtNum(ls)} · curv: ${fmtNum(lc)}</div>`;
          // Bollinger band values (only for Price/MA pairs with envelope on).
          if (showBoll) {
            const uv = upperData[idx];
            const lo = lowerData[idx];
            const sd = longStds[idx];
            html += `<div style="margin-top:2px;opacity:0.85">Upper: ${fmtNum(uv)} · Lower: ${fmtNum(lo)}</div>`;
            html += `<div style="opacity:0.85">σ${pair.ma_long}d: ${fmtNum(sd)} · band width: ${uv != null && lo != null ? fmtNum(uv - lo) : "—"}</div>`;
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
    yAxis: {
      type: "value",
      scale: true,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        formatter: (v: number) => fmtNum(v),
      },
      splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
    },
    series: [
      {
        type: "line",
        name: sName,
        data: shorts,
        symbol: "none",
        lineStyle: { color: sColor, width: 1.4 },
        z: 5,
      },
      {
        type: "line",
        name: lName,
        data: longs,
        symbol: "none",
        lineStyle: { color: lColor, width: 1.4 },
        z: 5,
      },
      {
        type: "line",
        name: "_base",
        data: baseData,
        stack: "gapFill",
        symbol: "none",
        lineStyle: { opacity: 0 },
        z: 1,
      },
      {
        type: "line",
        name: "_pos",
        data: posData,
        stack: "gapFill",
        symbol: "none",
        lineStyle: { opacity: 0 },
        areaStyle: { color: UP_COLOR, opacity: 0.4 },
        z: 2,
      },
      {
        type: "line",
        name: "_neg",
        data: negData,
        stack: "gapFill",
        symbol: "none",
        lineStyle: { opacity: 0 },
        areaStyle: { color: DOWN_COLOR, opacity: 0.4 },
        z: 2,
      },
      // ---- Bollinger envelope series (only rendered when showBoll = true) ----
      // Upper band: dashed line, drawn at z=4 so it sits above the gap-fill
      // stack but below the short/long lines (z=5).
      ...(showBoll ? [{
        type: "line" as const,
        name: upperName,
        data: upperData,
        symbol: "none",
        lineStyle: { color: BOLL_BAND_COLOR, width: 1, type: "dashed" as const, opacity: 0.7 },
        z: 4,
      }] : []),
      // Lower band: dashed line.
      ...(showBoll ? [{
        type: "line" as const,
        name: lowerName,
        data: lowerData,
        symbol: "none",
        lineStyle: { color: BOLL_BAND_COLOR, width: 1, type: "dashed" as const, opacity: 0.7 },
        z: 4,
      }] : []),
      // Stack base for the band fill = Lower (invisible line + invisible area).
      ...(showBoll ? [{
        type: "line" as const,
        name: "_bollBase",
        data: bollBase,
        stack: "bollBand",
        symbol: "none",
        lineStyle: { opacity: 0 },
        areaStyle: { opacity: 0 },
        z: 0,
      }] : []),
      // Stack delta = Upper - Lower = 2k×σ (invisible line, visible area fill).
      // The fill opacity is very low so the green/red gap fill stays prominent.
      ...(showBoll ? [{
        type: "line" as const,
        name: "_bollDelta",
        data: bollDelta,
        stack: "bollBand",
        symbol: "none",
        lineStyle: { opacity: 0 },
        areaStyle: { color: BOLL_BAND_FILL, opacity: 0.08 },
        z: 0,
      }] : []),
    ],
  };
}
