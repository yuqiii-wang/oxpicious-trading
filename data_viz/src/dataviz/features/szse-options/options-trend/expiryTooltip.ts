/**
 * React-based tooltip formatters for options-trend charts.
 *
 * Uses React.createElement + a custom element-to-HTML renderer to produce
 * HTML strings for ECharts tooltips — proper React component syntax without
 * requiring react-dom/server.
 *
 * Pattern follows the existing implementation in:
 *   - analysis/pages/MaSpread/chartOption/tooltipFormatter.tsx
 *   - szse-options/annual-sentiment/tooltipComponents.tsx
 */
import React from "react";
import { fmtNum } from "@/lib/series";
import { FUTURES_EXPIRY_DOT } from "@/theme/chart-palette";
import type { ExpiryMarker, ExpiryMarkerDataItem } from "./sharedData";

// ---- Custom React element → HTML renderer --------------------------------

type El = React.ReactElement | string | number | boolean | null | undefined;

function styleObjectToString(style: React.CSSProperties | undefined): string {
  if (!style) return "";
  const parts: string[] = [];
  for (const [key, val] of Object.entries(style)) {
    if (val == null) continue;
    const cssKey = key.replace(/[A-Z]/g, (m) => `-${m.toLowerCase()}`);
    if (typeof val === "number") {
      const unitless = ["opacity", "zIndex", "fontWeight", "lineHeight"];
      if (unitless.includes(key) || val === 0) {
        parts.push(`${cssKey}:${val}`);
      } else {
        parts.push(`${cssKey}:${val}px`);
      }
    } else {
      parts.push(`${cssKey}:${String(val)}`);
    }
  }
  return parts.join(";");
}

function renderChildren(children: React.ReactNode): string {
  if (children == null || children === false || children === true) return "";
  if (typeof children === "string") return children;
  if (typeof children === "number") return String(children);
  if (Array.isArray(children)) return children.map((c) => renderChildren(c)).join("");
  if (React.isValidElement(children)) return renderEl(children);
  return String(children);
}

function renderEl(el: El): string {
  if (el == null || el === false || el === true) return "";
  if (typeof el === "string") return el;
  if (typeof el === "number") return String(el);
  if (Array.isArray(el)) return el.map((c) => renderEl(c)).join("");
  if (!React.isValidElement(el)) return "";

  const { type, props } = el as React.ReactElement;

  if (type === React.Fragment) return renderChildren(props?.children);
  if (typeof type === "function") return renderEl((type as React.FC<Record<string, unknown>>)(props ?? {}) as El);

  const tag = String(type).toLowerCase();
  const styleStr = styleObjectToString(props?.style as React.CSSProperties | undefined);
  const classStr = props?.className ? ` class="${String(props.className)}"` : "";
  const styleAttr = styleStr ? ` style="${styleStr}"` : "";
  const childHtml = renderChildren(props?.children as React.ReactNode);

  return `<${tag}${classStr}${styleAttr}>${childHtml}</${tag}>`;
}

function render(el: React.ReactElement): string {
  return renderEl(el);
}

// ---- Shared helpers ------------------------------------------------------

export const EXPIRY_MARKERS_SERIES_NAME = "Expiry Markers";

export interface TooltipColors {
  textColor: string;
  tooltipBg: string;
  splitLineColor: string;
}

export interface AxisTooltipParam {
  axisValue?: string;
  marker?: string;
  seriesName?: string;
  value?: number | Array<number | null>;
  data?: ExpiryMarkerDataItem;
}

function Row({ children, style }: { children?: React.ReactNode; style?: React.CSSProperties }) {
  return React.createElement("div", { style: { marginBottom: "2px", ...style } }, children);
}

function Header({ children }: { children?: React.ReactNode }) {
  return React.createElement(Row, { style: { fontWeight: 600, marginBottom: "4px" } }, children);
}

// ---- Expiry info block ---------------------------------------------------

export function ExpiryInfoBlock({
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

// ---- Expiry dot tooltip --------------------------------------------------

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

// ---- Data helpers --------------------------------------------------------

/**
 * Build expiry marker scatter data aligned to broken date indices.
 * Falls back to the nearest prior valid value if the target date is null.
 */
export function buildExpiryData(
  brokenDates: string[],
  valueArrays: (number | null)[][],
  expiryMarkers: ExpiryMarker[],
): ExpiryMarkerDataItem[] {
  const dateIndexMap = new Map<string, number>();
  brokenDates.forEach((d, i) => dateIndexMap.set(d, i));

  const expiryData: ExpiryMarkerDataItem[] = [];
  for (const m of expiryMarkers) {
    const idx = dateIndexMap.get(m.tradingDate);
    if (idx == null) continue;

    let yVal: number | null = null;
    const firstArr = valueArrays[0];
    if (firstArr) {
      const v = firstArr[idx];
      if (v != null && Number.isFinite(v)) {
        yVal = v;
      } else {
        for (let j = idx - 1; j >= 0; j--) {
          const fv = firstArr[j];
          if (fv != null && Number.isFinite(fv)) {
            yVal = fv;
            break;
          }
        }
      }
    }
    if (yVal == null || !Number.isFinite(yVal)) continue;
    expiryData.push({ value: [idx, yVal], marker: m });
  }
  return expiryData;
}

// ---- Formatter generators -----------------------------------------------

/** Axis-level tooltip builder for trend charts (P/C Ratio, Total OI). */
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

    return render(React.createElement(React.Fragment, null, children));
  };
}

/** Dot-level tooltip formatter for expiry scatter points. */
export function makeExpiryDotTooltip(colors: TooltipColors, totalSuffix = "") {
  return (params: unknown): string => {
    const p = params as { data?: ExpiryMarkerDataItem };
    if (!p.data) return "";
    return render(
      React.createElement(ExpiryDotTooltip, {
        marker: p.data.marker,
        colors,
        totalSuffix,
      }),
    );
  };
}