/**
 * Build the ECharts option for the Industry ETF Contribution bar chart —
 * 2nd plot onward in "ETF Contribution" mode.
 *
 * Mirrors the benchmark attribution bar chart but with ETFs as bars instead
 * of benchmark indices:
 *   Bar 1 (left  Y-axis): ETF trading amount (亿元) — colored by ETF return
 *                         direction (green if up, red if down).
 *   Bar 2 (right Y-axis): ETF's % share of industry total trading amount
 *                         (trading_amount / industry_etf_trading_amount × 100).
 *
 * Tooltip surfaces: trading amount, ETF return, % share, parent index code,
 * and the industry aggregate + MA5 for context.
 *
 * Sorted by trading amount descending (largest capital-flow ETFs first).
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import type { IndustryEtfContributionBarsResponse } from "@shared/types";
import {
  UP_COLOR,
  DOWN_COLOR,
  MUTED_PALETTE,
  SUBTITLE_COLOR,
  axisColors,
  commonLegend,
  commonGrid,
} from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import React from "react";
import { renderReactElement, tooltipComponents } from "@/lib/react-tooltip-renderer";

/** Format a fractional value as a signed percentage string. */
function fmtPctSigned(v: number | null, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return (v >= 0 ? "+" : "") + fmtNum(v * 100, digits) + "%";
}

/** Format a yuan amount as 亿元 (100M yuan). */
function fmtAmtYi(v: number | null, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return fmtNum(v / 1e8, digits) + "亿";
}

export function buildIndustryEtfContributionOption(
  data: IndustryEtfContributionBarsResponse,
  themeMode: ThemeMode,
): EChartsOption {
  const c = axisColors(themeMode);

  // Filter to ETFs with non-null trading_amount (bars need a value).
  const etfs = data.etfs.filter((e) => e.trading_amount != null);

  const labels = etfs.map((e) => e.etf_name || e.etf_code);
  const codes = etfs.map((e) => e.etf_code);
  const amounts = etfs.map((e) => e.trading_amount);
  const returns = etfs.map((e) => e.etf_return);
  const parentIndices = etfs.map((e) => e.parent_index_code);

  // % share of industry total
  const industryTotal = data.industry_etf_trading_amount;
  const shares = etfs.map((e) => {
    if (e.trading_amount == null || industryTotal == null || industryTotal === 0) return null;
    return (e.trading_amount / industryTotal) * 100;
  });

  // Max absolute amount for label visibility threshold.
  const maxAmt = amounts.reduce((m, v) => (v == null ? m : Math.max(m, Math.abs(v))), 0);

  const base: Pick<EChartsOption, "backgroundColor" | "animation" | "grid" | "legend"> = {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 64, right: 64, bottom: 96 }),
    legend: commonLegend(themeMode, {
      itemWidth: 12,
      itemHeight: 7,
      data: ["Trading Amt", "Share %"],
    }),
  };

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
        const amt = amounts[idx];
        const ret = returns[idx];
        const share = shares[idx];
        const parent = parentIndices[idx];
        const rsign = ret == null ? "" : ret >= 0 ? "▲ " : "▼ ";
        const children: React.ReactNode[] = [];
        children.push(
          React.createElement(tooltipComponents.Header, null,
            labels[idx],
            " ",
            React.createElement("span", { style: { opacity: 0.6 } }, `(${codes[idx]})`)
          ),
          React.createElement("div", { style: { marginTop: 2 } }, `Parent index: ${parent}`),
          React.createElement("div", null,
            "Trading Amt: ",
            React.createElement(tooltipComponents.Bold, null, fmtAmtYi(amt))
          ),
          React.createElement("div", null,
            rsign,
            "ETF Return: ",
            React.createElement(tooltipComponents.Bold, {
              style: { color: ret == null ? c.textColor : ret >= 0 ? UP_COLOR : DOWN_COLOR }
            }, fmtPctSigned(ret))
          ),
          React.createElement("div", null,
            `Share of Industry: ${share == null ? "—" : fmtNum(share, 1) + "%"}`
          )
        );
        if (industryTotal != null) {
          const totalRow: React.ReactNode[] = [
            "Industry Total: ",
            fmtAmtYi(industryTotal)
          ];
          if (data.industry_etf_trading_amount_ma5 != null) {
            totalRow.push(
              ` · MA5: `,
              fmtAmtYi(data.industry_etf_trading_amount_ma5)
            );
          }
          children.push(
            React.createElement("div", { style: { opacity: 0.7, marginTop: 2 } }, ...totalRow)
          );
        }
        return renderReactElement(React.createElement(React.Fragment, null, ...children));
      },
    },
    xAxis: {
      type: "category",
      data: labels,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 8,
        interval: 0,
        rotate: 55,
        formatter: (v: string) => {
          const lbl = v.length > 6 ? v.slice(0, 5) + "…" : v;
          return lbl;
        },
      },
      splitLine: { show: false },
    },
    yAxis: [
      {
        type: "value",
        name: "Trading Amt (亿)",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => fmtNum(v / 1e8, 1),
        },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
      {
        type: "value",
        name: "Share %",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => fmtNum(v, 0) + "%",
        },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: "Trading Amt",
        type: "bar",
        yAxisIndex: 0,
        barMaxWidth: 22,
        data: amounts.map((v, i) => {
          const ret = returns[i];
          const visible = v != null && maxAmt > 0 && Math.abs(v) / maxAmt >= 0.05;
          return {
            value: v,
            itemStyle: {
              color: ret == null ? SUBTITLE_COLOR : ret >= 0 ? UP_COLOR : DOWN_COLOR,
              opacity: 0.85,
            },
            label: {
              show: visible,
              position: "insideTop" as const,
              distance: 2,
              color: c.textColor,
              fontSize: 8,
              fontWeight: 600,
              formatter: () => (v == null ? "" : fmtAmtYi(v, 1)),
            },
          };
        }),
      },
      {
        name: "Share %",
        type: "bar",
        yAxisIndex: 1,
        barMaxWidth: 22,
        data: shares.map((v) => ({
          value: v,
          itemStyle: {
            color: MUTED_PALETTE[0],
            opacity: 0.6,
          },
          label: {
            show: v != null && v >= 1.0,
            position: "insideTop" as const,
            distance: 2,
            color: SUBTITLE_COLOR,
            fontSize: 8,
            formatter: () => (v == null || v < 1.0 ? "" : fmtNum(v, 1) + "%"),
          },
        })),
      },
    ],
  };
}
