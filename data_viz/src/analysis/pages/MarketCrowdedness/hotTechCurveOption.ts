/**
 * Build the ECharts option for the hot-stock tech profile chart — the
 * security's daily stats close (left axis), with the SELECTED hot-tech
 * stat's mean and median over the day's rolling hot set on the offset
 * right axis. Two read modes switch what the stat series show (the
 * shared OhlcModeToggle naming — abs | pct):
 *   abs  — mean_X / median_X on the metric's own scale (scale: true;
 *          ratios/returns as decimals, rsi14 0..100, macd price units)
 *   pct  — mean_X_pct / median_X_pct, the own-history percentiles
 *          plotted 0..100 with a "%" axis (the ma-spread percentage-
 *          mode setup; comparable across metrics and pools)
 * Series names stay Mean/Median in both modes (the axis name carries
 * the unit, same as ma-spread). The tooltip always carries both
 * readings plus the day's hot_n (the number of hot members the
 * aggregates cover).
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import type {
  MarketCrowdednessHotTechMetric,
  MarketCrowdednessHotTechResponse,
  MarketCrowdednessHotTechRow,
} from "@shared/types";
import {
  PALETTE_HI,
  MUTED_PALETTE,
  axisColors,
  commonLegend,
  commonGrid,
  commonDataZoom,
} from "@/theme/chart-palette";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import { fmtNum } from "@/lib/series";
import type { HotTechView } from "./types";
import { HOT_TECH_METRIC_ITEMS } from "./constants";

export function buildHotTechCurveOption(
  data: MarketCrowdednessHotTechResponse,
  metric: MarketCrowdednessHotTechMetric,
  view: HotTechView,
  mode: ThemeMode,
): EChartsOption {
  const c = axisColors(mode);
  const dates = data.rows.map((r) => r.date);
  const closes = data.rows.map((r) => r.close);
  const hotN = data.rows.map((r) => r.hot_n);
  const pct = view === "pct" ? "_pct" : "";
  const meanKey = `mean_${metric}${pct}` as keyof MarketCrowdednessHotTechRow;
  const medianKey = `median_${metric}${pct}` as keyof MarketCrowdednessHotTechRow;
  const meanValues = data.rows.map((r) => r[meanKey]);
  const medianValues = data.rows.map((r) => r[medianKey]);
  const rawMeanValues = data.rows.map((r) => r[`mean_${metric}`]);
  const rawMedianValues = data.rows.map((r) => r[`median_${metric}`]);
  const meanPctValues = data.rows.map((r) => r[`mean_${metric}_pct`]);
  const medianPctValues = data.rows.map((r) => r[`median_${metric}_pct`]);
  const metricLabel = HOT_TECH_METRIC_ITEMS.find(
    (m) => m.key === metric,
  )?.label ?? metric;
  // Out-of-scope codes / pre-gate days have no metric rows — omit the
  // stat series/axis until a non-null value shows up.
  const hasMetric = meanValues.some((v) => v != null && Number.isFinite(v));
  // Pct mode plots the percentiles in percent units (0..100) — the
  // ma-spread percentage-mode setup; series names stay Mean/Median (the
  // axis "%" name carries the unit).
  const pctScale = view === "pct" ? 100.0 : 1.0;
  const scale = (v: number | null) =>
    v == null || !Number.isFinite(v) ? null : v * pctScale;

  const series: EChartsOption["series"] = [];

  // 1. Close line (left axis).
  series.push({
    name: "Close",
    type: "line",
    data: closes,
    showSymbol: false,
    symbol: "circle",
    symbolSize: 5,
    lineStyle: { color: PALETTE_HI, width: 1.5 },
    itemStyle: { color: PALETTE_HI },
    z: 10,
  });

  // 2. The selected stat's mean (translucent evidence) + median
  //    (the robust read, on top) on the offset right axis.
  if (hasMetric) {
    series.push({
      name: "Mean",
      type: "line",
      yAxisIndex: 1,
      data: meanValues.map(scale),
      showSymbol: false,
      symbol: "circle",
      symbolSize: 5,
      lineStyle: { color: MUTED_PALETTE[0], width: 1.2, opacity: 0.55 },
      itemStyle: { color: MUTED_PALETTE[0] },
      z: 10,
    });
    series.push({
      name: "Median",
      type: "line",
      yAxisIndex: 1,
      data: medianValues.map(scale),
      showSymbol: false,
      symbol: "circle",
      symbolSize: 5,
      lineStyle: { color: MUTED_PALETTE[1], width: 1.4, opacity: 0.75 },
      itemStyle: { color: MUTED_PALETTE[1] },
      z: 11,
    });
  }

  const fmtVal = (v: number | null) =>
    v == null || !Number.isFinite(v) ? "—" : fmtNum(v, 4);
  // Math.round, NOT fmtNum(v, 0) — fmtNum strips trailing zeros, which
  // would render a 20%ile read as "2%ile" (and 100 as "1").
  const fmtPctile = (v: number | null) =>
    v != null && Number.isFinite(v)
      ? ` · ${Math.round(v * 100)}%ile`
      : "";

  return baseChartOption(mode, {
    // top: 44 (vs the crowdedness card's 32) separates the legend row
    // from the right-axis name — with a short axis name ("Px/MA20") the
    // 32px band draws the two right-aligned texts on top of each other.
    grid: commonGrid({ bottom: 50, top: 44, right: hasMetric ? 96 : 56 }),
    dataZoom: commonDataZoom(),
    tooltip: commonTooltip(mode, {
      axisPointer: { type: "cross" },
      confine: true,
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          dataIndex?: number;
        }>;
        const idx = arr[0]?.dataIndex ?? 0;
        const close = closes[idx];
        const hn = hotN[idx];
        const children = [
          `${data.name || data.code} (${data.code})`,
          dates[idx] ?? "—",
          `Close: ${close == null || !Number.isFinite(close) ? "—" : fmtNum(close, 2)}`,
          `Hot set: ${hn != null && Number.isFinite(hn) ? `${hn} names` : "—"}`,
        ];
        if (hasMetric) {
          children.push(
            `${metricLabel} mean: ${fmtVal(rawMeanValues[idx])}${fmtPctile(meanPctValues[idx])}`,
            `${metricLabel} median: ${fmtVal(rawMedianValues[idx])}${fmtPctile(medianPctValues[idx])}`,
          );
        }
        return children.join("<br/>");
      },
    }),
    legend: commonLegend(mode, {
      data: hasMetric ? ["Close", "Mean", "Median"] : ["Close"],
    }),
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 9 },
      splitLine: { show: false },
    },
    yAxis: [
      {
        type: "value",
        scale: true,
        name: "Close",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: number) => Math.round(v).toLocaleString("en-US") },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
      // Stat axis (offset right) — present only with data; the pct read
      // pins to the 0..100 percentile scale with the ma-spread "%"
      // axis name.
      ...(hasMetric
        ? [{
            type: "value" as const,
            ...(view === "pct"
              ? { min: 0, max: 100, name: "%" }
              : { scale: true, name: metricLabel }),
            nameTextStyle: { color: c.textColor, fontSize: 9 },
            axisLine: { lineStyle: { color: c.axisLineColor } },
            axisLabel: {
              color: c.textColor,
              fontSize: 9,
              // Pct labels are plain integers — fmtNum's trailing-zero
              // strip would render the 100/80/... axis as 1/8/...
              formatter: (v: number) =>
                view === "pct" ? String(Math.round(v)) : fmtNum(v, 2),
            },
            splitLine: { show: false },
          }]
        : []),
    ],
    series,
  });
}
