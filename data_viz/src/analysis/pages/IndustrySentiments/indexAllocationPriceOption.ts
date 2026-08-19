/**
 * Build the ECharts option for the Index Allocation view's TOP close-price
 * plot.
 *
 * One line per selected member index, rebased to 100 at the start of the
 * visible (zoom) window so indices with different absolute price levels
 * (e.g. CSI 300 ~3500 vs a small index ~1000) are comparable on a common
 * scale. Tooltip shows the rebased % AND the actual raw close per index.
 *
 * Deliberately PLAIN — no trading-amount bars, no MA overlay, no cascading
 * rebase (unlike the ETF Contribution plot). This is an overview of the
 * selected indices' close-price trends; the per-index attribution detail
 * lives in the PerfAttrPanel cards below the plot.
 */
import React from "react";
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import {
  axisColors,
  commonLegend,
  commonGrid,
  commonDataZoom,
} from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import { rebaseTo100 } from "./helpers";
import { renderReactElement } from "@/lib/react-tooltip-renderer";

/** One index's raw close series prepared for plotting (aligned to dates). */
export interface IndexAllocationSeries {
  code: string;
  name: string;
  color: string;
  /** Raw daily close aligned to the shared date axis (null = no data). */
  closes: Array<number | null>;
}

/**
 * @param allDates  Sorted union of all indices' dates (shared x-axis).
 * @param indices   One IndexAllocationSeries per selected index.
 * @param lo        Visible-window start index (inclusive).
 * @param hi        Visible-window end index (inclusive).
 * @param themeMode Current theme.
 */
export function buildIndexAllocationPriceOption(
  allDates: string[],
  indices: IndexAllocationSeries[],
  lo: number,
  hi: number,
  themeMode: ThemeMode,
): EChartsOption {
  const c = axisColors(themeMode);

  // Rebase each index to 100 at the first non-null close within [lo, hi].
  const rebasedSeries = indices.map((idx) => ({
    name: idx.name,
    color: idx.color,
    raw: idx.closes,
    rebased: rebaseTo100(idx.closes, lo, hi),
  }));

  // X-axis: year-month ticks at a 3-month interval (first date of each
  // displayed month). Keeps the axis readable across long histories.
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

  const series: EChartsOption["series"] = rebasedSeries.map((s, i) => ({
    name: s.name,
    type: "line",
    smooth: false,
    showSymbol: false,
    data: s.rebased,
    lineStyle: { width: 1.5, color: s.color },
    itemStyle: { color: s.color },
    z: 10 + i,
  }));

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 56, right: 24, bottom: 50, top: 32 }),
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
          marker?: string;
          seriesName?: string;
          value?: number | null;
        }>;
        if (arr.length === 0) return "";
        const idx0 = arr[0].dataIndex ?? 0;
        const dateStr = allDates[idx0] ?? "";
        if (!dateStr) return "";
        const children: React.ReactNode[] = [];
        children.push(React.createElement("div", { style: { fontWeight: 600 } }, dateStr));
        for (const p of arr) {
          const s = rebasedSeries.find((it) => it.name === p.seriesName);
          if (!s) continue;
          const rv = p.value;
          if (rv == null || typeof rv !== "number" || !Number.isFinite(rv)) continue;
          const raw = s.raw[idx0] ?? null;
          const pct = rv - 100;
          const lineChildren: React.ReactNode[] = [];
          if (p.marker) lineChildren.push(p.marker);
          lineChildren.push(` ${p.seriesName}: `);
          lineChildren.push(React.createElement("b", null, (pct >= 0 ? "+" : "") + fmtNum(pct, 2) + "%"));
          if (raw != null) {
            lineChildren.push(" ");
            lineChildren.push(React.createElement("span", { style: { opacity: 0.6 } }, `(${fmtNum(raw, 2)})`));
          }
          children.push(React.createElement("div", null, ...lineChildren));
        }
        return renderReactElement(React.createElement(React.Fragment, null, ...children));
      },
    },
    legend: commonLegend(themeMode, { data: rebasedSeries.map((s) => s.name) }),
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
    yAxis: {
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
    series,
  };
}
