/**
 * buildCorrelationChartOption — the second plot: per-contract rolling
 * correlation vs the underlying (active + matured contracts), y-axis fixed
 * at -1 to 1, using the exact same per-contract colors/opacities as the
 * main price plot.
 */
import React from "react";
import type { EChartsOption } from "echarts";
import { computeFuturesContractStyles } from "@/dataviz/features/futures/chartOption";
import type { FuturesCombinedResponse } from "@shared/types";
import {
  AXIS_POINTER_LINE,
  TOOLTIP_CARD_BG,
  TOOLTIP_CARD_BORDER,
  TOOLTIP_CARD_TEXT,
} from "@/theme/chart-palette";
import { renderReactElement, tooltipComponents } from "@/lib/react-tooltip-renderer";

type CorrMap = Map<string, Map<string, number | null>>;

export function buildCorrelationChartOption(
  combinedData: FuturesCombinedResponse,
  corrMap: CorrMap,
  viewMode: "future" | "history",
  zoom: { start: number; end: number },
): EChartsOption {
  const { dates } = combinedData;
  const { qualifying, matured, maturedCodeSet, styleByCode } =
    computeFuturesContractStyles(combinedData, viewMode, zoom);

  const mkSeries = (code: string) => {
    const st = styleByCode.get(code)!;
    const dm = corrMap.get(code);
    const data = dates.map((d) => {
      const v = dm?.get(d);
      return v != null && Number.isFinite(v) ? v : null;
    });
    return {
      name: code,
      type: "line" as const,
      showSymbol: false,
      connectNulls: true,
      sampling: "lttb" as const,
      data,
      itemStyle: { color: st.color },
      lineStyle: {
        width: st.lineWidth,
        color: st.color,
        opacity: st.opacity,
      },
      z: st.isActive ? 10 : 5,
    };
  };

  // Series order MUST match the price plot ([...qualifying, ...matured]):
  // echarts.connect cross-chart tooltip sync passes (seriesIndex, dataIndex)
  // of the source chart's first involved series, and the receiving chart
  // resolves the point from its own series at the same index — so a
  // different order would sample the wrong contract (often with null corr
  // → stale tooltip). z-levels still put matured behind the active blues.
  const series = [
    ...qualifying.map((c) => mkSeries(c.code)),
    ...matured.map((c) => mkSeries(c.code)),
  ];

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: { left: 60, right: 24, top: 24, bottom: 60 },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line", lineStyle: { color: AXIS_POINTER_LINE, type: "dashed" } },
      confine: true,
      backgroundColor: TOOLTIP_CARD_BG,
      borderColor: TOOLTIP_CARD_BORDER,
      borderWidth: 1,
      textStyle: { color: TOOLTIP_CARD_TEXT, fontSize: 11 },
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          dataIndex?: number;
          axisValue?: string;
          seriesName?: string;
          value?: number | null;
          color?: string;
        }>;
        if (arr.length === 0) return "";
        const dateStr = arr[0].axisValue ?? dates[arr[0].dataIndex ?? 0] ?? "";
        const filtered = arr.filter((p) => {
          const v = Array.isArray(p.value) ? p.value[1] : p.value;
          if (v == null || !Number.isFinite(v as number)) return false;
          if (viewMode !== "history" && maturedCodeSet.has(p.seriesName ?? "")) return false;
          return true;
        });
        const children: React.ReactNode[] = [];
        children.push(React.createElement(tooltipComponents.Header, null, dateStr));
        for (const p of filtered) {
          const v = (Array.isArray(p.value) ? p.value[1] : p.value) as number;
          const valStr = (v >= 0 ? "+" : "") + v.toFixed(4);
          children.push(React.createElement(tooltipComponents.Row, null, [
            React.createElement("span", { style: { color: p.color ?? "" } }, "●"),
            ` ${p.seriesName ?? ""}: `,
            React.createElement(tooltipComponents.Bold, null, valStr),
          ]));
        }
        return renderReactElement(React.createElement(React.Fragment, null, children));
      },
    },
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: "#ddd" } },
      axisLabel: { fontSize: 11, color: "#888" },
      splitLine: { show: false },
      boundaryGap: false,
    },
    yAxis: {
      type: "value",
      min: -1,
      max: 1,
      name: "Correlation",
      nameTextStyle: { fontSize: 10, color: "#888" },
      axisLine: { lineStyle: { color: "#ddd" } },
      axisLabel: { fontSize: 11, color: "#888" },
      splitLine: { lineStyle: { color: "#eee", type: "dashed", opacity: 0.4 } },
    },
    dataZoom: [
      {
        type: "inside",
        start: zoom.start,
        end: zoom.end,
        zoomLock: false,
      },
      {
        type: "slider",
        height: 18,
        bottom: 10,
        start: zoom.start,
        end: zoom.end,
      },
    ],
    series,
  };
}
