/**
 * Roof/floor LEVEL-ESTIMATE overlay — the quantitative per-method levels
 * ("rfest" extras group, analysis.mov_ave_spreads_roof_floor_estimates):
 * for the CHOSEN date (the clicked anchor date, latest by default) and
 * the enabled roof/floor window, the selected method's estimated roof /
 * floor levels drawn as TWO HORIZONTAL dashed lines spanning the chart.
 *
 * The estimate rows are index-aligned with the chart rows (the API pivots
 * the LONG table per date off the detail rows' dates), so the chosen
 * date's levels are a direct row read — no client-side math beyond the
 * field pick. Values are RAW price units; in percentage ohlcMode the
 * axis relabels them (same as every other price line on the chart), and
 * the markLine labels format through the chart's fmtPrice.
 *
 * The row's `breached_{method}_{W}d` flag (the table's
 * is_recently_breached: ALL of the last 5 trading days closed beyond the
 * d−5 line) switches both lines + labels to their LIGHT color variants —
 * an overrun level de-emphasizes until it is a level again.
 */
import type { MovAveSpreadRoofFloorEstimateRow, RoofFloorEstimateMethod } from "@shared/types";
import { ROOF_COLOR, FLOOR_COLOR, ROOF_COLOR_LIGHT, FLOOR_COLOR_LIGHT } from "./colors";

/** markLine block attached to the long-MA series (merged with the roof/
 *  floor anchor mark line by buildPairOption — one markLine per series). */
export interface EstimateMarkLine {
  markLine: {
    silent: true;
    symbol: [string, string];
    label: { show: boolean };
    lineStyle: { type: string; color?: string; width: number; opacity: number };
    data: Array<Record<string, unknown>>;
  };
}

export interface RoofFloorEstimateState {
  /** True when a roof/floor window is enabled, a method is picked, and
   *  the estimate rows are index-aligned with the chart rows. */
  active: boolean;
  method: RoofFloorEstimateMethod | null;
  /** The chart-row index the estimates are read at (the clicked anchor
   *  date; the latest row when no date was clicked). */
  dateIdx: number | null;
  roofVal: number | null;
  floorVal: number | null;
  /** The chosen row's is_recently_breached flag — ALL of the last 5
   *  trading days closed beyond the d−5 line (the est lines draw faded). */
  breached: boolean;
  markLine: EstimateMarkLine | null;
}

export interface RoofFloorEstimateBuildArgs {
  n: number;
  /** Enabled rolling-OHLC window (trading days) — null = section off. */
  roofFloorWindow: number | null;
  /** Index into the chart rows of the clicked chart date (null = latest). */
  roofFloorClickIdx: number | null;
  /** Estimate rows (index-aligned with the chart rows; null = group off
   *  or not yet loaded). */
  estRows: MovAveSpreadRoofFloorEstimateRow[] | null;
  /** The picked estimate method — null = the Est-levels toggle is off. */
  estMethod: RoofFloorEstimateMethod | null;
  /** % mode-aware value formatter for the markLine labels. */
  fmtValue: (v: number | null | undefined) => string;
}

export function buildRoofFloorEstimateState(
  args: RoofFloorEstimateBuildArgs,
): RoofFloorEstimateState {
  const { n, roofFloorWindow, roofFloorClickIdx, estRows, estMethod } = args;
  const aligned =
    roofFloorWindow != null && estMethod != null
    && estRows != null && estRows.length === n;
  if (!aligned || roofFloorWindow == null || estMethod == null || estRows == null) {
    return {
      active: false,
      method: estMethod,
      dateIdx: null,
      roofVal: null,
      floorVal: null,
      breached: false,
      markLine: null,
    };
  }

  const dateIdx =
    roofFloorClickIdx != null && roofFloorClickIdx >= 0 && roofFloorClickIdx < n
      ? roofFloorClickIdx
      : n - 1;
  const row = estRows[dateIdx];
  const roofVal = row?.[`roof_${estMethod}_${roofFloorWindow}d`] ?? null;
  const floorVal = row?.[`floor_${estMethod}_${roofFloorWindow}d`] ?? null;
  const breached = row?.[`breached_${estMethod}_${roofFloorWindow}d`] ?? false;

  // Two horizontal lines (either may be absent — e.g. the trendline
  // method without a determined line at that date); each carries its own
  // lineStyle + value label so both can share ONE markLine block. A
  // recently-breached estimate draws in its LIGHT color — the level has
  // been overrun on every one of the last 5 days, so it fades until it
  // is a level again.
  const roofColor = breached ? ROOF_COLOR_LIGHT : ROOF_COLOR;
  const floorColor = breached ? FLOOR_COLOR_LIGHT : FLOOR_COLOR;
  const data: Array<Record<string, unknown>> = [];
  if (roofVal != null) {
    data.push({
      yAxis: roofVal,
      lineStyle: { type: "dashed", color: roofColor, width: 1.4, opacity: 0.9 },
      label: {
        show: true,
        position: "insideEndTop",
        color: roofColor,
        fontSize: 9,
        formatter: `Est roof ${args.fmtValue(roofVal)}`,
      },
    });
  }
  if (floorVal != null) {
    data.push({
      yAxis: floorVal,
      lineStyle: { type: "dashed", color: floorColor, width: 1.4, opacity: 0.9 },
      label: {
        show: true,
        position: "insideEndBottom",
        color: floorColor,
        fontSize: 9,
        formatter: `Est floor ${args.fmtValue(floorVal)}`,
      },
    });
  }

  return {
    active: true,
    method: estMethod,
    dateIdx,
    roofVal,
    floorVal,
    breached,
    markLine: data.length > 0
      ? {
          markLine: {
            silent: true,
            symbol: ["none", "none"],
            label: { show: false },
            lineStyle: { type: "dashed", width: 1.4, opacity: 0.9 },
            data,
          },
        }
      : null,
  };
}
