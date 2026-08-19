/**
 * ECharts option builder for the Market Movements page.
 *
 * Three plots:
 *   1. Top: benchmark_price_pct line + per-industry SHADED AREAS
 *      (industry_price_pct). Industry shades use DIRECTIONAL coloring
 *      (UP_COLOR green above benchmark, DOWN_COLOR red below) mirroring
 *      the Hypes & Drains sub-view of Market Trend — NOT per-industry
 *      colors. Shade is centered about the BENCHMARK line (not a 0-
 *      baseline area): green shade fills benchmark→industry when
 *      industry > benchmark; red shade fills industry→benchmark when
 *      industry < benchmark. Each industry is rendered as 3 stacked
 *      line series (base + pos + neg) with lineStyle opacity 0 so the
 *      shade has no curve boundary. The shared layered-shade builder
 *      lives in @/lib/benchmark-shade (also used by Hypes & Drains).
 *      Tooltip shows industry_price_pct (raw %) + the precomputed diff
 *      (industry_price_pct_vs_benchmark) from the DB. Click → select tick.
 *   2. Middle: bar chart of industry_price_pct at the selected tick,
 *      sorted by signed value. Green = positive, red = negative.
 *      Click bar → select industry.
 *   3. Bottom: bar chart of code_price_pct for the selected industry's
 *      member indices at the selected tick, sorted by signed value.
 */
import type { EChartsOption, SeriesOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import {
  axisColors,
  commonLegend,
  commonGrid,
  UP_COLOR,
  DOWN_COLOR,
  PALETTE_HI,
} from "@/theme/chart-palette";
import { buildBenchmarkCenteredShadeSeries } from "@/lib/benchmark-shade";
import {
  buildOhlcBarSeries,
  prevDayOhlcSeriesName,
  prevDayTickLabel,
  type PrevDayOhlcBar,
} from "./prevDayOhlc";
import { renderTooltip, renderIndustryBarTooltip, renderMemberBarTooltip } from "./tooltips";
import type {
  IntradayMovementsResponse,
} from "@shared/types";

/** Filter mode for the Intraday Attribution middle/bottom plots.
 *  - "all"      — show every industry (both real industries and strategy themes)
 *  - "industry" — show only real industries (is_strategy === false)
 *  - "strategy" — show only strategy themes (is_strategy === true) */
export type IndustryFilter = "all" | "industry" | "strategy";

// ============================================================================
//  Full trading-day tick generation — produces every 5-min tick from
//  9:30 to 15:30 (spanning morning + afternoon sessions with the lunch
//  break between). Used to freeze the x-axis so the chart always shows
//  the full day range, even when intraday data only covers a partial
//  window (e.g. morning session only).
// ============================================================================
function generateFullTradingDayTicks(): string[] {
  const ticks: string[] = [];
  const morningStart = 9 * 60 + 30;   // 09:30
  const morningEnd = 11 * 60 + 30;    // 11:30
  const afternoonStart = 13 * 60;     // 13:00
  const afternoonEnd = 15 * 60 + 30;  // 15:30
  const fmt = (mins: number): string => {
    const h = Math.floor(mins / 60);
    const m = mins % 60;
    return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:00`;
  };
  for (let t = morningStart; t <= morningEnd; t += 5) ticks.push(fmt(t));
  for (let t = afternoonStart; t <= afternoonEnd; t += 5) ticks.push(fmt(t));
  return ticks;
}

/** Full set of 5-min ticks for the trading day (9:30–15:30).
 *  Exported so consumers (e.g. the click handler in the page component)
 *  can map ECharts category indices back to tick time strings. */
export const FULL_DAY_TICKS = generateFullTradingDayTicks();

// ============================================================================
//  TOP PLOT — benchmark % line + per-industry directional shaded areas
// ============================================================================
export function buildMarketMovementsTopOption(
  data: IntradayMovementsResponse,
  selectedTick: string,
  themeMode: ThemeMode,
  noBenchmark: boolean = false,
  prevDayBar: PrevDayOhlcBar | null = null,
  selectedIndustryId: string | null = null,
  selectedMemberCode: string | null = null,
): EChartsOption {
  const c = axisColors(themeMode);

  // Freeze x-axis to the full trading-day tick range (9:30–15:30).
  // Map actual data points into this fixed range, using null for ticks
  // that have no data yet (future ticks during market hours, or outside
  // the live session). This ensures the chart always shows the full
  // day range for consistent visual comparison across different times.
  const tickIndexMap = new Map<string, number>();
  FULL_DAY_TICKS.forEach((t, i) => tickIndexMap.set(t, i));

  const times = FULL_DAY_TICKS;

  // Prev-day OHLC bar: when available, PREPEND one extra x category
  // ("MM-DD" of the prev trading day) BEFORE the 09:30 tick and shift
  // every intraday series by `offset` slots (null at the prev slot keeps
  // lines/shades starting at 09:30). When no bar is available the axis
  // is exactly the full-day tick range (offset 0, nothing prepended).
  const offset = prevDayBar ? 1 : 0;
  const xCategories = prevDayBar
    ? [prevDayTickLabel(prevDayBar), ...times]
    : times;
  const pad = (arr: Array<number | null>): Array<number | null> =>
    offset ? [null, ...arr] : arr;

  // No-benchmark mode: flat 0.0% baseline instead of the actual benchmark
  // line. Industry shades are then centered about zero (raw % vs prev close).
  // Build the benchmark time→pct lookup map once (shared by both the
  // benchPct array builder and the directional-color logic for overlays).
  const benchByTime = new Map<string, number | null>();
  for (const b of data.benchmark_series) {
    benchByTime.set(b.time, b.benchmark_price_pct);
  }
  const benchPct: Array<number | null> = pad(
    noBenchmark
      ? times.map(() => 0.0)
      : times.map((t) => benchByTime.get(t) ?? null),
  );

  // Benchmark line color — dark green when the last tick is above prev-day
  // close (benchmark_price_pct > 0), dark red when at or below. In no-
  // benchmark mode (flat 0.0% baseline) or when no data exists, fall back
  // to the original PALETTE_HI blue so the line stays visible.
  const lastBenchPct = (() => {
    for (let i = benchPct.length - 1; i >= 0; i--) {
      if (benchPct[i] != null) return benchPct[i] as number;
    }
    return null;
  })();
  const benchmarkLineColor =
    noBenchmark || lastBenchPct == null
      ? PALETTE_HI
      : lastBenchPct > 0
        ? UP_COLOR
        : DOWN_COLOR;

  // Index of the selected tick (for the markLine) within the FULL day range.
  const selectedIdx =
    offset + Math.max(0, tickIndexMap.get(selectedTick) ?? 0);

  // Benchmark line (always opaque, prominent). In no-benchmark mode this
  // is a flat 0.0% reference line.
  const benchmarkLabel = noBenchmark
    ? "No Benchmark (0.0%)"
    : `${data.benchmark_name} (${data.benchmark_code})`;
  const benchmarkSeries = {
    name: benchmarkLabel,
    type: "line" as const,
    data: benchPct,
    showSymbol: false,
    smooth: false,
    connectNulls: noBenchmark ? false : true,
    lineStyle: { width: 2.5, color: benchmarkLineColor },
    itemStyle: { color: benchmarkLineColor },
    z: 10,
    markLine: {
      symbol: "none",
      silent: true,
      label: { show: false },
      lineStyle: { color: c.axisLineColor, type: "dashed", width: 1.5 },
      data: [{ xAxis: selectedIdx }],
    },
  };

  // Per-industry directional shaded areas, built via the SHARED layered-shade
  // helper (same builder used by Hypes & Drains). The shade is centered about
  // the BENCHMARK line (not a 0-baseline area): green fills benchmark→industry
  // when industry > benchmark; red fills industry→benchmark when below. Each
  // industry is 3 stacked line series (base + pos + neg) with lineStyle
  // opacity 0 — no curve boundary, only the area shade. The benchmark line
  // is the only solid visible edge.
  //   - ALL industries shaded ALL the time (no ACTIVE/FADING/HIDDEN mask).
  //   - Opacity 0.15: light enough that ~30 overlapping industries don't
  //     turn the plot opaque (Hypes & Drains uses 0.35 because only the
  //     top/bottom 3-5 ACTIVE industries are shaded at any time).
  //   - NO smooth: smooth interpolation on stacked areas breaks the stack
  //     boundary (the smoothed curve dips below base, causing the area fill
  //     to visually start from 0% instead of from the benchmark line).
  const shadeIndustries = data.industries.map((ind) => {
    const byTime = new Map<string, number | null>();
    for (const r of data.industry_series) {
      if (r.industry_id === ind.industry_id) {
        byTime.set(r.time, r.industry_price_pct);
      }
    }
    return {
      id: ind.industry_id,
      label: ind.industry_label,
      values: pad(times.map((t) => byTime.get(t) ?? null)),
    };
  });
  const { series: industrySeries, legendLabels: legendIndustryLabels } =
    buildBenchmarkCenteredShadeSeries(benchPct, shadeIndustries, {
      shadeOpacity: 0.15,
      stackPrefix: "mmShade",
      zBase: 4,
    });

  // Selected industry / member index intraday curve — rendered as a
  // prominent solid line on the top plot alongside the benchmark. When a
  // member index is clicked the INDEX curve takes priority (more specific);
  // when only an industry is clicked the INDUSTRY aggregate curve is shown.
  // Color is directional: green when above the benchmark, red when at or
  // below — consistent with the industry shade coloring convention.
  const extraSeries: SeriesOption[] = [];
  const extraLegendLabels: string[] = [];

  // Build member-series lookup: code → { pctByTime, diffByTime }.
  const memberLookup = new Map<string, {
    name: string;
    pctByTime: Map<string, number | null>;
    diffByTime: Map<string, number | null>;
  }>();
  for (const m of data.member_series) {
    if (!memberLookup.has(m.code)) {
      memberLookup.set(m.code, {
        name: m.code_name || m.code,
        pctByTime: new Map(),
        diffByTime: new Map(),
      });
    }
    const entry = memberLookup.get(m.code)!;
    entry.pctByTime.set(m.time, m.code_price_pct);
    const bv = benchByTime.get(m.time);
    if (m.code_price_pct != null && bv != null) {
      entry.diffByTime.set(m.time, m.code_price_pct - bv);
    }
  }

  // Build industry-series lookup: industry_id → industry tick rows (by time).
  const industryById = new Map(data.industries.map((i) => [i.industry_id, i]));
  const industryPctById = new Map<string, Map<string, number | null>>();
  for (const r of data.industry_series) {
    let byTime = industryPctById.get(r.industry_id);
    if (!byTime) {
      byTime = new Map<string, number | null>();
      industryPctById.set(r.industry_id, byTime);
    }
    byTime.set(r.time, r.industry_price_pct);
  }

  // Determine directional color by comparing last valid curve point with
  // the benchmark at the same tick. Green = above benchmark, red = below.
  const directionalColor = (curveByTime: Map<string, number | null>): string => {
    // Walk ticks in reverse order to find the last tick where both
    // curve and benchmark have data.
    for (let i = times.length - 1; i >= 0; i--) {
      const t = times[i];
      const cv = curveByTime.get(t);
      const bv = benchByTime.get(t);
      if (cv != null && bv != null) {
        return cv > bv ? UP_COLOR : DOWN_COLOR;
      }
    }
    return PALETTE_HI; // fallback when no overlapping data
  };

  if (selectedMemberCode) {
    const entry = memberLookup.get(selectedMemberCode);
    if (entry) {
      const color = directionalColor(entry.pctByTime);
      const label = `${entry.name} (${selectedMemberCode})`;
      extraSeries.push({
        name: label,
        type: "line" as const,
        data: pad(times.map((t) => entry.pctByTime.get(t) ?? null)),
        showSymbol: false,
        smooth: false,
        connectNulls: true,
        lineStyle: { width: 2, color, type: "solid" as const },
        itemStyle: { color },
        z: 9,
      });
      extraLegendLabels.push(label);
    }
  } else if (selectedIndustryId) {
    const ind = industryById.get(selectedIndustryId);
    const byTime = industryPctById.get(selectedIndustryId);
    if (ind && byTime) {
      const color = directionalColor(byTime);
      const label = ind.industry_label;
      extraSeries.push({
        name: label,
        type: "line" as const,
        data: pad(times.map((t) => byTime.get(t) ?? null)),
        showSymbol: false,
        smooth: false,
        connectNulls: true,
        lineStyle: { width: 2, color, type: "dashed" as const },
        itemStyle: { color },
        z: 9,
      });
      extraLegendLabels.push(label);
    }
  }

  // Per-industry lookup for the tooltip: industry_id → (label + per-time pct
  // + precomputed diff vs benchmark). Pre-pivoted once so the tooltip
  // formatter stays O(1) per industry per render. The diff comes straight
  // from the DB column (industry_price_pct_vs_benchmark_price_pct) — no
  // client-side subtraction.
  const indPctByTime = new Map<
    string,
    { label: string; pctByTime: Map<string, number | null>; diffByTime: Map<string, number | null> }
  >();
  for (const ind of data.industries) {
    const pctByTime = new Map<string, number | null>();
    const diffByTime = new Map<string, number | null>();
    for (const r of data.industry_series) {
      if (r.industry_id === ind.industry_id) {
        pctByTime.set(r.time, r.industry_price_pct);
        diffByTime.set(r.time, r.industry_price_pct_vs_benchmark);
      }
    }
    indPctByTime.set(ind.industry_id, { label: ind.industry_label, pctByTime, diffByTime });
  }

  return {
    backgroundColor: "transparent",
    legend: {
      ...commonLegend(themeMode),
      data: legendIndustryLabels.concat([
        benchmarkLabel,
        ...extraLegendLabels,
        ...(prevDayBar ? [prevDayOhlcSeriesName(prevDayBar.label)] : []),
      ]),
    },
    grid: commonGrid({ top: 40, bottom: 50, left: 60, right: 60 }),
    tooltip: {
      trigger: "axis",
      backgroundColor: c.tooltipBg,
      borderColor: c.axisLineColor,
      textStyle: { color: c.textColor, fontSize: 11 },
      axisPointer: {
        type: "cross",
        crossStyle: { color: c.axisLineColor },
      },
      // Custom formatter:
      //   - Prev-day tick (idx 0, only when a prev-day bar is shown): the
      //     prev-day OHLC tooltip (O/H/L/C fractions rebased to the prev
      //     day's OPEN — O = 0.00% by definition; tooltip-only, the bar
      //     itself keeps the plot's close-based y-axis). No industry rows.
      //   - Normal mode: shows the benchmark % at the top, then ONLY the
      //     top 5 + bottom 5 industries ranked by diff vs benchmark
      //     (similar to the Hypes & Drains / Benchmark Price tooltip which
      //     focuses on the extremes). Industries with no data at this tick
      //     are skipped.
      //     Main value: industry_price_pct (raw % vs prev close).
      //     Diff shown in parentheses: industry_price_pct_vs_benchmark.
      //   - No-benchmark mode: shows all industries sorted by raw %
      //     (descending), with green for positive and red for negative.
      //     No diff column (no benchmark to compare against).
      formatter: (params: unknown) => {
        return renderTooltip({
          params,
          times,
          offset,
          benchPct,
          noBenchmark,
          benchmarkLabel,
          data,
          prevDayBar,
          selectedMemberCode,
          selectedIndustryId,
          memberLookup,
          indPctByTime,
        });
      },
    },
    xAxis: {
      type: "category",
      data: xCategories,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 10,
        // Show a label every 30 min (at :00 and :30 minutes) + ALWAYS the
        // prepended prev-day tick (idx 0, "MM-DD"). Times from the API are
        // "HH:MM:SS" — slice the minutes field (chars 3-5) rather than
        // using endsWith, because endsWith(":00") would match the SECONDS
        // field (always "00") and show every 5-min label. When the
        // prev-day category is prepended, the 09:30 label (idx 1) is
        // SKIPPED — it sits one narrow category away from "MM-DD" and the
        // two centered labels collide into an unreadable smudge.
        interval: (idx: number) => {
          if (offset > 0 && idx === 0) return true;
          if (offset > 0 && idx === 1) return false;
          const t = times[idx - offset];
          if (!t) return false;
          const mm = t.slice(3, 5);
          return mm === "00" || mm === "30";
        },
      },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      scale: true,
      name: "% change vs prev close",
      nameTextStyle: { color: c.textColor, fontSize: 10 },
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 10,
        formatter: (v: number) => (v * 100).toFixed(2) + "%",
      },
      splitLine: { lineStyle: { color: c.splitLineColor } },
    },
    series: [
      // Prev-day OHLC bar at the prepended category 0 (before 09:30).
      ...(prevDayBar ? [buildOhlcBarSeries(prevDayBar)] : []),
      benchmarkSeries,
      // Extra overlay: selected member index (solid) or industry (dashed).
      ...extraSeries,
      // benchmark-shade returns a series ARRAY typed as the loose
      // SeriesOption | SeriesOption[] union — cast to the array form.
      ...(industrySeries as unknown as SeriesOption[]),
    ],
  } as EChartsOption;
}

// ============================================================================
//  MIDDLE PLOT — industry bar chart at selected tick (ALL industries,
//  filtered by the industry/strategy toggle).
// ============================================================================
export function buildIndustryBarsOption(
  data: IntradayMovementsResponse,
  selectedTick: string,
  themeMode: ThemeMode,
  filter: IndustryFilter = "all",
): EChartsOption {
  const c = axisColors(themeMode);

  // Build a lookup of industry_id → is_strategy from the industries list.
  const industryStrategyMap = new Map<string, boolean>();
  for (const ind of data.industries) {
    industryStrategyMap.set(ind.industry_id, ind.is_strategy);
  }

  // Filter industry_series to the selected tick AND by the filter toggle,
  // then sort by value descending.
  const rowsAtTick = data.industry_series.filter((r) => {
    if (r.time !== selectedTick || r.industry_price_pct == null) return false;
    if (filter === "all") return true;
    const isStrategy = industryStrategyMap.get(r.industry_id) ?? false;
    return filter === "strategy" ? isStrategy : !isStrategy;
  });
  const sorted = [...rowsAtTick].sort(
    (a, b) => (b.industry_price_pct ?? -Infinity) - (a.industry_price_pct ?? -Infinity),
  );

  const labels = sorted.map((r) => r.industry_label);
  const values = sorted.map((r) => r.industry_price_pct);
  const colors = values.map((v) => (v != null && v >= 0 ? UP_COLOR : DOWN_COLOR));

  return {
    backgroundColor: "transparent",
    grid: commonGrid({ top: 32, bottom: 80, left: 60, right: 30 }),
    tooltip: {
      trigger: "item",
      backgroundColor: c.tooltipBg,
      borderColor: c.axisLineColor,
      textStyle: { color: c.textColor, fontSize: 11 },
      formatter: (p: unknown) => {
        const param = p as { dataIndex: number };
        const r = sorted[param.dataIndex];
        if (!r) return "";
        return renderIndustryBarTooltip(r.industry_label, r.industry_price_pct);
      },
    },
    xAxis: {
      type: "category",
      data: labels,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        interval: 0,
        rotate: 45,
      },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      name: "% change",
      nameTextStyle: { color: c.textColor, fontSize: 10 },
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 10,
        formatter: (v: number) => (v * 100).toFixed(2) + "%",
      },
      splitLine: { lineStyle: { color: c.splitLineColor } },
    },
    series: [
      {
        name: "Industry %",
        type: "bar",
        data: values.map((v, i) => ({
          value: v,
          industry_id: sorted[i].industry_id,
          itemStyle: { color: colors[i] },
        })),
        barMaxWidth: 24,
        label: {
          show: true,
          position: "top",
          color: c.textColor,
          fontSize: 8,
          formatter: (p: unknown) => {
            const param = p as { dataIndex: number };
            const v = values[param.dataIndex];
            return v != null ? (v * 100).toFixed(2) + "%" : "";
          },
        },
      },
    ],
  } as EChartsOption;
}

// ============================================================================
//  BOTTOM PLOT — member index bar chart for clicked industry at selected tick
// ============================================================================
export function buildMemberBarsOption(
  data: IntradayMovementsResponse,
  selectedTick: string,
  selectedIndustryId: string,
  themeMode: ThemeMode,
): EChartsOption {
  const c = axisColors(themeMode);

  // Filter member_series to (selected tick + selected industry), sort desc.
  // The bottom plot stays empty until an industry bar is clicked — no
  // all-industries fallback.
  const rowsAtTick = data.member_series.filter(
    (r) =>
      r.time === selectedTick &&
      r.code_price_pct != null &&
      r.industry_id === selectedIndustryId,
  );
  const sorted = [...rowsAtTick].sort(
    (a, b) => (b.code_price_pct ?? -Infinity) - (a.code_price_pct ?? -Infinity),
  );

  const labels = sorted.map((r) => r.code_name || r.code);
  const values = sorted.map((r) => r.code_price_pct);
  const colors = values.map((v) => (v != null && v >= 0 ? UP_COLOR : DOWN_COLOR));

  return {
    backgroundColor: "transparent",
    grid: commonGrid({ top: 32, bottom: 80, left: 60, right: 30 }),
    tooltip: {
      trigger: "item",
      backgroundColor: c.tooltipBg,
      borderColor: c.axisLineColor,
      textStyle: { color: c.textColor, fontSize: 11 },
      formatter: (p: unknown) => {
        const param = p as { dataIndex: number };
        const r = sorted[param.dataIndex];
        if (!r) return "";
        return renderMemberBarTooltip(r.code_name || r.code, r.code, r.code_price_pct);
      },
    },
    xAxis: {
      type: "category",
      data: labels,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        interval: 0,
        rotate: 45,
      },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      name: "% change",
      nameTextStyle: { color: c.textColor, fontSize: 10 },
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 10,
        formatter: (v: number) => (v * 100).toFixed(2) + "%",
      },
      splitLine: { lineStyle: { color: c.splitLineColor } },
    },
    series: [
      {
        name: "Member %",
        type: "bar",
        data: values.map((v, i) => ({
          value: v,
          code: sorted[i].code,
          itemStyle: { color: colors[i] },
        })),
        barMaxWidth: 24,
        label: {
          show: true,
          position: "top",
          color: c.textColor,
          fontSize: 8,
          formatter: (p: unknown) => {
            const param = p as { dataIndex: number };
            const v = values[param.dataIndex];
            return v != null ? (v * 100).toFixed(2) + "%" : "";
          },
        },
      },
    ],
  } as EChartsOption;
}
