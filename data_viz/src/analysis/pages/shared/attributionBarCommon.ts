/**
 * Shared logic for attribution bar charts — used by both:
 *   • PerfAttr's fluctuationOption (benchmarks as bars for a selected code)
 *   • Industry Sentiment's industryBenchmarkAttributionOption (benchmarks as
 *     bars for a selected industry)
 *
 * Both charts share the same visual layout:
 *   • Bar 1 (left  Y-axis): contribution = return × (shared_weight / 100)
 *   • Bar 2 (right Y-axis): shared_weight (%)
 *   • Broad-market benchmarks dimmed (low opacity) + All/Sector toggle
 *   • Sorted by effective contribution (positive first, then negative)
 *
 * This module provides the generic sorting, contribution calculation, label
 * formatting, and axis setup so the two option builders stay consistent.
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import {
  UP_COLOR,
  DOWN_COLOR,
  SUBTITLE_COLOR,
  axisColors,
  commonLegend,
  commonGrid,
} from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";

// ----------------------------------------------------------------------------
//  Generic row interface — both option builders map their data to this shape.
// ----------------------------------------------------------------------------
export interface AttributionBarRow {
  /** Display label (benchmark name or industry label). */
  label: string;
  /** Code (benchmark_code or industry_id). */
  code: string;
  /** Fractional daily return (e.g. 0.0125 = +1.25%). May be null. */
  benchmarkReturn: number | null;
  /** Shared weight in % (overlap fraction × 100). May be null. */
  sharedWeight: number | null;
  /** True if this row is a broad-market benchmark. */
  isBroadMarket: boolean;
}

export interface AttributionBarContext {
  /** The code of the selected/highlighted row (for emphasis). Null = none. */
  selectedCode: string | null;
  /** Theme mode for colors. */
  themeMode: ThemeMode;
  /** When false, broad-market rows are filtered out entirely. */
  showBroadMarket: boolean;
}

// ----------------------------------------------------------------------------
//  Shared computations
// ----------------------------------------------------------------------------

/**
 * Sort rows by effective contribution (return × shared_weight / 100).
 * Positive contributions first (descending), then negative (descending).
 * Rows with null return or null shared weight are dropped.
 */
export function sortAndFilterRows(
  rows: AttributionBarRow[],
  ctx: AttributionBarContext,
): AttributionBarRow[] {
  let sorted = [...rows].sort((a, b) => {
    const ar = a.benchmarkReturn ?? 0;
    const br = b.benchmarkReturn ?? 0;
    const aw = a.sharedWeight ?? 0;
    const bw = b.sharedWeight ?? 0;
    const aeff = ar * (aw / 100);
    const beff = br * (bw / 100);
    if (aeff >= 0 && beff < 0) return -1;
    if (aeff < 0 && beff >= 0) return 1;
    return beff - aeff;
  });

  if (!ctx.showBroadMarket) {
    sorted = sorted.filter((r) => !r.isBroadMarket);
  }

  return sorted.filter(
    (r) => r.sharedWeight != null && r.benchmarkReturn != null,
  );
}

/**
 * Compute contribution = fractional_return × (shared_weight / 100).
 */
export function computeContributions(
  rows: AttributionBarRow[],
): Array<number | null> {
  return rows.map((r) => {
    if (r.benchmarkReturn == null || r.sharedWeight == null) return null;
    return r.benchmarkReturn * (r.sharedWeight / 100);
  });
}

/**
 * Format a fractional value as a signed percentage string.
 */
export function fmtPctSigned(v: number | null, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return (v >= 0 ? "+" : "") + fmtNum(v * 100, digits) + "%";
}

/** Minimum |contribution| / max|contribution| ratio for label visibility. */
const LABEL_MIN_RATIO = 0.08;

/**
 * Returns true if the contribution label should be visible (above a minimum
 * threshold relative to the max absolute contribution).
 */
export function contribLabelVisible(
  val: number | null,
  maxAbsContrib: number,
): boolean {
  if (val == null || maxAbsContrib === 0) return false;
  return Math.abs(val) / maxAbsContrib >= LABEL_MIN_RATIO;
}

/**
 * Color for a contribution bar — green if return >= 0, red if negative.
 */
export function returnColor(
  ret: number | null,
  axisLineColor: string,
): string {
  return ret == null ? axisLineColor : ret >= 0 ? UP_COLOR : DOWN_COLOR;
}

// ----------------------------------------------------------------------------
//  Shared ECharts fragments
// ----------------------------------------------------------------------------

/**
 * Build the shared xAxis config for an attribution bar chart.
 * Uses `rich` text formatting to dim broad-market labels and highlight the
 * selected row.
 */
export function buildXAxis(
  labels: string[],
  codes: string[],
  broadFlags: boolean[],
  ctx: AttributionBarContext,
) {
  const c = axisColors(ctx.themeMode);
  return {
    type: "category" as const,
    data: labels,
    axisLine: { lineStyle: { color: c.axisLineColor } },
    axisLabel: {
      color: c.textColor,
      fontSize: 8,
      interval: 0,
      rotate: 55,
      formatter: (_v: string, i: number) => {
        const lbl = labels[i].length > 6 ? labels[i].slice(0, 5) + "…" : labels[i];
        if (codes[i] === ctx.selectedCode) return `{sel|${lbl}}`;
        return broadFlags[i] ? `{light|${lbl}}` : lbl;
      },
      rich: {
        light: { color: SUBTITLE_COLOR, fontSize: 8 },
        sel: { color: UP_COLOR, fontSize: 8, fontWeight: 700 },
      },
    },
    splitLine: { show: false },
  };
}

/**
 * Build the shared dual yAxis config:
 *   Axis 0 (left):  Contribution (fractional, formatted as signed %)
 *   Axis 1 (right): Shared Wt (%)
 */
export function buildYAxes(themeMode: ThemeMode) {
  const c = axisColors(themeMode);
  return [
    {
      type: "value" as const,
      name: "Contribution",
      nameTextStyle: { color: c.textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        formatter: (v: number) => fmtPctSigned(v, 2),
      },
      splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed" as const, opacity: 0.4 } },
    },
    {
      type: "value" as const,
      name: "Shared Wt %",
      nameTextStyle: { color: c.textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        formatter: (v: number) => fmtNum(v, 0) + "%",
      },
      splitLine: { show: false },
    },
  ];
}

/**
 * Build the shared base option (backgroundColor, animation, grid, tooltip
 * trigger, legend). The caller adds the `series` and tooltip `formatter`.
 */
export function buildBaseOption(
  themeMode: ThemeMode,
  legendData: string[],
): Pick<EChartsOption, "backgroundColor" | "animation" | "grid" | "legend"> {
  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 64, right: 64, bottom: 96 }),
    legend: commonLegend(themeMode, { itemWidth: 12, itemHeight: 7, data: legendData }),
  };
}

/**
 * Build the Contribution bar series data items with broad-market dimming
 * and selected-row highlighting.
 */
export function buildContributionBarData(
  rows: AttributionBarRow[],
  contribs: Array<number | null>,
  maxAbsContrib: number,
  ctx: AttributionBarContext,
) {
  const c = axisColors(ctx.themeMode);
  return contribs.map((v, i) => {
    const visible = contribLabelVisible(v, maxAbsContrib);
    const raw = rows[i].benchmarkReturn;
    const rawStr =
      raw == null ? "" : `  [${raw >= 0 ? "▲" : "▼"}${fmtNum(raw * 100, 2)}%]`;
    const lblText = visible && v != null ? fmtPctSigned(v, 2) + rawStr : "";
    const broad = rows[i].isBroadMarket;
    const isSelected = rows[i].code === ctx.selectedCode;
    return {
      value: v,
      benchmarkCode: rows[i].code,
      itemStyle: {
        color: returnColor(raw, c.axisLineColor),
        opacity: isSelected ? 1.0 : broad ? 0.4 : 0.85,
      },
      label: {
        show: visible,
        position: (v != null && v >= 0 ? "insideTop" : "insideBottom") as "insideTop" | "insideBottom",
        distance: 2,
        color: isSelected ? UP_COLOR : broad ? SUBTITLE_COLOR : c.textColor,
        fontSize: 8,
        fontWeight: isSelected ? 700 : 600,
        formatter: () => lblText,
      },
    };
  });
}

/**
 * Build a shared-weight bar series data items with broad-market dimming.
 */
export function buildSharedWtBarData(
  rows: AttributionBarRow[],
  weights: Array<number | null>,
  ctx: AttributionBarContext,
  color: string,
) {
  const c = axisColors(ctx.themeMode);
  return weights.map((v, i) => {
    const broad = rows[i].isBroadMarket;
    const isSelected = rows[i].code === ctx.selectedCode;
    return {
      value: v,
      benchmarkCode: rows[i].code,
      itemStyle: {
        color,
        opacity: isSelected ? 0.85 : broad ? 0.28 : 0.6,
      },
      label: {
        show: !(v == null || v < 1.5),
        position: "insideTop" as const,
        distance: 2,
        color: broad ? SUBTITLE_COLOR : c.textColor,
        fontSize: 8,
        formatter: () => (v == null || v < 1.5 ? "" : fmtNum(v, 1) + "%"),
      },
    };
  });
}
