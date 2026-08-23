/**
 * Bands tooltip formatter — React-based tooltip for the Expiry OI Bands chart.
 *
 * Uses React.createElement + a custom element-to-HTML renderer (shared
 * pattern with expiryTooltip.ts and annual-sentiment/tooltipComponents.tsx).
 *
 * Filters items to only show strike levels that have OI data for the
 * hovered date. Supports both 80% wall and large_num wall modes.
 */
import React from "react";
import { DOWN_COLOR, SPOT_COLOR, UP_COLOR } from "@/theme/chart-palette";
import { fmtMil, fmtNum } from "@/lib/series";
import { renderReactElement } from "@/lib/react-tooltip-renderer";
import { EXPIRY_MARKERS_SERIES_NAME, ExpiryInfoBlock, type TooltipColors } from "./expiryTooltip";
import { mixHex } from "./bandTexture";
import {
  BEAR_THRESHOLD_SERIES_NAME,
  BULL_THRESHOLD_SERIES_NAME,
  CALL_LARGE_NUM_SERIES_NAME,
  PUT_LARGE_NUM_SERIES_NAME,
  type WallMode,
} from "./bandData";
import type { BandCell } from "./bandData";
import type { ExpiryMarkerDataItem } from "./sharedData";

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
  wallMode: WallMode = "80pct",
) {
  const colors: TooltipColors = { textColor, tooltipBg, splitLineColor };
  return (params: unknown): string => {
    const arr = (Array.isArray(params) ? params : [params]) as BandTooltipParam[];
    if (arr.length === 0) return "";

    const spotParam = arr.find((p) => p.seriesName === "Spot");
    const expiryItem = arr.find((p) => p.seriesName === EXPIRY_MARKERS_SERIES_NAME);
    const bandCells = arr
      .filter((p) => p.seriesName !== "Spot" && p.seriesName !== EXPIRY_MARKERS_SERIES_NAME
        && p.seriesName !== BULL_THRESHOLD_SERIES_NAME
        && p.seriesName !== BEAR_THRESHOLD_SERIES_NAME
        && p.seriesName !== CALL_LARGE_NUM_SERIES_NAME
        && p.seriesName !== PUT_LARGE_NUM_SERIES_NAME)
      .map((p) => p.data as BandCell)
      .filter((d): d is BandCell => !!d && typeof d !== "number" && d.putPct != null && d.totalOi > 0);

    const dateStr = spotParam?.axisValue ?? bandCells[0]?.date ?? "";
    const spotV =
      typeof spotParam?.value === "number" ? spotParam.value : spotParam?.value?.[1];
    const hasSpot = spotV != null && Number.isFinite(spotV);

    // Determine the wall series names based on mode
    const callWallName = wallMode === "80pct" ? BULL_THRESHOLD_SERIES_NAME : CALL_LARGE_NUM_SERIES_NAME;
    const putWallName = wallMode === "80pct" ? BEAR_THRESHOLD_SERIES_NAME : PUT_LARGE_NUM_SERIES_NAME;

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

    // Only show bandCells that belong to the hovered date
    // (filter by date match when dateStr is available)
    const filteredCells = dateStr
      ? bandCells.filter((d) => d.date === dateStr)
      : bandCells;

    for (const d of filteredCells) {
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

    // Show wall entries based on mode
    const callWallV = arr.find((p) => p.seriesName === callWallName)?.value;
    const putWallV = arr.find((p) => p.seriesName === putWallName)?.value;

    const callWallEntry = thresholdEntry(callWallName, callWallV, UP_COLOR);
    if (callWallEntry) entries.push(callWallEntry);
    const putWallEntry = thresholdEntry(putWallName, putWallV, DOWN_COLOR);
    if (putWallEntry) entries.push(putWallEntry);

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

    return renderReactElement(React.createElement(React.Fragment, null, children));
  };
}