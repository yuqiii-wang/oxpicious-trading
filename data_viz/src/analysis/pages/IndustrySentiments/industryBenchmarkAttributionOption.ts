/**
 * Build the ECharts option for the Industry-level Benchmark Attribution
 * bar chart — 2nd plot onward in "Benchmark Attribution" mode.
 *
 * Data source: analysis.industry_attributions (pre-materialized per
 * (date, industry_id, benchmark_code) row with industry_shared_weight +
 * benchmark_shared_weight). benchmark_return is computed on-the-fly.
 *
 * Vertical grouped bars per benchmark:
 *   Bar 1 (left  Y-axis): contribution = benchmark_return × (benchmark_shared_weight / 100).
 *                         Green if benchmark rose, red if dropped.
 *   Bar 2 (right Y-axis): benchmark_shared_weight (% of benchmark in industry stocks, 0-100).
 *   Bar 3 (right Y-axis): industry_shared_weight (% of member overlap, can exceed 100).
 *
 * Tooltip surfaces: contribution, raw benchmark return, both shared weights,
 * and a broad-market tag.
 *
 * The selected benchmark (from the dropdown — the one whose price chart is
 * shown as the 1st plot) is highlighted with a brighter bar color.
 *
 * Uses the shared attributionBarCommon module so the visual style stays
 * consistent with PerfAttr's fluctuationOption.
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import type { IndustryBenchmarkAttributionResponse } from "@shared/types";
import { MUTED_PALETTE, UP_COLOR, DOWN_COLOR, axisColors } from "@/theme/chart-palette";
import {
  type AttributionBarRow,
  type AttributionBarContext,
  sortAndFilterRows,
  computeContributions,
  fmtPctSigned,
  buildBaseOption,
  buildXAxis,
  buildYAxes,
  buildContributionBarData,
  buildSharedWtBarData,
} from "@/analysis/pages/shared/attributionBarCommon";
import { fmtNum } from "@/lib/series";

export function buildIndustryBenchmarkAttributionOption(
  data: IndustryBenchmarkAttributionResponse,
  themeMode: ThemeMode,
  selectedBenchmarkCode: string | null,
): EChartsOption {
  const c = axisColors(themeMode);

  // Map API rows to generic AttributionBarRow[]
  const rawRows: AttributionBarRow[] = data.benchmarks.map((b) => ({
    label: b.benchmark_name || b.benchmark_code,
    code: b.benchmark_code,
    benchmarkReturn: b.benchmark_return,
    sharedWeight: b.benchmark_shared_weight,
    isBroadMarket: b.is_broad_market === true,
  }));

  const ctx: AttributionBarContext = {
    selectedCode: selectedBenchmarkCode,
    themeMode,
    // Both broad-market AND member-index benchmarks are materialized — keep
    // them all visible. Broad-market bars are dimmed (lower opacity) by
    // buildContributionBarData/buildSharedWtBarData; member-index bars are
    // shown at full opacity (they are the industry's own indices).
    showBroadMarket: true,
  };

  const sorted = sortAndFilterRows(rawRows, ctx);

  const labels = sorted.map((r) => r.label);
  const codes = sorted.map((r) => r.code);
  const broadFlags = sorted.map((r) => r.isBroadMarket);
  const returns = sorted.map((r) => r.benchmarkReturn);
  const benchSharedWts = sorted.map((r) => r.sharedWeight);
  const industrySharedWts = sorted.map(
    (_, i) => data.benchmarks.find((b) => b.benchmark_code === sorted[i].code)?.industry_shared_weight ?? null,
  );

  const contrib = computeContributions(sorted);
  const maxAbsContrib = contrib.reduce(
    (m, v) => (v == null ? m : Math.max(m, Math.abs(v))),
    0,
  );

  const base = buildBaseOption(themeMode, [
    "Contribution",
    "Benchmark Wt",
    "Industry Wt",
  ]);

  return {
    ...base,
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      backgroundColor: c.tooltipBg,
      borderColor: c.splitLineColor,
      textStyle: { color: c.textColor, fontSize: 11 },
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          dataIndex?: number;
        }>;
        if (arr.length === 0) return "";
        const idx = arr[0].dataIndex ?? 0;
        const rv = returns[idx];
        const cv = contrib[idx];
        const bw = benchSharedWts[idx];
        const iw = industrySharedWts[idx];
        const sign = cv == null ? "" : cv >= 0 ? "▲ " : "▼ ";
        const rsign = rv == null ? "" : rv >= 0 ? "▲ " : "▼ ";
        const broad = broadFlags[idx] ? " · broad-market" : "";
        const selected = codes[idx] === selectedBenchmarkCode ? " · selected" : "";
        return `
          <div style="font-weight:600">${labels[idx]} <span style="opacity:0.6">(${codes[idx]})</span>${broad}${selected}</div>
          <div style="margin-top:2px">${sign}Contribution (Ret×BenchWt): <b style="color:${cv == null ? c.textColor : cv >= 0 ? UP_COLOR : DOWN_COLOR}">${fmtPctSigned(cv)}</b></div>
          <div>${rsign}Benchmark Return: ${fmtPctSigned(rv)}</div>
          <div>Benchmark Shared Wt (in industry): ${bw == null ? "—" : fmtNum(bw, 2) + "%"}</div>
          <div>Industry Shared Wt (in benchmark): ${iw == null ? "—" : fmtNum(iw, 2) + "%"}</div>
        `;
      },
    },
    xAxis: buildXAxis(labels, codes, broadFlags, ctx),
    yAxis: buildYAxes(themeMode),
    series: [
      {
        name: "Contribution",
        type: "bar",
        yAxisIndex: 0,
        data: buildContributionBarData(sorted, contrib, maxAbsContrib, ctx),
        barMaxWidth: 22,
        label: { show: false, color: c.textColor, fontSize: 8 },
      },
      {
        name: "Benchmark Wt",
        type: "bar",
        yAxisIndex: 1,
        data: buildSharedWtBarData(sorted, benchSharedWts, ctx, MUTED_PALETTE[0]),
        barMaxWidth: 22,
      },
      {
        name: "Industry Wt",
        type: "bar",
        yAxisIndex: 1,
        data: buildSharedWtBarData(sorted, industrySharedWts, ctx, MUTED_PALETTE[1]),
        barMaxWidth: 22,
      },
    ],
  };
}
