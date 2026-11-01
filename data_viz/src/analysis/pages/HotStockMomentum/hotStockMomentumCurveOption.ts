/**
 * Build the ECharts option for the Hot Stock Momentum chart — the
 * security's daily stats close (left axis) + trading amount bars (right
 * axis), with the momentum curve on its own offset right axis once the
 * placeholder pipeline starts producing rows (it is NULL today, so the
 * metric series/axis are omitted until then — the benchmark-price
 * chart's close + amt layout).
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import type { HotStockMomentumChartResponse } from "@shared/types";
import {
  PALETTE_HI,
  MUTED_PALETTE,
  NEUTRAL_FILL,
  axisColors,
  commonLegend,
  commonGrid,
  commonDataZoom,
} from "@/theme/chart-palette";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import { fmtNum } from "@/lib/series";

export function buildHotStockMomentumCurveOption(
  data: HotStockMomentumChartResponse,
  mode: ThemeMode,
): EChartsOption {
  const c = axisColors(mode);
  const dates = data.rows.map((r) => r.date);
  const closes = data.rows.map((r) => r.close);
  const amts = data.rows.map((r) => r.trading_amount);
  const metricValues = data.rows.map((r) => r.momentum);
  // The metric pipeline is a placeholder — omit its series/axis until a
  // non-null value shows up (a fresh null series would render nothing).
  const hasMetric = metricValues.some((v) => v != null && Number.isFinite(v));

  const series: EChartsOption["series"] = [];

  // 1. Trading amount bars (background anchor, right axis — the
  //    benchmark-price chart's layout; lines draw on top).
  series.push({
    name: "Trading Amt",
    type: "bar",
    yAxisIndex: 1,
    data: amts,
    barCategoryGap: "20%",
    itemStyle: { color: NEUTRAL_FILL, opacity: 0.18 },
    z: 1,
    tooltip: { show: false },
  });

  // 2. Close line (left axis).
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

  // 3. Momentum line (own offset right axis — only when data exists).
  if (hasMetric) {
    series.push({
      name: "Momentum",
      type: "line",
      yAxisIndex: 2,
      data: metricValues,
      showSymbol: false,
      symbol: "circle",
      symbolSize: 5,
      lineStyle: { color: MUTED_PALETTE[0], width: 1.5 },
      itemStyle: { color: MUTED_PALETTE[0] },
      z: 10,
    });
  }

  return baseChartOption(mode, {
    grid: commonGrid({ bottom: 50, top: 32, right: hasMetric ? 96 : 56 }),
    dataZoom: commonDataZoom(),
    tooltip: commonTooltip(mode, {
      axisPointer: { type: "cross" },
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          dataIndex?: number;
        }>;
        const idx = arr[0]?.dataIndex ?? 0;
        const close = closes[idx];
        const amt = amts[idx];
        const children = [
          `${data.name || data.code} (${data.code})`,
          dates[idx] ?? "—",
          `Close: ${close == null || !Number.isFinite(close) ? "—" : fmtNum(close, 2)}`,
          `Trading Amt: ${amt == null || !Number.isFinite(amt) ? "—" : fmtNum(amt / 1e8, 2) + "亿"}`,
        ];
        if (hasMetric) {
          const v = metricValues[idx];
          children.push(
            `Momentum: ${v == null || !Number.isFinite(v) ? "—" : fmtNum(v, 4)}`,
          );
        }
        return children.join("<br/>");
      },
    }),
    legend: commonLegend(mode, {
      data: hasMetric ? ["Close", "Trading Amt", "Momentum"] : ["Close", "Trading Amt"],
    }),
    xAxis: {
      type: "category",
      data: dates,
      boundaryGap: true, // bars need a gap so they center on ticks
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
      {
        type: "value",
        scale: true,
        name: "Amt (亿)",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: number) => Math.round(v / 1e8).toLocaleString("en-US") },
        splitLine: { show: false },
      },
      // Momentum axis (offset further right) — present only with data.
      ...(hasMetric
        ? [{
            type: "value" as const,
            scale: true,
            name: "Momentum",
            nameTextStyle: { color: c.textColor, fontSize: 9 },
            axisLine: { lineStyle: { color: c.axisLineColor } },
            axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: number) => fmtNum(v, 2) },
            splitLine: { show: false },
          }]
        : []),
    ],
    series,
  });
}
