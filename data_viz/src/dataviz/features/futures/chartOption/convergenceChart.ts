/**
 * convergenceChart — basis-convergence ("contrarian") plot builder.
 *
 * One line per contract: the signed basis gap
 * (gap_price_vs_underlying, in bps) between futures and its underlying
 * (spot). The emphasized zero line is the convergence target — as a
 * contract ages toward expiry its line visibly collapses into zero,
 * making the spot-vs-futures deviation (and its decay) obvious.
 *
 * Markers:
 *   - Matured contracts (history mode): a dot at the contract's LAST
 *     trading day labelled with the expiry gap ("expiry ±X bps") — the
 *     convergence endpoint.
 *   - Active contracts: a dot at the product's latest trading date
 *     labelled with yesterday's gap ("yesterday ±X bps") — where each
 *     contract stands now.
 *
 * Colors/order/dataZoom follow computeFuturesContractStyles so the
 * plot matches the price and correlation charts exactly.
 */
import React from "react";
import type { EChartsOption } from "echarts";
import {
  baseChartOption,
  commonTooltip,
} from "@/shared/charts/base-chart";
import type { ThemeMode } from "@/store/filters";
import {
  AXIS_POINTER_LINE,
  TOOLTIP_CARD_BG,
  TOOLTIP_CARD_BORDER,
  TOOLTIP_CARD_TEXT,
} from "@/theme/chart-palette";
import { renderReactElement, tooltipComponents } from "@/lib/react-tooltip-renderer";
import type { FuturesCombinedResponse } from "@shared/types";
import { computeFuturesContractStyles } from "./contractStyles";
import type { ViewMode, ZoomRange } from "./types";

function addDays(dateStr: string, days: number): string {
  const d = new Date(dateStr + "T00:00:00");
  d.setDate(d.getDate() + Math.round(days));
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

interface MarkerDataItem {
  coord: [string, number];
  labelText: string;
}

export function buildBasisConvergenceChartOption(
  mode: ThemeMode,
  data: FuturesCombinedResponse,
  gapByCodeDate: Map<string, Map<string, number | null>>,
  viewMode: ViewMode,
  zoomRange?: ZoomRange,
): EChartsOption | null {
  const { dates } = data;
  if (!dates.length) return null;
  const lastDate = dates[dates.length - 1];

  const { styleByCode, qualifying, matured, maturedCodeSet, rowByCodeDate } =
    computeFuturesContractStyles(data, viewMode, zoomRange);

  const fmtBps = (bps: number) => `${bps >= 0 ? "+" : ""}${bps.toFixed(1)} bps`;

  // Last non-null gap for a contract, walking back from `fromDate` at
  // most `maxLookback` calendar dates (handles the final trading day
  // missing from futures_ext for the current session).
  const lastGap = (
    code: string,
    fromDate: string,
    maxLookback = 7,
  ): { date: string; gap: number } | null => {
    const dm = gapByCodeDate.get(code);
    if (!dm) return null;
    let d = fromDate;
    for (let i = 0; i < maxLookback; i++) {
      const g = dm.get(d);
      if (g != null && Number.isFinite(g)) return { date: d, gap: g };
      const prev = new Date(d + "T00:00:00");
      prev.setDate(prev.getDate() - 1);
      d = `${prev.getFullYear()}-${String(prev.getMonth() + 1).padStart(2, "0")}-${String(prev.getDate()).padStart(2, "0")}`;
    }
    return null;
  };

  const mkSeries = (code: string, lastDateForCode: string) => {
    const st = styleByCode.get(code)!;
    const dm = gapByCodeDate.get(code);
    const rowData = rowByCodeDate.get(code);

    const dataItems = dates.map((d) => {
      const g = dm?.get(d);
      if (g == null || !Number.isFinite(g)) return null;
      const bps = g * 1e4;
      const dte = rowData?.get(d)?.days_to_expiry ?? null;
      // Object item so the tooltip formatter can read signed bps + dte.
      return { value: bps, signed: bps, dte };
    });

    // Markers near the edges of the VISIBLE zoom window get their label
    // pushed inward ("left" at the right edge, "right" at the left edge);
    // otherwise the label sits on top of the dot. Uses the current zoom
    // range (the option rebuilds on every zoom change).
    const totalDates = dates.length;
    let visStartIdx = 0;
    let visEndIdx = totalDates - 1;
    if (zoomRange) {
      visStartIdx = Math.floor((zoomRange.start / 100) * totalDates);
      visEndIdx = Math.ceil((zoomRange.end / 100) * totalDates);
    }
    const visLen = Math.max(visEndIdx - visStartIdx + 1, 1);
    const labelPosFor = (dateStr: string): "top" | "left" | "right" => {
      const idx = dates.indexOf(dateStr);
      if (idx >= visEndIdx - visLen * 0.05) return "left";
      if (idx <= visStartIdx + visLen * 0.08) return "right";
      return "top";
    };

    // --- Markers: expiry (matured, history mode) + yesterday (active) ---
    const markers: Array<MarkerDataItem & { position: "top" | "left" | "right" }> = [];
    if (st.isActive) {
      const lg = lastGap(code, lastDate);
      if (lg) {
        markers.push({
          coord: [lg.date, lg.gap * 1e4],
          labelText: `${code} yesterday: ${fmtBps(lg.gap * 1e4)}`,
          position: labelPosFor(lg.date),
        });
      }
    } else if (viewMode === "history") {
      // Matured contracts stopped trading long ago — walk back from the
      // contract's OWN last trading day, not the product calendar's end.
      const lg = lastGap(code, lastDateForCode, 14);
      if (lg) {
        // Expiry calendar date = contract's last trading day + remaining
        // days_to_expiry (0 on the final trading day for CFFEX).
        const dte = rowData?.get(lg.date)?.days_to_expiry ?? 0;
        const expiryDateStr = addDays(lg.date, dte);
        markers.push({
          coord: [lg.date, lg.gap * 1e4],
          labelText: `${code} expiry ${expiryDateStr}: ${fmtBps(lg.gap * 1e4)}`,
          position: labelPosFor(lg.date),
        });
      }
    }

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
      markPoint: markers.length
        ? {
            symbol: "circle",
            symbolSize: 7,
            itemStyle: {
              color: st.color,
              borderColor: "#fff",
              borderWidth: 1,
              opacity: st.isActive ? 1 : Math.max(st.opacity, 0.55),
            },
            label: {
              show: true,
              position: "top",
              distance: 6,
              fontSize: 10,
              color: "#666",
              formatter: (p: { data: MarkerDataItem & { position?: "top" | "left" | "right" } }) =>
                p.data.labelText,
            },
            data: markers.map((m) => ({ ...m, label: { position: m.position } })),
          }
        : undefined,
    };
  };

  // Series order MUST match the price + correlation plots
  // ([...qualifying, ...matured]) — see the correlation chart note.
  // Matured contracts anchor their expiry marker at their own last
  // trading day (contract meta last_date); active ones at the product's
  // latest date ("yesterday").
  const series = [
    ...qualifying.map((c) => mkSeries(c.code, lastDate)),
    ...matured.map((c) => mkSeries(c.code, c.last_date)),
  ];

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
    // The zero line (last series, silent + excluded from tooltip) is the
    // convergence target — futures price meeting spot. Placed last so the
    // preceding series indices keep matching the price/correlation plots.
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
