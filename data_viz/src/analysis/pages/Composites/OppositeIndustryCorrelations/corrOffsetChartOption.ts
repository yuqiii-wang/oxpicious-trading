/**
 * Chart option for the Opposite Industry Correlations page — one line per
 * industry pair on the selected metric (Overall / Offset / Opposite) and
 * window (20d/60d/255d), with an axis tooltip that shows ALL three windows'
 * values per pair (the selected one bolded).
 */
import React from "react";
import type { EChartsOption } from "echarts";
import { baseChartOption } from "@/shared/charts/base-chart";
import type { ThemeMode } from "@/store/filters";
import {
  MUTED_PALETTE,
  axisColors,
  commonLegend,
  commonGrid,
} from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import { renderReactElement, tooltipComponents } from "@/lib/react-tooltip-renderer";
import type { IndustryCorrOffsetRow, IndustryCorrOffsetsResponse } from "@shared/types";
import { METRIC_COLS, METRIC_LABELS, pairLabel, rowVal } from "./constants";
import type { CorrWindow, OffsetMetric } from "./constants";

export function buildCorrOffsetChartOption(
  data: IndustryCorrOffsetsResponse,
  mode: ThemeMode,
  metric: OffsetMetric,
  win: CorrWindow,
): EChartsOption | null {
  if (data.offsets.length === 0) return null;
  const c = axisColors(mode);

  const byPair = new Map<string, IndustryCorrOffsetRow[]>();
  const pairKeys = new Set<string>();
  for (const row of data.offsets) {
    const key = `${row.industry_id}\u0000${row.benchmark_industry_id}`;
    pairKeys.add(key);
    let arr = byPair.get(key);
    if (!arr) { arr = []; byPair.set(key, arr); }
    arr.push(row);
  }
  const sortedPairs = Array.from(pairKeys).sort();
  const allDatesSet = new Set<string>();
  for (const row of data.offsets) allDatesSet.add(row.start_date);
  const allDates = Array.from(allDatesSet).sort();

  const col = METRIC_COLS[metric][win];
  const series: Array<Record<string, unknown>> = sortedPairs.map((key, i) => {
    const rows = byPair.get(key)!;
    const byDate = new Map(rows.map((r) => [r.start_date, r]));
    const color = MUTED_PALETTE[i % MUTED_PALETTE.length];
    const aligned = allDates.map((d) => rowVal(byDate.get(d) ?? rows[0], col));
    return {
      name: pairLabel(rows[0]),
      type: "line",
      smooth: false,
      showSymbol: false,
      connectNulls: false,
      data: aligned,
      lineStyle: { width: 1.6, color },
      itemStyle: { color },
      z: 3,
    };
  });

  const isScore = metric === "score";
  // The tooltip is a plain themed card WITHOUT an axisPointer, so it is
  // passed as an explicit override rather than commonTooltip(mode, …) —
  // the kit default would introduce a cross+snap pointer this chart never
  // had, changing the hover interaction.
  return baseChartOption(mode, {
    grid: commonGrid({ left: 56, right: 24, bottom: 32 }),
    legend: commonLegend(mode, {
      data: series.map((s) => s.name as string),
    }),
    tooltip: {
      trigger: "axis",
      backgroundColor: c.tooltipBg,
      borderColor: c.splitLineColor,
      textStyle: { color: c.textColor, fontSize: 11 },
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          dataIndex?: number;
          seriesName?: string;
        }>;
        if (arr.length === 0) return "";
        const idx0 = arr[0].dataIndex ?? 0;
        const dateStr = allDates[idx0] ?? "";
        if (!dateStr) return "";
        const children: React.ReactNode[] = [];
        children.push(React.createElement(tooltipComponents.Header, null, dateStr));
        children.push(React.createElement("div", {
          style: { marginTop: 2, opacity: 0.7 },
        }, `${METRIC_LABELS[metric]} · ${win} window · benchmark ${data.benchmark_code} (window starts every ${data.offsets[0]?.interval ?? 20} trading days)`));
        const rowChildren: React.ReactNode[] = [];
        const col = METRIC_COLS[metric];
        for (const p of arr) {
          const key = sortedPairs.find((k) => {
            const rows0 = byPair.get(k);
            return rows0 && rows0.length > 0 && pairLabel(rows0[0]) === p.seriesName;
          });
          if (!key) continue;
          const rows = byPair.get(key)!;
          const r = rows.find((x) => x.start_date === dateStr);
          if (!r) continue;
          const pairIdx = sortedPairs.indexOf(key);
          const color = MUTED_PALETTE[pairIdx % MUTED_PALETTE.length];
          const makeChip = (w: CorrWindow, v: number | null) => {
            const isSel = w === win;
            const style: React.CSSProperties = isSel
              ? { fontWeight: 700 }
              : { opacity: 0.55, fontSize: "0.85em" };
            return React.createElement("span", { style },
              `${w}:${v == null || !Number.isFinite(v) ? "—" : fmtNum(v, 3)}`,
            );
          };
          rowChildren.push(React.createElement("div", {
            style: { display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" },
          },
            React.createElement("span", { style: { color } }, "●"),
            React.createElement("span", { style: { flex: 1 } }, p.seriesName ?? ""),
            React.createElement("span", { style: { display: "flex", gap: 6, alignItems: "baseline" } },
              makeChip("20d", rowVal(r, col["20d"])),
              makeChip("60d", rowVal(r, col["60d"])),
              makeChip("255d", rowVal(r, col["255d"])),
            ),
          ));
        }
        children.push(React.createElement("div", { style: { marginTop: 4 } }, rowChildren));
        return renderReactElement(React.createElement(React.Fragment, null, children));
      },
    },
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
      min: isScore ? 0 : -1,
      max: 1,
      name: isScore ? "Opposite score" : "Correlation",
      nameTextStyle: { color: c.textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        formatter: (v: number) => fmtNum(v, 2),
      },
      splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
    },
    series,
  });
}
