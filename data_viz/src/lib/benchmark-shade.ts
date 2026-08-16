/**
 * Shared helper for building BENCHMARK-CENTERED DIRECTIONAL SHADES — the
 * layered (base + pos + neg) stacked-area pattern used by both:
 *
 *   • Market Movements (Live Data, top plot) — per-5-min-tick %
 *     change vs benchmark, ALL industries shaded ALL the time.
 *   • Industry Sentiments → Market Trend → Hypes & Drains — daily
 *     rebased-to-100 curves, only ACTIVE industries shaded (FADING /
 *     HIDDEN get null via the visible mask).
 *
 * PLOT STYLE (single source of truth — both pages share this look):
 *   - Each industry is rendered as 3 STACKED line series:
 *       base = min(bench, ind)        — lifts the stack to the lower edge
 *       pos  = max(0, ind - bench)    — green shade height when ind > bench
 *       neg  = max(0, bench - ind)    — red shade height when ind < bench
 *   - lineStyle opacity 0 → NO curve boundary, only the area shade
 *     (the benchmark line is the only solid visible edge).
 *   - NO smooth: smooth interpolation on stacked areas breaks the stack
 *     boundary (the smoothed curve dips below base, causing the area fill
 *     to visually start from 0 instead of from the benchmark line).
 *   - The shade is CENTERED ABOUT THE BENCHMARK line — NOT a 0-baseline
 *     area. Green shade fills the gap from benchmark UP to industry curve
 *     (outperforming); red shade fills the gap from industry curve DOWN
 *     to benchmark (underperforming).
 *
 * Per-tick NULL handling:
 *   - If either benchmark[i] or industry.values[i] is NULL → no shade at
 *     that tick (base/pos/neg all NULL → gap in the area).
 *   - If the optional visible[i] mask is FALSE → no shade at that tick
 *     (used by the Hypes & Drains ACTIVE/FADING/HIDDEN state machine).
 */
import type { EChartsOption } from "echarts";
import { UP_COLOR, DOWN_COLOR } from "@/theme/chart-palette";

/** One industry's curve to be shaded relative to the benchmark. */
export interface BenchmarkShadeIndustry {
  /** Stable id, used to generate unique stack ids. */
  id: string;
  /** Display label for the legend. */
  label: string;
  /** Per-tick industry values (e.g. industry_price_pct, or rebased-to-100
   *  curve). Aligned to the benchmark array. */
  values: Array<number | null>;
  /** Optional per-tick visibility mask. When provided, only ticks where
   *  visible[i] === true get shaded; others are NULL (no shade). When
   *  omitted, all non-null values are shaded. */
  visible?: Array<boolean> | null;
}

export interface BenchmarkShadeOptions {
  /** Area opacity for the green/red shades. Default 0.15 (light, suitable
   *  for the ALL-industries-always Market Movements view). Hypes & Drains
   *  passes 0.35 because only ACTIVE industries are shaded. */
  shadeOpacity?: number;
  /** Stack-id prefix to avoid collisions with other stacked series in the
   *  same chart. Default "bmShade". */
  stackPrefix?: string;
  /** z-index for the base series (pos/neg are z+1). Default 4. */
  zBase?: number;
}

export interface BenchmarkShadeResult {
  /** ECharts series entries to spread into the option's `series` array. */
  series: NonNullable<EChartsOption["series"]>;
  /** Legend labels (one per industry, in input order). */
  legendLabels: string[];
}

/**
 * Build the layered (base + pos + neg) stacked-area series for one or more
 * industries, centered about the benchmark line.
 *
 * @param benchmarkValues  Per-tick benchmark values (the reference line).
 * @param industries       One entry per industry to shade.
 * @param options          Shade opacity, stack-id prefix, z-base.
 * @returns Series array + legend labels.
 */
export function buildBenchmarkCenteredShadeSeries(
  benchmarkValues: Array<number | null>,
  industries: BenchmarkShadeIndustry[],
  options: BenchmarkShadeOptions = {},
): BenchmarkShadeResult {
  const {
    shadeOpacity = 0.15,
    stackPrefix = "bmShade",
    zBase = 4,
  } = options;

  const n = benchmarkValues.length;
  const series: NonNullable<EChartsOption["series"]> = [];
  const legendLabels: string[] = [];

  for (const ind of industries) {
    const baseData: Array<number | null> = new Array(n).fill(null);
    const posData: Array<number | null> = new Array(n).fill(null);
    const negData: Array<number | null> = new Array(n).fill(null);

    for (let i = 0; i < n; i++) {
      const bv = benchmarkValues[i];
      const iv = ind.values[i];
      if (bv == null || iv == null) continue;
      // Optional visibility mask (Hypes & Drains ACTIVE/FADING/HIDDEN).
      if (ind.visible && !ind.visible[i]) continue;
      const diff = iv - bv;
      baseData[i] = Math.min(bv, iv);
      posData[i] = diff >= 0 ? diff : 0;
      negData[i] = diff >= 0 ? 0 : -diff;
    }

    const stackId = `${stackPrefix}_${ind.id}`;
    legendLabels.push(ind.label);

    // Base (transparent — just lifts the stack to min(bench, ind)).
    // stackStrategy: 'all' is REQUIRED here — without it, ECharts defaults
    // to 'samesign' which stacks positive and negative values separately
    // (positive from 0 up, negative from 0 down). When the benchmark or
    // industry % goes negative (extremely common on red-market days — the
    // current dataset has 36/50 benchmark ticks below zero), the base value
    // is negative and the subsequent pos/neg series would stack from 0
    // instead of from the base, causing shades to render from the 0-baseline
    // instead of from the benchmark line. 'all' makes the stack purely
    // additive regardless of sign, so base→pos and base→neg always stack
    // contiguously and the shade stays centered about the benchmark.
    series.push({
      name: ind.label,
      type: "line",
      data: baseData,
      stack: stackId,
      stackStrategy: "all",
      symbol: "none",
      showSymbol: false,
      lineStyle: { opacity: 0 },
      z: zBase,
      tooltip: { show: false },
    });

    // Green shade — industry ABOVE benchmark (outperforming / HYPE).
    series.push({
      name: ind.label,
      type: "line",
      data: posData,
      stack: stackId,
      stackStrategy: "all",
      symbol: "none",
      showSymbol: false,
      lineStyle: { opacity: 0 },
      areaStyle: { color: UP_COLOR, opacity: shadeOpacity },
      z: zBase + 1,
      tooltip: { show: false },
    });

    // Red shade — industry BELOW benchmark (underperforming / DRAIN).
    series.push({
      name: ind.label,
      type: "line",
      data: negData,
      stack: stackId,
      stackStrategy: "all",
      symbol: "none",
      showSymbol: false,
      lineStyle: { opacity: 0 },
      areaStyle: { color: DOWN_COLOR, opacity: shadeOpacity },
      z: zBase + 1,
      tooltip: { show: false },
    });
  }

  return { series, legendLabels };
}
