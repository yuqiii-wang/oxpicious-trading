/**
 * ECharts option builder for the Futures top plot.
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
import React from "react";
import { createRoot, type Root } from "react-dom/client";
import { flushSync } from "react-dom";
import type { EChartsOption } from "echarts";
import {
  FUTURES_BLUE_NEAR,
  FUTURES_BLUE_FAR,
  FUTURES_GREY_DARK,
  FUTURES_GREY_LIGHT,
  FUTURES_SPOT,
  FUTURES_GHOST_OPACITY,
  FUTURES_HISTORY_OPACITY,
} from "@/theme/chart-palette";
import FuturesTooltip, { type TooltipItem } from "./FuturesTooltip";
import type {
  FuturesCombinedResponse,
  FuturesContractMeta,
  FuturesRow,
} from "../../../../shared/types";

type ViewMode = "future" | "history";

const SPOT_LINE_WIDTH = 3;
const ACTIVE_LINE_WIDTH = 2;
const MATURED_LINE_WIDTH = 1;
const MATURED_LINE_WIDTH_HISTORY = 2;

export interface ZoomRange {
  start: number;
  end: number;
}

export interface FuturesChartExtra {
  /** date -> code -> gap_price_vs_underlying */
  gapByCodeDate: Map<string, Map<string, number | null>>;
}

/**
 * Build the grey color for a matured contract at the given index.
 * Uses a power curve (exponent=0.5) so recently matured contracts stay
 * darker for longer, ensuring visible contrast even with few visible
 * contracts in a zoomed-in view.
 */
function greyColorFor(idx: number, total: number): string {
  if (total <= 1) return FUTURES_GREY_DARK;
  const t = idx / (total - 1);
  const adjustedT = Math.pow(t, 0.5);
  return lerpColor(FUTURES_GREY_DARK, FUTURES_GREY_LIGHT, adjustedT);
}

/** Per-contract visual style (shared by the price plot and any companion
 *  plots so contract colors are identical across charts). */
export interface FuturesContractStyle {
  color: string;
  opacity: number;
  lineWidth: number;
  /** true for alive+continuous (blue family), false for matured (grey) */
  isActive: boolean;
}

export interface FuturesContractStyles {
  styleByCode: Map<string, FuturesContractStyle>;
  /** Alive + continuous contracts, sorted by contract_year_month asc. */
  qualifying: FuturesContractMeta[];
  /** Matured contracts, sorted by last_date desc (most recent first). */
  matured: FuturesContractMeta[];
  maturedCodeSet: Set<string>;
  /** (code → date → row) index for quick settlement lookups. */
  rowByCodeDate: Map<string, Map<string, FuturesRow>>;
}

/**
 * Compute per-contract colors/opacity/width shared by all futures charts
 * (price curves + correlation). This is the single source of truth for the
 * blue (active) / grey (matured) gradient scheme, so companion plots render
 * with exactly the same colors as the main Futures Price Curves plot.
 */
export function computeFuturesContractStyles(
  data: FuturesCombinedResponse,
  viewMode: ViewMode,
  zoomRange?: ZoomRange,
): FuturesContractStyles {
  const { dates, contracts, rows } = data;

  // Index rows by (code → date → row) for quick lookup
  const rowByCodeDate = new Map<string, Map<string, FuturesRow>>();
  for (const r of rows) {
    if (!rowByCodeDate.has(r.code)) rowByCodeDate.set(r.code, new Map());
    rowByCodeDate.get(r.code)!.set(r.date, r);
  }

  // Split contracts by category
  const qualifying = contracts.filter(
    (c) => c.is_alive && c.is_continuous,
  );
  const matured = contracts.filter((c) => !c.is_alive);

  // Sort qualifying by contract_year_month ascending (front month first)
  qualifying.sort((a, b) => a.contract_year_month.localeCompare(b.contract_year_month));
  // Matured sort by last_date desc (most recently matured first)
  matured.sort((a, b) => b.last_date.localeCompare(a.last_date));

  // Determine visible date range from zoom percentages
  const totalDates = dates.length;
  let visibleStartIdx = 0;
  let visibleEndIdx = totalDates - 1;
  if (zoomRange) {
    visibleStartIdx = Math.floor((zoomRange.start / 100) * totalDates);
    visibleEndIdx = Math.ceil((zoomRange.end / 100) * totalDates);
  }
  const visibleDates = dates.slice(visibleStartIdx, visibleEndIdx + 1);

  // Filter matured contracts to those active in the visible date range
  let gradientMatured = matured;
  if (zoomRange && (zoomRange.start > 0 || zoomRange.end < 100)) {
    const visibleMatured = matured.filter((c) => {
      const codeRows = rowByCodeDate.get(c.code);
      if (!codeRows) return false;
      for (const d of visibleDates) {
        if (codeRows.has(d)) return true;
      }
      return false;
    });
    if (visibleMatured.length >= 2) {
      gradientMatured = visibleMatured;
    }
  }

  // Compute blue gradient for qualifying contracts
  const nQualifying = qualifying.length;
  const blueFor = (idx: number) => {
    if (nQualifying <= 1) return FUTURES_BLUE_NEAR;
    const t = idx / (nQualifying - 1);
    return lerpColor(FUTURES_BLUE_NEAR, FUTURES_BLUE_FAR, t);
  };

  // Compute grey gradient for matured contracts
  const nMatured = gradientMatured.length;
  const maturedIdxMap = new Map<string, number>();
  gradientMatured.forEach((c, idx) => maturedIdxMap.set(c.code, idx));

  const maturedOpacity = viewMode === "history" ? FUTURES_HISTORY_OPACITY : FUTURES_GHOST_OPACITY;
  const maturedLineWidth = viewMode === "history" ? MATURED_LINE_WIDTH_HISTORY : MATURED_LINE_WIDTH;

  const styleByCode = new Map<string, FuturesContractStyle>();
  qualifying.forEach((c, idx) => {
    styleByCode.set(c.code, {
      color: blueFor(idx),
      opacity: 1,
      lineWidth: ACTIVE_LINE_WIDTH,
      isActive: true,
    });
  });
  matured.forEach((c) => {
    const gradIdx = maturedIdxMap.get(c.code);
    styleByCode.set(c.code, {
      color: gradIdx != null
        ? greyColorFor(gradIdx, nMatured)
        : FUTURES_GREY_LIGHT,
      opacity: maturedOpacity,
      lineWidth: maturedLineWidth,
      isActive: false,
    });
  });

  return {
    styleByCode,
    qualifying,
    matured,
    maturedCodeSet: new Set(matured.map((c) => c.code)),
    rowByCodeDate,
  };
}

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
      axisPointer: { type: "line", lineStyle: { color: "#999", type: "dashed" } },
      confine: true,
      backgroundColor: "transparent",
      borderColor: "transparent",
      borderWidth: 0,
      padding: 0,
      textStyle: { color: "#333", fontSize: 12 },
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
          };
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

// ----------------------------------------------------------------------------
//  Helpers
// ----------------------------------------------------------------------------

function lerpColor(hexA: string, hexB: string, t: number): string {
  const [r1, g1, b1] = hexToRgb(hexA);
  const [r2, g2, b2] = hexToRgb(hexB);
  const r = Math.round(r1 + (r2 - r1) * t);
  const g = Math.round(g1 + (g2 - g1) * t);
  const b = Math.round(b1 + (b2 - b1) * t);
  return `rgb(${r}, ${g}, ${b})`;
}

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  return [
    parseInt(h.substring(0, 2), 16),
    parseInt(h.substring(2, 4), 16),
    parseInt(h.substring(4, 6), 16),
  ];
}
