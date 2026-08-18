import React from "react";
import { createRoot, type Root } from "react-dom/client";
import { flushSync } from "react-dom";
import type { EChartsOption } from "echarts";
import {
  FUTURES_SPOT,
  FUTURES_GREY_DARK,
  FUTURES_EXPIRY_DOT,
  FUTURES_EXPIRY_DOT_BORDER,
  AXIS_POINTER_LINE,
  TOOLTIP_CARD_TEXT,
} from "@/theme/chart-palette";
import FuturesTooltip, { type TooltipItem } from "../FuturesTooltip";
import type { FuturesCombinedResponse } from "@shared/types";
import { buildExpiryDotsSeriesData } from "./expiryDots";
import { computeFuturesContractStyles } from "./contractStyles";
import {
  type ViewMode,
  type ZoomRange,
  type FuturesChartExtra,
  type ExpiryDotDataItem,
  EXPIRY_DOTS_SERIES_ID,
  EXPIRY_DOTS_SERIES_NAME,
  SPOT_LINE_WIDTH,
} from "./types";

/**
 * Build the ECharts option for the Futures top plot.
 *
 * Blue gradient (active qualifying contracts — farther maturity = lighter).
 * Grey gradient (matured contracts — more recent = darker, older = lighter).
 * Underlying spot price overlaid as a bold orange line.
 *
 * Colors are sourced from the shared theme palette (chart-palette.ts) so that
 * all canvas-rendered charts stay in sync with colors.css.
 *
 * Tooltip renders a real React component (FuturesTooltip) synchronously via
 * `flushSync` — React 18's concurrent render is async, so without flushSync
 * ECharts would measure an empty container and show a blank tooltip.
 *
 * The tooltip only lists contracts that were actually TRADING on the hovered
 * date (null values are skipped), so at any date only the handful of active
 * contracts at that time appear. Matured (history) curves are listed only in
 * history mode.
 */
export function buildFuturesChartOption(
  data: FuturesCombinedResponse,
  viewMode: ViewMode,
  zoomRange?: ZoomRange,
  extra?: FuturesChartExtra,
): EChartsOption {
  const { dates } = data;

  const {
    styleByCode,
    qualifying,
    matured,
    maturedCodeSet,
    rowByCodeDate,
  } = computeFuturesContractStyles(data, viewMode, zoomRange);

  // For tooltip filtering: the spot series name
  const spotName = data.underlying_name || data.product_name;

  // Build dataZoom defaults (last 120 days by default for readability)
  const defaultZoomStart = Math.max(0, 100 - (120 / Math.max(dates.length, 1)) * 100);
  const zoomStart = zoomRange ? zoomRange.start : defaultZoomStart;
  const zoomEnd = zoomRange ? zoomRange.end : 100;

  // Pre-compute color map for tooltip: seriesName → color
  const colorMap = new Map<string, string>();

  const series = [] as unknown[];

  // --- Qualifying active contracts (blue gradient) ---
  qualifying.forEach((c) => {
    const st = styleByCode.get(c.code)!;
    colorMap.set(c.code, st.color);
    series.push({
      name: c.code,
      type: "line",
      showSymbol: false,
      connectNulls: true,
      sampling: "lttb",
      data: dates.map((d) => {
        const r = rowByCodeDate.get(c.code)?.get(d);
        return r ? r.settlement_price : null;
      }),
      itemStyle: { color: st.color },
      lineStyle: {
        width: st.lineWidth,
        color: st.color,
      },
      emphasis: {
        focus: "series",
        lineStyle: { width: st.lineWidth + 1 },
      },
      z: 10,
    });
  });

  // --- Matured contracts (grey gradient) ---
  matured.forEach((c) => {
    const st = styleByCode.get(c.code)!;
    colorMap.set(c.code, st.color);
    series.push({
      name: c.code,
      type: "line",
      showSymbol: false,
      connectNulls: true,
      data: dates.map((d) => {
        const r = rowByCodeDate.get(c.code)?.get(d);
        return r ? r.settlement_price : null;
      }),
      itemStyle: { color: st.color },
      lineStyle: {
        width: st.lineWidth,
        color: st.color,
        opacity: st.opacity,
      },
      z: 5,
    });
  });

  // --- Underlying spot price (index futures only) ---
  if (data.spot_price && data.spot_price.some((v) => v != null)) {
    colorMap.set(spotName, FUTURES_SPOT);
    series.push({
      name: spotName,
      type: "line",
      showSymbol: false,
      connectNulls: true,
      sampling: "lttb",
      data: data.spot_price,
      itemStyle: { color: FUTURES_SPOT },
      lineStyle: {
        width: SPOT_LINE_WIDTH,
        color: FUTURES_SPOT,
      },
      emphasis: {
        focus: "series",
        lineStyle: { width: SPOT_LINE_WIDTH + 1 },
      },
      z: 20,
    });
  }

  // --- Expiry dots (history mode, hover-triggered) ---
  if (viewMode === "history" && extra?.expiryDotsRef) {
    series.push({
      id: EXPIRY_DOTS_SERIES_ID,
      name: EXPIRY_DOTS_SERIES_NAME,
      type: "scatter",
      symbolSize: 10,
      symbol: "circle",
      data: buildExpiryDotsSeriesData(extra.expiryDotsRef.current),
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
          return `${dot.code}\n${dot.expiryDate}`;
        },
      },
      z: 30,
    });
  }

  // One reusable tooltip container/root — re-rendered synchronously on each
  // hover. flushSync forces React 18 to paint immediately so ECharts can
  // measure the final DOM size.
  let tooltipEl: HTMLDivElement | null = null;
  let tooltipRoot: Root | null = null;

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: {
      left: 60,
      right: 24,
      top: 24,
      bottom: 60,
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line", lineStyle: { color: AXIS_POINTER_LINE, type: "dashed" } },
      confine: true,
      backgroundColor: "transparent",
      borderColor: "transparent",
      borderWidth: 0,
      padding: 0,
      textStyle: { color: TOOLTIP_CARD_TEXT, fontSize: 12 },
      formatter: (params: unknown) => {
        if (!Array.isArray(params) || (params as unknown[]).length === 0) return "";
        const pArr = params as unknown[];

        const first = pArr[0] as { axisValue: string };
        const date = first.axisValue;

        const gapMap = extra?.gapByCodeDate;

        // Only list series that were actually TRADING on the hovered date —
        // at any point in time only a handful of contracts are active.
        const items: TooltipItem[] = [];
        for (const p of pArr) {
          const pp = p as {
            seriesName: string;
            value: (number | null)[] | number | null;
            dataIndex?: number;
          };

          // Skip the internal expiry dots series — it carries no per-date value.
          if (pp.seriesName === EXPIRY_DOTS_SERIES_NAME) continue;

          const rawVal = Array.isArray(pp.value) ? pp.value[1] : pp.value;
          if (rawVal == null || Number.isNaN(Number(rawVal))) continue;
          // Matured (history) curves only appear in history mode.
          if (viewMode !== "history" && maturedCodeSet.has(pp.seriesName)) continue;

          const gapVal = gapMap ? gapMap.get(pp.seriesName)?.get(date) ?? null : null;

          items.push({
            seriesName: pp.seriesName,
            value: Number(rawVal),
            color: colorMap.get(pp.seriesName) ?? FUTURES_GREY_DARK,
            isSpot: pp.seriesName === spotName,
            gap: gapVal,
          });
        }

        if (items.length === 0) return "";

        if (!tooltipEl) {
          tooltipEl = document.createElement("div");
          tooltipRoot = createRoot(tooltipEl);
        }
        flushSync(() => {
          tooltipRoot!.render(
            React.createElement(FuturesTooltip, { date, items }),
          );
        });
        return tooltipEl;
      },
    },
    xAxis: {
      type: "category",
      data: dates,
      boundaryGap: false,
      axisLabel: {
        rotate: 0,
        fontSize: 11,
        color: "#888",
      },
      axisLine: { lineStyle: { color: "#ddd" } },
      axisTick: { show: false },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { fontSize: 11, color: "#888" },
      splitLine: { lineStyle: { color: "#eee" } },
    },
    dataZoom: [
      {
        type: "inside",
        start: zoomStart,
        end: zoomEnd,
        zoomLock: false,
      },
      {
        type: "slider",
        height: 18,
        bottom: 10,
        start: zoomStart,
        end: zoomEnd,
      },
    ],
    series,
  };
}