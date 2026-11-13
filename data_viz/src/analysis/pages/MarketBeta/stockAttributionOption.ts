/**
 * Build the ECharts option for the Stock Attribution histogram — one bar
 * per 'stock_change_bucket' bucket (12 codes, drop tail -> rise tail) in
 * code order, the number of member stocks whose daily pct_change fell in
 * the bucket on the as-of date.
 *
 * Coloring follows the repo chart convention (UP_COLOR green = rise,
 * DOWN_COLOR red = drop, same as the ETF-contribution return coloring):
 * buckets 0-5 ('<-17.5%' .. '-2.5~0%') are the drop side (DOWN_COLOR),
 * buckets 6-11 ('0~2.5%' .. '>=17.5%') the rise side (UP_COLOR). The
 * 0%-boundary sits between buckets 5 and 6 — pct_change == 0 falls in
 * bucket 6, so the rise side includes unchanged stocks.
 *
 * Tooltip surfaces: bucket label, stock count, % share of the industry's
 * total mapped stocks, and the up(≥0) / down(<0) split for the date.
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import type { IndustryStockAttributionResponse } from "@shared/types";
import {
  UP_COLOR,
  DOWN_COLOR,
  axisColors,
  commonGrid,
} from "@/theme/chart-palette";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import { fmtNum } from "@/lib/series";
import React from "react";
import { renderReactElement, tooltipComponents } from "@/lib/react-tooltip-renderer";

/** The first bucket code on the rise side (>= 0%): buckets 6-11 rise,
 *  0-5 drop. pct_change == 0 counts on the rise side (bucket 6 =
 *  '0~2.5%' is left-closed). */
const FIRST_RISE_BUCKET = 6;

export function buildStockAttributionOption(
  data: IndustryStockAttributionResponse,
  themeMode: ThemeMode,
): EChartsOption {
  const c = axisColors(themeMode);

  const buckets = [...data.buckets].sort((a, b) => a.bucket - b.bucket);
  const labels = buckets.map((b) => b.bucket_label);
  const counts = buckets.map((b) => b.stock_count);
  const maxCount = counts.reduce((m, v) => Math.max(m, v), 0);

  // Rise/drop split for the tooltip context line: buckets >= 6 are the
  // up(>=0) side, buckets <= 5 the down(<0) side.
  const upCount = buckets
    .filter((b) => b.bucket >= FIRST_RISE_BUCKET)
    .reduce((s, b) => s + b.stock_count, 0);
  const downCount = data.total_stocks - upCount;

  return baseChartOption(themeMode, {
    grid: commonGrid({ left: 56, right: 24, bottom: 56 }),
    legend: null,
    tooltip: commonTooltip(themeMode, {
      axisPointer: { type: "shadow" },
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          dataIndex?: number;
        }>;
        if (arr.length === 0) return "";
        const idx = arr[0].dataIndex ?? 0;
        const b = buckets[idx];
        if (!b) return "";
        const pct =
          b.pct_of_industry == null ? "—" : fmtNum(b.pct_of_industry * 100, 1) + "%";
        return renderReactElement(
          React.createElement(
            React.Fragment,
            null,
            React.createElement(tooltipComponents.Header, null, b.bucket_label),
            React.createElement("div", { style: { marginTop: 2 } },
              "Stocks: ",
              React.createElement(tooltipComponents.Bold, null, String(b.stock_count)),
            ),
            React.createElement("div", null, `Share of industry: ${pct}`),
            React.createElement(
              "div",
              { style: { opacity: 0.7, marginTop: 2 } },
              `Total ${data.total_stocks} · up(≥0) ${upCount} · down(<0) ${downCount}`,
            ),
          ),
        );
      },
    }),
    xAxis: {
      type: "category",
      data: labels,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        interval: 0,
        rotate: 40,
      },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      name: "Stocks",
      nameTextStyle: { color: c.textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 9 },
      minInterval: 1,
      splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
    },
    series: [
      {
        name: "Stocks",
        type: "bar",
        barMaxWidth: 34,
        data: buckets.map((b) => {
          const visible = b.stock_count > 0 && maxCount > 0
            && b.stock_count / maxCount >= 0.05;
          return {
            value: b.stock_count,
            itemStyle: {
              color: b.bucket >= FIRST_RISE_BUCKET ? UP_COLOR : DOWN_COLOR,
              opacity: 0.85,
            },
            label: {
              show: visible,
              position: "top" as const,
              distance: 2,
              color: c.textColor,
              fontSize: 8,
              fontWeight: 600,
              formatter: () => String(b.stock_count),
            },
          };
        }),
      },
    ],
  });
}
