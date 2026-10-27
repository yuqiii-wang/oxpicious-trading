/**
 * convergenceChart — basis-convergence ("contrarian") plot builder.
 *
 * One line per contract: the signed basis gap
 * (gap_price_vs_underlying, in bps) between futures and its underlying
 * (spot). The emphasized zero line is the convergence target — as a
 * contract ages toward expiry its line visibly collapses into zero,
 * making the spot-vs-futures deviation (and its decay) obvious.
 *
 * Expiry markers are HOVER-DRIVEN (see the analysis page's expiryDots
 * module): every contract LIVE on the hovered date gets one dot ANCHORED AT
 * its expiry date, on its gap curve's endpoint there (the convergence
 * endpoint), labelled with the expiry date and the final gap. This module
 * just hosts the empty scatter series that the hook pushes data into
 * (merge by id).
 *
 * Colors/order/dataZoom follow computeFuturesContractStyles so the
 * plot matches the price and correlation charts exactly.
 */
import React from "react";
import type { EChartsOption, SeriesOption } from "echarts";
import {
  baseChartOption,
  commonTooltip,
} from "@/shared/charts/base-chart";
import type { ThemeMode } from "@/store/filters";
import {
  AXIS_POINTER_LINE,
  FUTURES_EXPIRY_DOT,
  FUTURES_EXPIRY_DOT_BORDER,
  TOOLTIP_CARD_BG,
  TOOLTIP_CARD_BORDER,
  TOOLTIP_CARD_TEXT,
} from "@/theme/chart-palette";
import { renderReactElement, tooltipComponents } from "@/lib/react-tooltip-renderer";
import type { FuturesCombinedResponse } from "@shared/types";
import { computeFuturesContractStyles } from "./contractStyles";
import { buildExpiryDotsSeriesData } from "./expiryDots";
import { EXPIRY_DOTS_SERIES_ID, EXPIRY_DOTS_SERIES_NAME } from "./types";
import type { ExpiryDot, ExpiryDotDataItem, ViewMode, ZoomRange } from "./types";

export function buildBasisConvergenceChartOption(
  mode: ThemeMode,
  data: FuturesCombinedResponse,
  gapByCodeDate: Map<string, Map<string, number | null>>,
  viewMode: ViewMode,
  zoomRange?: ZoomRange,
  convDotsRef?: { current: ExpiryDot[] },
): EChartsOption | null {
  const { dates } = data;
  if (!dates.length) return null;

  const { styleByCode, qualifying, matured, maturedCodeSet, rowByCodeDate } =
    computeFuturesContractStyles(data, viewMode, zoomRange);

  const mkSeries = (code: string) => {
    const st = styleByCode.get(code)!;
    const dm = gapByCodeDate.get(code);
    const rowData = rowByCodeDate.get(code);

    const dataItems = dates.map((d) => {
      const g = dm?.get(d);
      if (g == null || !Number.isFinite(g)) return null;
      const bps = g * 1e4;
      // Object item so the tooltip formatter can read signed bps + the
      // row's days_to_expiry (true days remaining on that date).
      const dte = rowData?.get(d)?.days_to_expiry ?? null;
      return { value: bps, signed: bps, dte };
    });

    return {
      name: code,
      type: "line" as const,
      showSymbol: false,
      connectNulls: true,
      sampling: "lttb" as const,
      data: dataItems,
      itemStyle: { color: st.color },
      lineStyle: {
        width: st.lineWidth,
        color: st.color,
        opacity: st.opacity,
      },
      z: st.isActive ? 10 : 5,
    };
  };

  // Series order MUST match the price + correlation plots
  // ([...qualifying, ...matured]) — see the correlation chart note.
  const series: SeriesOption[] = [
    ...qualifying.map((c) => mkSeries(c.code)),
    ...matured.map((c) => mkSeries(c.code)),
  ];

  // Hover-fed expiry/yesterday dots (data pushed by useExpiryDots; merge by
  // id keeps this series config while only the data swaps on hover).
  if (convDotsRef) {
    series.push({
      id: EXPIRY_DOTS_SERIES_ID,
      name: EXPIRY_DOTS_SERIES_NAME,
      type: "scatter",
      symbolSize: 10,
      symbol: "circle",
      data: buildExpiryDotsSeriesData(convDotsRef.current),
      itemStyle: {
        color: FUTURES_EXPIRY_DOT,
        borderColor: FUTURES_EXPIRY_DOT_BORDER,
        borderWidth: 2,
      },
      label: {
        show: true,
        position: "top",
        distance: 6,
        fontSize: 10,
        fontWeight: 600,
        lineHeight: 13,
        color: FUTURES_EXPIRY_DOT,
        textBorderColor: FUTURES_EXPIRY_DOT_BORDER,
        textBorderWidth: 2,
        formatter: (p: unknown) => {
          const dot = (p as { data?: ExpiryDotDataItem }).data?.dot;
          if (!dot) return "";
          const bps = dot.value;
          const valStr =
            bps != null && Number.isFinite(bps)
              ? `${bps >= 0 ? "+" : ""}${bps.toFixed(1)} bps`
              : "";
          return `${dot.code} expiry ${dot.expiryDate}\n${valStr}`;
        },
      },
      z: 30,
    });
  }

  return baseChartOption(mode, {
    // No legend: per-contract identity is read from the hover tooltip.
    legend: null,
    grid: { left: 60, right: 24, top: 24, bottom: 60 },
    tooltip: commonTooltip(mode, {
      // Line (not cross) pointer; the React-element formatter below draws
      // the shared tooltip-card rows (signed bps + days to expiry).
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
          data?: { signed?: number; dte?: number | null };
        }>;
        if (arr.length === 0) return "";
        const dateStr = arr[0].axisValue ?? dates[arr[0].dataIndex ?? 0] ?? "";
        const filtered = arr.filter((p) => {
          // Skip the hover-fed dots series — it carries no per-date value.
          if (p.seriesName === EXPIRY_DOTS_SERIES_NAME) return false;
          const v = Array.isArray(p.value) ? p.value[1] : p.value;
          if (v == null || !Number.isFinite(v as number)) return false;
          if (viewMode !== "history" && maturedCodeSet.has(p.seriesName ?? "")) return false;
          return true;
        });
        const children: React.ReactNode[] = [];
        children.push(React.createElement(tooltipComponents.Header, null, dateStr));
        for (const p of filtered) {
          const signed = p.data?.signed;
          const dte = p.data?.dte;
          const valStr =
            signed != null && Number.isFinite(signed)
              ? `${signed >= 0 ? "+" : ""}${signed.toFixed(1)} bps`
              : "";
          const dteStr = dte != null && Number.isFinite(dte) ? ` · ${Math.round(dte)}d to expiry` : "";
          children.push(React.createElement(tooltipComponents.Row, null, [
            React.createElement("span", { style: { color: p.color ?? "" } }, "●"),
            ` ${p.seriesName ?? ""}: `,
            React.createElement(tooltipComponents.Bold, null, valStr),
            dteStr,
          ]));
        }
        return renderReactElement(React.createElement(React.Fragment, null, children));
      },
    }),
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
      name: "Basis gap (bps)",
      nameTextStyle: { fontSize: 10, color: "#888" },
      axisLine: { lineStyle: { color: "#ddd" } },
      axisLabel: { fontSize: 11, color: "#888" },
      splitLine: { lineStyle: { color: "#eee", type: "dashed", opacity: 0.4 } },
    },
    // The zero line (silent + excluded from tooltip) is the convergence
    // target — futures price meeting spot. Placed after the contract lines
    // so those indices keep matching the price/correlation plots; the dots
    // scatter comes last (tooltip skips it by name).
    series: [
      ...series,
      {
        name: "__zero_line__",
        type: "line" as const,
        data: dates.map(() => 0),
        showSymbol: false,
        silent: true,
        lineStyle: { width: 1.5, color: "#999", type: "solid", opacity: 0.9 },
        z: 8,
        tooltip: { show: false },
      },
    ],
    dataZoom: [
      {
        type: "inside",
        start: zoomRange?.start ?? 0,
        end: zoomRange?.end ?? 100,
        zoomLock: false,
      },
      {
        type: "slider",
        height: 18,
        bottom: 10,
        start: zoomRange?.start ?? 0,
        end: zoomRange?.end ?? 100,
      },
    ],
  });
}
