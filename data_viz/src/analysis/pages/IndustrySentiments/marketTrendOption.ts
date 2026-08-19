/**
 * Build the ECharts option for the Market Trend view.
 *
 * buildMarketTrendOption — the combined overview plot (sole chart).
 *   All four broad-market indices' closes rebased to 100 at the window
 *   start, plotted as lines on the left axis, PLUS each index's trading
 *   amount embedded as stacked bars on a right axis (the stack height is
 *   the aggregate capital flow; each segment is one index's proportional
 *   share). A `visibleCodes` filter controls which indices are drawn.
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import type { IndexBaselineRow } from "@shared/types";
import {
  axisColors,
  commonLegend,
  commonGrid,
  commonDataZoom,
} from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import React from "react";
import { renderReactElement, tooltipComponents } from "@/lib/react-tooltip-renderer";
import { MARKET_TREND_INDICES } from "./constants";

/** One index's daily baseline rows prepared for plotting. */
interface IndexSeriesData {
  code: string;
  name: string;
  color: string;
  rows: IndexBaselineRow[];
}

/**
 * Build the combined Market Trend overview chart (sole plot).
 *
 * Close lines (rebased to 100 at visible-window start, left axis) for each
 * selected index + trading amount stacked bars (right axis, bottom) when
 * `showAmt` is true. The stack height is the sum of the selected indices'
 * trading amounts (亿元); each coloured segment shows that index's
 * proportional contribution.
 *
 * @param allDates     Sorted union of all indices' dates.
 * @param datasets     One IndexSeriesData per index (rows aligned inside).
 * @param visibleCodes Codes to actually draw. Indices not in this list are
 *                     omitted from both the close lines and the stacked bars.
 * @param themeMode    Current theme.
 * @param showAmt      When false, the trading amount stacked bars (and their
 *                     right y-axis / tooltip rows / legend entries) are
 *                     omitted, leaving only the close lines.
 */
export function buildMarketTrendOption(
  allDates: string[],
  datasets: IndexSeriesData[],
  visibleCodes: string[],
  themeMode: ThemeMode,
  showAmt: boolean = true,
): EChartsOption {
  const c = axisColors(themeMode);
  const visible = datasets.filter((ds) => visibleCodes.includes(ds.code));

  // --- Close lines (rebased to 100 at window start) -------------------
  // Build per-index raw close aligned to allDates, then rebase each to 100
  // at its first non-null close within the window.
  const closeSeries = visible.map((ds) => {
    const closeByDate = new Map<string, number | null>();
    for (const r of ds.rows) closeByDate.set(r.date, r.close);
    const rawCloses: Array<number | null> = allDates.map(
      (d) => closeByDate.get(d) ?? null,
    );
    // Rebase to 100 at first valid close.
    let base: number | null = null;
    for (const v of rawCloses) {
      if (v != null && Number.isFinite(v) && Math.abs(v) > 1e-9) {
        base = v;
        break;
      }
    }
    const rebased: Array<number | null> = base == null
      ? rawCloses.map(() => null)
      : rawCloses.map((v) =>
          v == null || !Number.isFinite(v) ? null : (v / base) * 100,
        );
    return { name: ds.name, color: ds.color, rebased };
  });

  // --- Trading amount stacked bars (proportional aggregation) ----------
  // Per-index trading amount (亿元) aligned to allDates. Only built when
  // `showAmt` is true; otherwise the bar series, right y-axis, tooltip
  // amount rows, and legend entries are all omitted.
  const amtLookups = showAmt
    ? visible.map((ds) => {
        const m = new Map<string, number>();
        for (const r of ds.rows) {
          if (r.trading_amount != null && Number.isFinite(r.trading_amount)) {
            m.set(r.date, r.trading_amount / 1e8);
          }
        }
        return m;
      })
    : [];
  const amtSeriesData = amtLookups.map((lookup) =>
    allDates.map((d) => {
      const v = lookup.get(d);
      return v == null ? 0 : v;
    }),
  );
  // Per-date total for tooltip.
  const totalsByDate = showAmt
    ? allDates.map((_, i) =>
        amtSeriesData.reduce((sum, arr) => sum + (arr[i] ?? 0), 0),
      )
    : allDates.map(() => 0);

  // --- X-axis: year-month ticks (3-month interval) --------------------
  const displayMonths = new Set<string>();
  {
    const orderedMonths: string[] = [];
    const seen = new Set<string>();
    for (const d of allDates) {
      const ym = d.slice(0, 7);
      if (!seen.has(ym)) {
        seen.add(ym);
        orderedMonths.push(ym);
      }
    }
    for (let i = 0; i < orderedMonths.length; i += 3) {
      displayMonths.add(orderedMonths[i]);
    }
  }
  const firstDateOfMonth = new Set<string>();
  {
    let prev = "";
    for (const d of allDates) {
      const ym = d.slice(0, 7);
      if (ym !== prev) {
        firstDateOfMonth.add(d);
        prev = ym;
      }
    }
  }

  // --- Series: stacked bars first (z=1), then close lines (z=3) -------
  const series: EChartsOption["series"] = [
    // Trading amount stacked bars (one bar series per visible index).
    // Omitted entirely when `showAmt` is false.
    ...(showAmt
      ? visible.map((ds, di) => ({
          name: `${ds.name} Amt`,
          type: "bar" as const,
          stack: "market_trend_amt",
          yAxisIndex: 1,
          data: amtSeriesData[di],
          itemStyle: { color: ds.color, opacity: 0.35 },
          emphasis: { focus: "series" as const },
          barWidth: "90%",
          z: 1 + di,
        }))
      : []),
    // Close lines (rebased to 100).
    ...closeSeries.map((s, i) => ({
      name: s.name,
      type: "line" as const,
      smooth: false,
      showSymbol: false,
      yAxisIndex: 0,
      data: s.rebased,
      lineStyle: { width: 2, color: s.color },
      itemStyle: { color: s.color },
      z: 10 + i,
    })),
  ];

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 56, right: 56, bottom: 50, top: 32 }),
    dataZoom: commonDataZoom(),
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross", snap: true },
      backgroundColor: c.tooltipBg,
      borderColor: c.splitLineColor,
      textStyle: { color: c.textColor, fontSize: 11 },
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          dataIndex?: number;
          axisValue?: string;
          marker?: string;
          seriesName?: string;
          value?: number | null;
        }>;
        if (arr.length === 0) return "";
        const idx0 = arr[0].dataIndex ?? 0;
        const dateStr = allDates[idx0] ?? (arr[0].axisValue as string) ?? "";
        if (!dateStr) return "";
        const total = totalsByDate[idx0] ?? 0;
        const children: React.ReactNode[] = [];
        children.push(
          React.createElement(tooltipComponents.Header, null, dateStr)
        );
        if (showAmt && total > 0) {
          children.push(
            React.createElement("div", { style: { marginTop: 2, opacity: 0.7 } },
              `Total Amt: ${fmtNum(total)} 亿`
            )
          );
        }
        const closeRowElements: React.ReactNode[] = [];
        const amtRowElements: React.ReactNode[] = [];
        for (const p of arr) {
          const name = p.seriesName ?? "";
          const v = p.value;
          if (v == null || !Number.isFinite(v)) continue;
          if (name.endsWith(" Amt")) {
            const pct = total > 0 ? (v / total) * 100 : 0;
            amtRowElements.push(
              React.createElement("div", null,
                p.marker ?? "",
                ` ${name.replace(" Amt", "")}: `,
                React.createElement(tooltipComponents.Bold, null, `${fmtNum(v)} 亿`),
                ` (${fmtNum(pct, 1)}%)`
              )
            );
          } else {
            const pct = v - 100;
            closeRowElements.push(
              React.createElement("div", null,
                p.marker ?? "",
                ` ${name}: `,
                React.createElement(tooltipComponents.Bold, {
                  style: { color: p.marker?.includes("color") ? "" : "" }
                }, `${pct >= 0 ? "+" : ""}${fmtNum(pct, 2)}%`)
              )
            );
          }
        }
        if (closeRowElements.length) {
          children.push(
            React.createElement("div", { style: { marginTop: 4 } }, ...closeRowElements)
          );
        }
        if (showAmt && amtRowElements.length) {
          children.push(
            React.createElement("div", { style: { marginTop: 4, opacity: 0.85 } }, ...amtRowElements)
          );
        }
        return renderReactElement(React.createElement(React.Fragment, null, ...children));
      },
    },
    legend: commonLegend(themeMode, {
      data: [
        ...closeSeries.map((s) => s.name),
        ...(showAmt ? visible.map((ds) => `${ds.name} Amt`) : []),
      ],
    }),
    xAxis: {
      type: "category",
      data: allDates,
      boundaryGap: true,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        interval: (_idx: number, value: string) =>
          displayMonths.has(value.slice(0, 7)) && firstDateOfMonth.has(value),
        formatter: (v: string) => v.slice(0, 7),
      },
      splitLine: { show: false },
    },
    yAxis: [
      {
        type: "value",
        scale: true,
        name: "Rebased (start = 100)",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => fmtNum(v, 0),
        },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
      // Right axis only included when trading amount bars are shown.
      ...(showAmt
        ? [{
            type: "value" as const,
            scale: true,
            name: "Trading Amt (亿)",
            nameTextStyle: { color: c.textColor, fontSize: 9 },
            axisLine: { lineStyle: { color: c.axisLineColor } },
            axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: number) => fmtNum(v) },
            splitLine: { show: false },
          }]
        : []),
    ],
    series,
  };
}

/** Build the IndexSeriesData view used by the option builder. */
export function toIndexSeriesData(
  code: string,
  rows: IndexBaselineRow[],
): IndexSeriesData {
  const meta = MARKET_TREND_INDICES.find((m) => m.code === code);
  return {
    code,
    name: meta?.name ?? code,
    color: meta?.color ?? "#2980b9",
    rows,
  };
}
