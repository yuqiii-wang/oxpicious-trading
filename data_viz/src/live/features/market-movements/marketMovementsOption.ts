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
import type { EChartsOption } from "echarts";
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
import type {
  IntradayMovementsResponse,
  IntradayMovementsIndustryTick,
  IntradayMovementsMemberTick,
} from "../../../../shared/types";

// ============================================================================
//  TOP PLOT — benchmark % line + per-industry directional shaded areas
// ============================================================================
export function buildMarketMovementsTopOption(
  data: IntradayMovementsResponse,
  selectedTick: string,
  themeMode: ThemeMode,
): EChartsOption {
  const c = axisColors(themeMode);
  const times = data.benchmark_series.map((b) => b.time);
  const benchPct = data.benchmark_series.map((b) => b.benchmark_price_pct);

  // Index of the selected tick (for the markLine).
  const selectedIdx = Math.max(0, times.indexOf(selectedTick));

  // Benchmark line (always opaque, prominent).
  const benchmarkSeries = {
    name: `${data.benchmark_name} (${data.benchmark_code})`,
    type: "line" as const,
    data: benchPct,
    showSymbol: false,
    smooth: false,
    lineStyle: { width: 2.5, color: PALETTE_HI },
    itemStyle: { color: PALETTE_HI },
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
      values: times.map((t) => byTime.get(t) ?? null),
    };
  });
  const { series: industrySeries, legendLabels: legendIndustryLabels } =
    buildBenchmarkCenteredShadeSeries(benchPct, shadeIndustries, {
      shadeOpacity: 0.15,
      stackPrefix: "mmShade",
      zBase: 4,
    });

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
        `${data.benchmark_name} (${data.benchmark_code})`,
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
      // Custom formatter: shows the benchmark % at the top, then ONLY the
      // top 5 + bottom 5 industries ranked by diff vs benchmark (similar to
      // the Hypes & Drains / Benchmark Price tooltip which focuses on the
      // extremes). Industries with no data at this tick are skipped.
      //   - Main value shown: industry_price_pct (raw % vs prev close)
      //   - Diff shown in parentheses: industry_price_pct_vs_benchmark
      //     (the precomputed DB column, NOT a client-side subtraction)
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          dataIndex?: number;
          axisValue?: string;
        }>;
        if (arr.length === 0) return "";
        const idx = arr[0].dataIndex ?? 0;
        const tick = times[idx] ?? "";
        const bv = benchPct[idx];
        const benchPctStr = bv == null ? "—" : (bv * 100).toFixed(3) + "%";
        let html = `<div style="font-weight:600">${data.benchmark_name} (${data.benchmark_code})</div>` +
          `<div style="margin-top:2px;opacity:0.85">tick ${tick} · bench ${benchPctStr}</div>`;

        // Collect (industry_id, label, pct, diff) for industries with data
        // at this tick, then sort by diff descending and pick top 5 + bottom 5.
        // diff comes from the precomputed DB column (industry_price_pct_vs_benchmark).
        const rows: Array<{ label: string; pct: number; diff: number }> = [];
        for (const [, info] of indPctByTime) {
          const iv = info.pctByTime.get(tick);
          const dv = info.diffByTime.get(tick);
          if (iv == null || dv == null) continue;
          rows.push({ label: info.label, pct: iv, diff: dv });
        }
        rows.sort((a, b) => b.diff - a.diff);
        const top5 = rows.slice(0, 5);
        const bottom5 = rows.slice(-5).reverse(); // most negative first
        // Avoid duplicating industries when there are <= 10 total
        const shown = new Set<string>();
        const renderRow = (r: { label: string; pct: number; diff: number }) => {
          if (shown.has(r.label)) return "";
          shown.add(r.label);
          const arrow = r.diff >= 0 ? "▲" : "▼";
          const diffColor = r.diff >= 0 ? UP_COLOR : DOWN_COLOR;
          const diffStr = (r.diff >= 0 ? "+" : "") + (r.diff * 100).toFixed(3) + "%";
          return `<div style="margin-top:1px"><span style="color:${diffColor}">${arrow}</span> ${r.label}: <b>${(r.pct * 100).toFixed(3)}%</b> <span style="opacity:0.7">(${diffStr})</span></div>`;
        };

        if (top5.length > 0) {
          html += `<div style="margin-top:3px;opacity:0.6;font-size:10px">▲ Top 5</div>`;
          for (const r of top5) html += renderRow(r);
        }
        if (bottom5.length > 0 && bottom5[0].diff < 0) {
          html += `<div style="margin-top:3px;opacity:0.6;font-size:10px">▼ Bottom 5</div>`;
          for (const r of bottom5) html += renderRow(r);
        }
        return html;
      },
    },
    xAxis: {
      type: "category",
      data: times,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 10,
        // Show a label every 30 min (at :00 and :30 minutes). Times from the
        // API are "HH:MM:SS" — slice the minutes field (chars 3-5) rather
        // than using endsWith, because endsWith(":00") would match the
        // SECONDS field (always "00") and show every 5-min label.
        interval: (idx: number) => {
          const t = times[idx];
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
      benchmarkSeries,
      ...(industrySeries as EChartsOption["series"]),
    ],
  } as EChartsOption;
}

// ============================================================================
//  MIDDLE PLOT — industry bar chart at selected tick (ALL industries)
// ============================================================================
export function buildIndustryBarsOption(
  data: IntradayMovementsResponse,
  selectedTick: string,
  themeMode: ThemeMode,
): EChartsOption {
  const c = axisColors(themeMode);

  // Filter industry_series to the selected tick, sort by value descending.
  const rowsAtTick = data.industry_series.filter(
    (r) => r.time === selectedTick && r.industry_price_pct != null,
  );
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
        const pct = r.industry_price_pct != null
          ? (r.industry_price_pct * 100).toFixed(4) + "%"
          : "—";
        return `<b>${r.industry_label}</b><br/>${pct}`;
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
  selectedIndustryId: string | null,
  themeMode: ThemeMode,
): EChartsOption {
  const c = axisColors(themeMode);

  // Filter member_series to (selected tick + selected industry), sort desc.
  let rowsAtTick = data.member_series.filter(
    (r) => r.time === selectedTick && r.code_price_pct != null,
  );
  if (selectedIndustryId) {
    rowsAtTick = rowsAtTick.filter((r) => r.industry_id === selectedIndustryId);
  }
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
        const pct = r.code_price_pct != null
          ? (r.code_price_pct * 100).toFixed(4) + "%"
          : "—";
        return `<b>${r.code_name || r.code}</b> (${r.code})<br/>${pct}`;
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
