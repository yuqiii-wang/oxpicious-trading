/**
 * Build the ECharts option for an aggregate chart (mean_pe or
 * total_trading_amount) under the main Industry Sentiments price chart.
 *
 * In single-industry mode: one line per pool_size (all/small/mid/large) so
 * the user can compare valuation/turnover across pool sizes.
 * In multi-industry mode: one line per industry (for the selected pool_size)
 * so the user can compare across industries — same pattern as the "Mean
 * only" price mode.
 *
 * X-axis uses the same allDates + visible range as the main price chart so
 * the shared date-range slider controls all three plots in sync.
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import type { IndustrySentimentsChartResponse } from "@shared/types";
import { axisColors, commonLegend, commonGrid } from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import type { PoolSize, PerIndustryAggregation } from "./types";
import { MEAN_PALETTE, POOL_COLORS } from "./constants";

export function buildAggregateChartOption(
  data: IndustrySentimentsChartResponse,
  perIndustryAggregations: PerIndustryAggregation[],
  allDates: string[],
  visibleLo: number,
  visibleHi: number,
  poolSize: PoolSize,
  themeMode: ThemeMode,
  multiIndustry: boolean,
  column: "mean_pe" | "total_trading_amount",
  yAxisName: string,
  /** Resolve an industry_id to its MAJOR group color (multi-industry mode
   *  only). When omitted, falls back to MEAN_PALETTE by index. Passed by the
   *  page so the per-industry aggregate lines match the major colors used on
   *  the price chart. */
  industryColorFor?: (industryId: string) => string,
): EChartsOption {
  const c = axisColors(themeMode);
  const visibleDates = allDates.slice(visibleLo, visibleHi + 1);
  const series: Array<Record<string, unknown>> = [];
  // total_trading_amount is stored in yuan — divide by 1e8 to display in 亿元.
  // mean_pe is unitless and needs no scaling.
  const transformValue = (v: number | null): number | null =>
    v == null ? null : column === "total_trading_amount" ? v / 1e8 : v;

  if (!multiIndustry) {
    // Single-industry: one line per pool_size slice.
    for (const ps of ["all", "small", "mid", "large"] as PoolSize[]) {
      const aggByDate = new Map<string, number | null>();
      for (const a of data.aggregation) {
        if (a.pool_size !== ps) continue;
        aggByDate.set(a.date, transformValue(a[column]));
      }
      const aligned: Array<number | null> = allDates.map(
        (d) => aggByDate.get(d) ?? null,
      );
      const color = POOL_COLORS[ps];
      series.push({
        name: `${ps}`,
        type: "line",
        smooth: false,
        showSymbol: false,
        connectNulls: false,
        data: aligned.slice(visibleLo, visibleHi + 1),
        lineStyle: { width: 1.6, color },
        itemStyle: { color },
        z: 3,
      });
    }
  } else {
    // Multi-industry: one line per industry, filtered to selected pool_size.
    perIndustryAggregations.forEach((agg, i) => {
      const color = industryColorFor
        ? industryColorFor(agg.industry_id)
        : MEAN_PALETTE[i % MEAN_PALETTE.length];
      const shortLabel = (agg.industry_label || agg.industry_id).split("  ")[0] || agg.industry_id;
      const aggByDate = new Map<string, number | null>();
      for (const a of agg.aggregation) {
        if (a.pool_size !== poolSize) continue;
        aggByDate.set(a.date, transformValue(a[column]));
      }
      const aligned: Array<number | null> = allDates.map(
        (d) => aggByDate.get(d) ?? null,
      );
      series.push({
        name: shortLabel,
        type: "line",
        smooth: false,
        showSymbol: false,
        connectNulls: false,
        data: aligned.slice(visibleLo, visibleHi + 1),
        lineStyle: { width: 1.6, color },
        itemStyle: { color },
        z: 3,
      });
    });
  }

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 56, right: 24, bottom: 32 }),
    legend: commonLegend(themeMode, {
      data: series.map((s) => s.name as string),
    }),
    axisPointer: {
      link: [{ xAxisIndex: "all" }],
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line", snap: true },
      backgroundColor: c.tooltipBg,
      borderColor: c.splitLineColor,
      textStyle: { color: c.textColor, fontSize: 11 },
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          dataIndex?: number;
          seriesName?: string;
          value?: number | null;
        }>;
        if (arr.length === 0) return "";
        const idx0 = arr[0].dataIndex ?? 0;
        const dateStr = visibleDates[idx0] ?? "";
        if (!dateStr) return "";
        const unit = column === "mean_pe" ? "x" : "亿";
        const rowsHtml = arr
          .map((p) => {
            const v = p.value;
            const fmtV = (x: number | null | undefined) => {
              if (x == null || !Number.isFinite(x)) return "—";
              return fmtNum(x, column === "mean_pe" ? 2 : 1) + unit;
            };
            const color = (p.seriesName && (multiIndustry
              ? (industryColorFor
                  ? industryColorFor(
                      perIndustryAggregations.find(a =>
                        (a.industry_label || a.industry_id).split("  ")[0] === p.seriesName
                        || a.industry_id === p.seriesName,
                      )?.industry_id ?? "",
                    )
                  : MEAN_PALETTE[perIndustryAggregations.findIndex(a =>
                    (a.industry_label || a.industry_id).split("  ")[0] === p.seriesName
                    || a.industry_id === p.seriesName) % MEAN_PALETTE.length])
              : POOL_COLORS[(p.seriesName as PoolSize) ?? "all"])) ?? "#424242";
            return `<div style="display:flex;justify-content:space-between;gap:12px">
              <span style="color:${color}">━</span>
              <span style="flex:1">${p.seriesName ?? ""}</span>
              <b>${fmtV(v)}</b>
            </div>`;
          })
          .join("");
        return `<div style="font-weight:600">${dateStr}</div>
                <div style="margin-top:2px;opacity:0.7">${yAxisName}</div>
                <div style="margin-top:4px">${rowsHtml}</div>`;
      },
    },
    xAxis: {
      type: "category",
      data: visibleDates,
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
      name: yAxisName,
      nameTextStyle: { color: c.textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        formatter: (v: number) => fmtNum(v, column === "mean_pe" ? 1 : 0),
      },
      splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
    },
    series,
  };
}
