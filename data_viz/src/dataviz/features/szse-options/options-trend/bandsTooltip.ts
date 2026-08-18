/**
 * Bands tooltip formatter — React-based tooltip for the Expiry OI Bands chart.
 *
 * Uses React.createElement + a custom element-to-HTML renderer (shared
 * pattern with expiryTooltip.ts and annual-sentiment/tooltipComponents.tsx).
 */
import React from "react";
import { DOWN_COLOR, SPOT_COLOR, UP_COLOR } from "@/theme/chart-palette";
import { fmtMil, fmtNum } from "@/lib/series";
import { EXPIRY_MARKERS_SERIES_NAME, ExpiryInfoBlock, type TooltipColors } from "./expiryTooltip";
import { mixHex } from "./bandTexture";
import { BEAR_THRESHOLD_SERIES_NAME, BULL_THRESHOLD_SERIES_NAME } from "./bandData";
import type { BandCell } from "./bandData";
import type { ExpiryMarkerDataItem } from "./sharedData";

// ---- Renderer (shared with expiryTooltip.ts) -----------------------------

type El = React.ReactElement | string | number | boolean | null | undefined;

function styleObjectToString(style: React.CSSProperties | undefined): string {
  if (!style) return "";
  const parts: string[] = [];
  for (const [key, val] of Object.entries(style)) {
    if (val == null) continue;
    const cssKey = key.replace(/[A-Z]/g, (m) => `-${m.toLowerCase()}`);
    if (typeof val === "number") {
      const unitless = ["opacity", "zIndex", "fontWeight", "lineHeight"];
      if (unitless.includes(key) || val === 0) parts.push(`${cssKey}:${val}`);
      else parts.push(`${cssKey}:${val}px`);
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

function Dot({ color }: { color: string }) {
  return React.createElement("span", {
    style: {
      display: "inline-block",
      width: "8px",
      height: "8px",
      borderRadius: "50%",
      background: color,
      marginRight: "4px",
      verticalAlign: "middle",
    },
  });
}

function Bold({ children }: { children?: React.ReactNode }) {
  return React.createElement("b", null, children);
}

function Dim({ children }: { children?: React.ReactNode }) {
  return React.createElement("span", { style: { opacity: 0.65 } }, children);
}

const fmtOi = (v: number) => (v >= 1e6 ? fmtMil(v) : fmtNum(v));

interface BandTooltipParam {
  seriesName?: string;
  axisValue?: string;
  dataIndex?: number;
  value?: number | [number, number];
  data?: BandCell | ExpiryMarkerDataItem | number | null;
}

// ---- Formatter generator -------------------------------------------------

export function makeBandsTooltipFormatter(
  textColor: string,
  tooltipBg: string,
  splitLineColor: string,
) {
  const colors: TooltipColors = { textColor, tooltipBg, splitLineColor };
  return (params: unknown): string => {
    const arr = (Array.isArray(params) ? params : [params]) as BandTooltipParam[];
    if (arr.length === 0) return "";

    const spotParam = arr.find((p) => p.seriesName === "Spot");
    const expiryItem = arr.find((p) => p.seriesName === EXPIRY_MARKERS_SERIES_NAME);
    const bandCells = arr
      .filter((p) => p.seriesName !== "Spot" && p.seriesName !== EXPIRY_MARKERS_SERIES_NAME)
      .map((p) => p.data as BandCell)
      .filter((d): d is BandCell => !!d && typeof d !== "number" && d.putPct != null);

    const dateStr = spotParam?.axisValue ?? bandCells[0]?.date ?? "";
    const spotV =
      typeof spotParam?.value === "number" ? spotParam.value : spotParam?.value?.[1];
    const hasSpot = spotV != null && Number.isFinite(spotV);

    type Entry = { price: number; node: React.ReactElement };
    const entries: Entry[] = [];

    if (hasSpot) {
      entries.push({
        price: spotV as number,
        node: React.createElement(React.Fragment, null, [
          React.createElement(Dot, { color: SPOT_COLOR }),
          React.createElement(Bold, null, "Spot"),
          " · ",
          React.createElement(Bold, null, fmtNum(spotV as number)),
        ]),
      });
    }

    const bullV = arr.find((p) => p.seriesName === BULL_THRESHOLD_SERIES_NAME)?.value;
    const bearV = arr.find((p) => p.seriesName === BEAR_THRESHOLD_SERIES_NAME)?.value;

    for (const d of bandCells) {
      const callDominant = d.callOi >= d.putOi;
      const domName = callDominant ? "Call" : "Put";
      const domPct = callDominant ? 100 - d.putPct : d.putPct;
      const baseColor = callDominant ? UP_COLOR : DOWN_COLOR;
      const domColor = domPct > 75 ? baseColor : domPct > 60 ? mixHex(baseColor, "#ffffff", 0.55) : mixHex(baseColor, "#ffffff", 0.80);

      entries.push({
        price: d.strikeY,
        node: React.createElement(React.Fragment, null, [
          React.createElement(Dot, { color: domColor }),
          " K=",
          React.createElement(Bold, null, fmtNum(d.strikeY)),
          ` · ${fmtOi(d.totalOi)} · ${domName} ${fmtNum(domPct)}% `,
          React.createElement(Dim, null, `(C ${fmtOi(d.callOi)} / P ${fmtOi(d.putOi)})`),
        ]),
      });
    }

    const thresholdEntry = (
      seriesName: string,
      v: number | [number, number] | undefined,
      color: string,
    ): Entry | null => {
      const y = typeof v === "number" ? v : v?.[1];
      if (y == null || !Number.isFinite(y)) return null;
      return {
        price: y,
        node: React.createElement(React.Fragment, null, [
          React.createElement(Dot, { color }),
          ` ${seriesName} · `,
          React.createElement(Bold, null, fmtNum(y)),
        ]),
      };
    };

    const bullEntry = thresholdEntry(BULL_THRESHOLD_SERIES_NAME, bullV, UP_COLOR);
    if (bullEntry) entries.push(bullEntry);
    const bearEntry = thresholdEntry(BEAR_THRESHOLD_SERIES_NAME, bearV, DOWN_COLOR);
    if (bearEntry) entries.push(bearEntry);

    entries.sort((a, b) => b.price - a.price);

    const children: React.ReactNode[] = [
      React.createElement("b", null, dateStr),
    ];

    if (expiryItem && expiryItem.data && typeof expiryItem.data === "object" && "marker" in expiryItem.data) {
      children.push(
        React.createElement(ExpiryInfoBlock, {
          marker: (expiryItem.data as ExpiryMarkerDataItem).marker,
          colors,
        }),
      );
    }

    for (const e of entries) {
      children.push(React.createElement("div", { style: { marginTop: "2px" } }, e.node));
    }

    return render(React.createElement(React.Fragment, null, children));
  };
}