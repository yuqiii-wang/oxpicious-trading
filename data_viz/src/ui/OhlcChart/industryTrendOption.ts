/**
 * industryTrendOption — the pure ECharts option builder for CodeTrendChart's
 * INDUSTRY mode (`industryId` prop): one RAW close-price line per member
 * index of the industry, on the sorted union date axis of all members.
 *
 * Deliberately plain per the mode's contract ("just plot by close price"):
 * no rebasing, no pool-size dimming, no mean/σ overlay — those live on the
 * Industry Sentiments page's buildIndustryChartOption. Colors follow the
 * shared group-color convention: every member index is a variant shade of
 * its industry's single major color, assigned in the API's member order.
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import { axisColors, commonDataZoom, commonGrid, commonLegend } from "@/theme/chart-palette";
import { baseChartOption } from "@/shared/charts/base-chart";
import { buildGroupColorScheme, variantColorOf } from "@/theme/group-colors";
import { fmtNum } from "@/lib/series";
import type { CodeTrendIndustryData } from "./codeTrendTypes";

export function buildIndustryTrendOption(
  data: CodeTrendIndustryData,
  mode: ThemeMode,
): EChartsOption {
  const c = axisColors(mode);

  // Union date axis — sorted across ALL member indices (indices may start on
  // different listing dates; gaps stay null so lines simply start later).
  const dateSet = new Set<string>();
  for (const idx of data.indices) {
    for (const r of idx.rows) dateSet.add(r.date);
  }
  const allDates = Array.from(dateSet).sort();

  // Per-index variant shades of the industry's major color (same convention
  // as the Industry Sentiments multi-line chart).
  const scheme = buildGroupColorScheme([data.industry_id]);
  const major = scheme.majorColor(data.industry_id);

  const series = data.indices.map((idx, i) => {
    const closeByDate = new Map<string, number | null>();
    for (const r of idx.rows) closeByDate.set(r.date, r.close);
    const color = variantColorOf(major, i);
    return {
      name: idx.name || idx.code,
      type: "line" as const,
      smooth: false,
      showSymbol: false,
      connectNulls: false,
      data: allDates.map((d) => closeByDate.get(d) ?? null),
      lineStyle: { width: 1.5, color },
      itemStyle: { color },
    };
  });

  return baseChartOption(mode, {
    grid: commonGrid({ left: 56, right: 24, bottom: 50 }),
    dataZoom: commonDataZoom(),
    legend: commonLegend(mode, { data: series.map((s) => s.name) }),
    // Default themed axis tooltip (marker + name + close) — no override.
    xAxis: {
      type: "category",
      data: allDates,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        formatter: (v: string) => v.slice(0, 7),
      },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      name: "Close",
      nameTextStyle: { color: c.textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        formatter: (v: number) => fmtNum(v, 1),
      },
      scale: true,
      splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
    },
    series,
  });
}
