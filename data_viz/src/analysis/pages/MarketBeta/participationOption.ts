/**
 * Build the ECharts options for the Market Participation panel.
 *
 * buildTurnoverShareOption — plot 1: whole-market turnover stacked bars
 *   (SS + SZ proxy segments, 亿元, left axis) with the raw daily SSE
 *   share line (%, right axis; toggleable).
 *
 * buildBreadthOption — plot 2: advance/decline breadth — adv/dec counts
 *   as diverging bars around zero (left axis) with the A/D ratio line
 *   (log-ish right axis; toggleable) and the day's median change line.
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import {
  axisColors,
  commonLegend,
  commonGrid,
  commonDataZoom,
  UP_COLOR,
  DOWN_COLOR,
  MA20_COLOR,
  PALETTE_HI,
} from "@/theme/chart-palette";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import React from "react";
import {
  renderReactElement,
  tooltipComponents,
} from "@/lib/react-tooltip-renderer";
import type { MarketParticipationDay } from "@/lib/api-client";

/** number | null from the pg-wire string columns. */
function num(v: string | number | null | undefined): number | null {
  if (v == null) return null;
  const n = typeof v === "number" ? v : Number.parseFloat(v);
  return Number.isFinite(n) ? n : null;
}

export interface TurnoverShareGauges {
  latestTotalYi: number | null;
  latestVsMa20: number | null;
  latestPctRank: number | null;
  latestSharePct: number | null;
  latestShareVsMa20Pp: number | null;
}

/** The latest-row gauge readouts plot 1's tooltip header carries. */
export function turnoverShareGauges(
  rows: MarketParticipationDay[],
): TurnoverShareGauges {
  const last = rows.length > 0 ? rows[rows.length - 1] : null;
  if (!last) {
    return {
      latestTotalYi: null,
      latestVsMa20: null,
      latestPctRank: null,
      latestSharePct: null,
      latestShareVsMa20Pp: null,
    };
  }
  return {
    latestTotalYi: num(last.total_trading_amount) != null
      ? num(last.total_trading_amount)! / 1e8
      : null,
    latestVsMa20: num(last.total_amt_vs_ma20),
    latestPctRank: num(last.total_amt_pct_rank_252),
    latestSharePct: num(last.ss_share) != null ? num(last.ss_share)! * 100 : null,
    latestShareVsMa20Pp: num(last.ss_share_vs_ma20_pp),
  };
}

/**
 * Plot 1 — whole-market turnover (SS + SZ proxy stacked bars, 亿元,
 * left axis) + raw daily SSE share line (%, right axis, toggleable via
 * `showShare`).
 */
export function buildTurnoverShareOption(
  rows: MarketParticipationDay[],
  mode: ThemeMode,
  showShare: boolean,
): EChartsOption {
  const c = axisColors(mode);
  const dates = rows.map((r) => r.date);
  const ssYi = rows.map((r) => {
    const v = num(r.ss_trading_amount);
    return v == null ? 0 : v / 1e8;
  });
  const szYi = rows.map((r) => {
    const v = num(r.sz_trading_amount);
    return v == null ? 0 : v / 1e8;
  });
  const totalYi = dates.map((_, i) => ssYi[i] + szYi[i]);
  const sharePct = rows.map((r) => {
    const v = num(r.ss_share);
    return v == null ? null : v * 100;
  });

  const series: EChartsOption["series"] = [
    {
      name: "SS Amt (亿)",
      type: "bar",
      stack: "mkt_amt",
      yAxisIndex: 0,
      data: ssYi,
      itemStyle: { opacity: 0.45 },
      barWidth: "90%",
      z: 1,
    },
    {
      name: "SZ Amt (亿)",
      type: "bar",
      stack: "mkt_amt",
      yAxisIndex: 0,
      data: szYi,
      itemStyle: { opacity: 0.45 },
      barWidth: "90%",
      z: 2,
    },
    ...(showShare
      ? [
          {
            name: "SSE Share %",
            type: "line" as const,
            showSymbol: false,
            yAxisIndex: 1,
            data: sharePct,
            lineStyle: { width: 2 },
            z: 10,
          },
        ]
      : []),
  ];

  return baseChartOption(mode, {
    legend: commonLegend(mode, { data: series.map((s) => (s as { name: string }).name) }),
    grid: commonGrid({ left: 64, right: 56, bottom: 50, top: 32 }),
    dataZoom: commonDataZoom(),
    tooltip: commonTooltip(mode, {
      axisPointer: { type: "cross", snap: true },
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          dataIndex?: number;
        }>;
        if (arr.length === 0) return "";
        const idx = arr[0].dataIndex ?? 0;
        const dateStr = dates[idx] ?? "";
        if (!dateStr) return "";
        const children: React.ReactNode[] = [
          React.createElement(tooltipComponents.Header, null, dateStr),
        ];
        const lines: React.ReactNode[] = [
          React.createElement(
            "div",
            null,
            "Total ",
            React.createElement(
              "b",
              null,
              Math.round(totalYi[idx]).toLocaleString(),
            ),
            " 亿",
          ),
          React.createElement(
            "div",
            { style: { opacity: 0.75, fontSize: "0.92em" } },
            `SS ${Math.round(ssYi[idx]).toLocaleString()} · SZ ${Math.round(szYi[idx]).toLocaleString()} 亿`,
          ),
        ];
        if (showShare && sharePct[idx] != null) {
          lines.push(
            React.createElement(
              "div",
              { style: { opacity: 0.75, fontSize: "0.92em" } },
              `SSE share ${sharePct[idx]!.toFixed(2)}%`,
            ),
          );
        }
        children.push(
          React.createElement(
            "div",
            { style: { marginTop: 2 } },
            lines,
          ),
        );
        return renderReactElement(
          React.createElement(React.Fragment, null, ...children),
        );
      },
    }),
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 9 },
    },
    yAxis: [
      {
        type: "value",
        name: "亿",
        scale: true,
        axisLabel: { color: c.textColor, fontSize: 9 },
        splitLine: { lineStyle: { color: c.splitLineColor } },
      },
      ...(showShare
        ? [
            {
              type: "value" as const,
              name: "SSE %",
              scale: true,
              axisLabel: {
                color: c.textColor,
                fontSize: 9,
                formatter: (v: number) => `${v.toFixed(0)}%`,
              },
              splitLine: { show: false },
            },
          ]
        : []),
    ],
    series,
  });
}

/**
 * Plot 2 — advance/decline breadth: diverging adv (+) / dec (−) count
 * bars around zero (left axis), the day's median % change line (left %,
 * axis 0 shared), and the A/D ratio line (right axis, toggleable via
 * `showRatio`).
 */
export function buildBreadthOption(
  rows: MarketParticipationDay[],
  mode: ThemeMode,
  showRatio: boolean,
): EChartsOption {
  const c = axisColors(mode);
  const dates = rows.map((r) => r.date);
  const advBars = rows.map((r) => (r.adv_count ?? 0));
  const decBars = rows.map((r) => -(r.dec_count ?? 0));
  const medianPct = rows.map((r) => num(r.median_pct_change));
  const adRatio = rows.map((r) => num(r.ad_ratio));

  const series: EChartsOption["series"] = [
    {
      // Rise side = GREEN, drop side = RED — the house up/down semantics
      // (chart-palette UP_COLOR / DOWN_COLOR).
      name: "Advancing",
      type: "bar",
      stack: "breadth",
      yAxisIndex: 0,
      data: advBars,
      itemStyle: { color: UP_COLOR, opacity: 0.5 },
      barWidth: "90%",
      z: 1,
    },
    {
      name: "Declining",
      type: "bar",
      stack: "breadth",
      yAxisIndex: 0,
      data: decBars,
      itemStyle: { color: DOWN_COLOR, opacity: 0.5 },
      barWidth: "90%",
      z: 2,
    },
    {
      name: "Median % Chg",
      type: "line",
      showSymbol: false,
      yAxisIndex: 1,
      data: medianPct,
      lineStyle: { width: 1.5, color: MA20_COLOR },
      itemStyle: { color: MA20_COLOR },
      z: 8,
    },
    ...(showRatio
      ? [
          {
            name: "A/D Ratio",
            type: "line" as const,
            showSymbol: false,
            yAxisIndex: 2,
            data: adRatio,
            lineStyle: { width: 2, color: PALETTE_HI },
            itemStyle: { color: PALETTE_HI },
            z: 10,
          },
        ]
      : []),
  ];

  return baseChartOption(mode, {
    legend: commonLegend(mode, { data: series.map((s) => (s as { name: string }).name) }),
    grid: commonGrid({ left: 56, right: 56, bottom: 50, top: 32 }),
    dataZoom: commonDataZoom(),
    tooltip: commonTooltip(mode, {
      axisPointer: { type: "cross", snap: true },
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          dataIndex?: number;
        }>;
        if (arr.length === 0) return "";
        const idx = arr[0].dataIndex ?? 0;
        const dateStr = dates[idx] ?? "";
        if (!dateStr) return "";
        const children: React.ReactNode[] = [
          React.createElement(tooltipComponents.Header, null, dateStr),
        ];
        const counts: React.ReactNode[] = [
          React.createElement(
            "b",
            { style: { color: UP_COLOR } },
            `▲ ${advBars[idx].toLocaleString()}`,
          ),
          "  /  ",
          React.createElement(
            "b",
            { style: { color: DOWN_COLOR } },
            `▼ ${(-decBars[idx]).toLocaleString()}`,
          ),
          "  /  = ",
          (rows[idx].flat_count ?? 0).toLocaleString(),
        ];
        const lines: React.ReactNode[] = [
          React.createElement("div", null, counts),
        ];
        if (medianPct[idx] != null) {
          lines.push(
            React.createElement(
              "div",
              { style: { opacity: 0.75, fontSize: "0.92em" } },
              `median ${medianPct[idx]!.toFixed(2)}%`,
            ),
          );
        }
        if (showRatio && adRatio[idx] != null) {
          lines.push(
            React.createElement(
              "div",
              { style: { opacity: 0.75, fontSize: "0.92em" } },
              `A/D ${adRatio[idx]!.toFixed(2)}`,
            ),
          );
        }
        children.push(
          React.createElement(
            "div",
            { style: { marginTop: 2 } },
            lines,
          ),
        );
        return renderReactElement(
          React.createElement(React.Fragment, null, ...children),
        );
      },
    }),
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 9 },
    },
    yAxis: [
      {
        type: "value",
        name: "names",
        axisLabel: { color: c.textColor, fontSize: 9 },
        splitLine: { lineStyle: { color: c.splitLineColor } },
      },
      {
        type: "value",
        name: "median %",
        scale: true,
        axisLabel: { color: c.textColor, fontSize: 9 },
        splitLine: { show: false },
      },
      ...(showRatio
        ? [
            {
              type: "value" as const,
              name: "A/D",
              scale: true,
              axisLabel: { color: c.textColor, fontSize: 9 },
              splitLine: { show: false },
            },
          ]
        : []),
    ],
    series,
  });
}
