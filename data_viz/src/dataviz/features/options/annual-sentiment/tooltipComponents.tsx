/**
 * React-based tooltip formatters for annual-sentiment charts.
 *
 * Uses React.createElement + a custom element-to-HTML renderer to produce
 * HTML strings for ECharts tooltips — proper React component syntax without
 * requiring react-dom/server.
 */
import React from "react";
import { renderReactElement } from "@/lib/react-tooltip-renderer";
import { fmtNum } from "@/lib/series";
import { formatPriceValue, type OhlcMode } from "@/lib/ohlc";
import { FUTURES_EXPIRY_DOT } from "@/theme/chart-palette";
import type { ExpiryMarker, ExpiryMarkerDataItem } from "./types";

// ---- Shared helpers ------------------------------------------------------

export const EXPIRY_MARKERS_SERIES_NAME = "Expiry Markers";

export interface TooltipColors {
  textColor: string;
  tooltipBg: string;
  splitLineColor: string;
}

// ---- Reusable React tooltip components -----------------------------------

function Row({ children, style }: { children?: React.ReactNode; style?: React.CSSProperties }) {
  return React.createElement("div", { style: { marginBottom: "2px", ...style } }, children);
}

function Header({ children }: { children?: React.ReactNode }) {
  return React.createElement(Row, { style: { fontWeight: 600, marginBottom: "4px" } }, children);
}


// ---- Expiry info block component -----------------------------------------

function ExpiryInfoBlock({
  marker,
  colors,
  shownLimit = 8,
  includeTotal = true,
  totalSuffix = "",
}: {
  marker: ExpiryMarker;
  colors: TooltipColors;
  shownLimit?: number;
  includeTotal?: boolean;
  totalSuffix?: string;
}) {
  const children: React.ReactNode[] = [];

  children.push(
    React.createElement("div", {
      style: { margin: "6px 0 2px", fontWeight: 600, color: FUTURES_EXPIRY_DOT },
    }, `📅 Expiry: ${marker.expiryDate}`),
  );
  children.push(
    React.createElement("div", {
      style: { color: colors.textColor, opacity: 0.85, marginBottom: "2px" },
    }, `Contracts expiring (${marker.contracts.length}):`),
  );

  const shown = marker.contracts.slice(0, shownLimit);
  for (const ctr of shown) {
    children.push(
      React.createElement("div", {
        style: { display: "flex", justifyContent: "space-between", gap: "8px" },
      }, [
        React.createElement("span", { style: { opacity: 0.8 } }, ctr.name || ctr.code),
        React.createElement("span", { style: { fontWeight: 500 } }, fmtNum(ctr.prevDayOi)),
      ]),
    );
  }

  if (marker.contracts.length > shownLimit) {
    children.push(
      React.createElement("div", {
        style: { opacity: 0.6, fontSize: "10px", marginTop: "2px" },
      }, `+${marker.contracts.length - shownLimit} more…`),
    );
  }

  if (includeTotal) {
    children.push(
      React.createElement("div", {
        style: {
          borderTop: `1px solid ${colors.splitLineColor}`,
          marginTop: "4px",
          paddingTop: "3px",
          display: "flex",
          justifyContent: "space-between",
        },
      }, [
        React.createElement("span", { style: { fontWeight: 600 } }, "Total Prev-Day OI"),
        React.createElement("span", { style: { fontWeight: 600 } }, `${fmtNum(marker.totalPrevDayOi)}${totalSuffix}`),
      ]),
    );
    children.push(React.createElement("div", { style: { height: "4px" } }));
  }

  return React.createElement(React.Fragment, null, children);
}

// ---- Expiry dot tooltip component -----------------------------------------

function ExpiryDotTooltip({
  marker,
  colors,
  totalSuffix = "",
}: {
  marker: ExpiryMarker;
  colors: TooltipColors;
  totalSuffix?: string;
}) {
  const children: React.ReactNode[] = [];

  children.push(
    React.createElement("div", { style: { fontWeight: 600, marginBottom: "6px" } }, `📅 Expiry: ${marker.expiryDate}`),
  );
  children.push(
    React.createElement("div", {
      style: { opacity: 0.85, marginBottom: "4px", fontSize: "10px" },
    }, `Last trading day: ${marker.tradingDate}`),
  );
  children.push(
    React.createElement("div", { style: { fontWeight: 600, marginBottom: "2px" } }, `Expiring Contracts (${marker.contracts.length}):`),
  );

  const shown = marker.contracts.slice(0, 10);
  for (const ctr of shown) {
    children.push(
      React.createElement("div", {
        style: { display: "flex", justifyContent: "space-between", gap: "8px", fontSize: "11px" },
      }, [
        React.createElement("span", { style: { opacity: 0.8 } }, ctr.name || ctr.code),
        React.createElement("span", { style: { fontWeight: 500 } }, fmtNum(ctr.prevDayOi)),
      ]),
    );
  }

  if (marker.contracts.length > 10) {
    children.push(
      React.createElement("div", {
        style: { opacity: 0.6, fontSize: "10px", marginTop: "2px" },
      }, `+${marker.contracts.length - 10} more…`),
    );
  }

  children.push(
    React.createElement("div", {
      style: {
        borderTop: `1px solid ${colors.splitLineColor}`,
        marginTop: "6px",
        paddingTop: "4px",
        display: "flex",
        justifyContent: "space-between",
      },
    }, [
      React.createElement("span", { style: { fontWeight: 600 } }, "Total Prev-Day OI"),
      React.createElement("span", { style: { fontWeight: 600 } }, `${fmtNum(marker.totalPrevDayOi)}${totalSuffix}`),
    ]),
  );

  return React.createElement(React.Fragment, null, children);
}

// ---- Axis tooltip formatter generators ------------------------------------

interface AxisTooltipParam {
  axisValue?: string;
  marker?: string;
  seriesName?: string;
  value?: number | Array<number | null>;
  data?: ExpiryMarkerDataItem;
}

/** Shared axis-level tooltip builder for P/C Ratio and Total OI charts. */
export function makeAxisTooltipFormatter(colors: TooltipColors) {
  return (params: unknown): string => {
    const arr = (Array.isArray(params) ? params : [params]) as AxisTooltipParam[];
    if (arr.length === 0) return "";

    const children: React.ReactNode[] = [];
    const dateStr = (arr[0].axisValue as string) || "";

    children.push(React.createElement(Header, null, dateStr));

    const expiryItem = arr.find((p) => p.seriesName === EXPIRY_MARKERS_SERIES_NAME);
    if (expiryItem && expiryItem.data) {
      children.push(
        React.createElement(ExpiryInfoBlock, {
          marker: expiryItem.data.marker,
          colors,
        }),
      );
    }

    for (const p of arr) {
      if (p.seriesName === EXPIRY_MARKERS_SERIES_NAME) continue;
      if (p.value == null) continue;
      const v = Array.isArray(p.value) ? p.value[p.value.length - 1] : p.value;
      if (v == null || (typeof v === "number" && !Number.isFinite(v))) continue;
      const vstr = typeof v === "number" ? fmtNum(v) : String(v);
      children.push(
        React.createElement(Row, null, [
          p.marker ?? "",
          ` ${p.seriesName ?? ""}: `,
          React.createElement("b", null, vstr),
        ]),
      );
    }

    return renderReactElement(React.createElement(React.Fragment, null, children));
  };
}

/** Dot-level tooltip formatter for expiry scatter points. */
export function makeExpiryDotTooltip(colors: TooltipColors, totalSuffix = "") {
  return (params: unknown): string => {
    const p = params as { data?: ExpiryMarkerDataItem };
    if (!p.data) return "";
    return renderReactElement(
      React.createElement(ExpiryDotTooltip, {
        marker: p.data.marker,
        colors,
        totalSuffix,
      }),
    );
  };
}

/** OHLC axis tooltip formatter. */
export function makeOhlcAxisTooltipFormatter(colors: TooltipColors, ohlcMode: OhlcMode) {
  return (params: unknown): string => {
    const arr = (Array.isArray(params) ? params : [params]) as AxisTooltipParam[];
    if (arr.length === 0) return "";

    const children: React.ReactNode[] = [];
    const dateStr = (arr[0].axisValue as string) || "";

    children.push(React.createElement(Header, null, dateStr));

    for (const p of arr) {
      if (p.value == null) continue;
      const name = p.seriesName ?? "";

      if (Array.isArray(p.value)) {
        const [o, cl, l, h] = p.value;
        if (o == null && cl == null && l == null && h == null) continue;
        children.push(
          React.createElement(Row, null, [
            p.marker ?? "",
            ` ${name}: O=${formatPriceValue(o, ohlcMode)} H=${formatPriceValue(h, ohlcMode)} L=${formatPriceValue(l, ohlcMode)} C=${formatPriceValue(cl, ohlcMode)}`,
          ]),
        );
      } else {
        const v = p.value as number;
        if (!Number.isFinite(v)) continue;
        const vstr = name === "Volume" ? fmtNum(v) + " mil" : fmtNum(v);
        children.push(
          React.createElement(Row, null, [
            p.marker ?? "",
            ` ${name}: `,
            React.createElement("b", null, vstr),
          ]),
        );
      }
    }

    return renderReactElement(React.createElement(React.Fragment, null, children));
  };
}