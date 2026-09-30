/**
 * Bands tooltip formatter — React-based tooltip for the Expiry OI Bands chart.
 *
 * Uses React.createElement + a custom element-to-HTML renderer (shared
 * pattern with expiryTooltip.ts and annual-sentiment/tooltipComponents.tsx).
 *
 * Filters items to only show strike levels that have OI data for the
 * hovered date. Shows the backend zone walls (wall_type='zone') with
 * lifecycle state / mass share / persistence.
 */
import React from "react";
import { DOWN_COLOR, SPOT_COLOR, UP_COLOR } from "@/theme/chart-palette";
import { fmtMil, fmtNum } from "@/lib/series";
import { renderReactElement } from "@/lib/react-tooltip-renderer";
import { EXPIRY_MARKERS_SERIES_NAME, ExpiryInfoBlock, type TooltipColors } from "./expiryTooltip";
import { mixHex } from "./bandTexture";
import {
  CALL_ZONE_SERIES_NAME,
  PUT_ZONE_SERIES_NAME,
  type ZoneWallPoint,
  type ZoneWallSeries,
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

/** State label color: ACTIVE = side color, ERODED dimmed, BREACHED red. */
function stateColor(state: ZoneWallPoint["state"]): string {
  switch (state) {
    case "BREACHED":
      return "#c0392b";
    case "ERODED":
      return "#e67e22";
    default:
      return "#27ae60";
  }
}

export function makeBandsTooltipFormatter(
  textColor: string,
  tooltipBg: string,
  splitLineColor: string,
  zones?: ZoneWallSeries,
) {
  const colors: TooltipColors = { textColor, tooltipBg, splitLineColor };
  // Date → dominant zone per side (first non-null wins; broken-date arrays
  // may repeat a source date).
  const zoneByDate = new Map<string, { call: ZoneWallPoint | null; put: ZoneWallPoint | null }>();
  if (zones) {
    for (let i = 0; i < zones.call.length || i < zones.put.length; i++) {
      const call = zones.call[i] ?? null;
      const put = zones.put[i] ?? null;
      const date = call?.date ?? put?.date;
      if (date && !zoneByDate.has(date)) zoneByDate.set(date, { call, put });
    }
  }
  return (params: unknown): string => {
    const arr = (Array.isArray(params) ? params : [params]) as BandTooltipParam[];
    if (arr.length === 0) return "";

    const spotParam = arr.find((p) => p.seriesName === "Spot");
    const expiryItem = arr.find((p) => p.seriesName === EXPIRY_MARKERS_SERIES_NAME);
    const bandCells = arr
      .filter((p) => p.seriesName !== "Spot" && p.seriesName !== EXPIRY_MARKERS_SERIES_NAME
        && p.seriesName !== CALL_ZONE_SERIES_NAME
        && p.seriesName !== PUT_ZONE_SERIES_NAME)
      .map((p) => p.data as BandCell)
      .filter((d): d is BandCell => !!d && typeof d !== "number" && d.putPct != null && d.totalOi > 0);

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

    // Zone wall entries (backend analysis.options_walls, dominant per side)
    const zoneEntry = (name: string, z: ZoneWallPoint | null, color: string): Entry | null => {
      if (!z) return null;
      const stateBits: React.ReactNode[] = [];
      if (z.state) {
        stateBits.push(React.createElement(
          "span",
          { key: "s", style: { color: stateColor(z.state), fontWeight: 600 } },
          z.state,
        ));
      }
      if (z.daysPersisted != null) {
        stateBits.push(React.createElement(Dim, { key: "d" }, ` ×${z.daysPersisted}d`));
      }
      return {
        price: z.center,
        node: React.createElement(React.Fragment, null, [
          React.createElement(Dot, { color }),
          ` ${name} · `,
          React.createElement(Bold, null, `${fmtNum(z.low)}–${fmtNum(z.high)}`),
          ` (ctr ${fmtNum(z.center)})`,
          z.massShare != null
            ? React.createElement(Dim, null, ` · mass ${(z.massShare * 100).toFixed(1)}%`)
            : null,
          stateBits.length > 0 ? React.createElement(Dim, null, " · ") : null,
          ...stateBits,
        ]),
      };
    };
    const zonesForDate = dateStr ? zoneByDate.get(dateStr) : undefined;
    if (zonesForDate) {
      const callEntry = zoneEntry(CALL_ZONE_SERIES_NAME, zonesForDate.call, UP_COLOR);
      if (callEntry) entries.push(callEntry);
      const putEntry = zoneEntry(PUT_ZONE_SERIES_NAME, zonesForDate.put, DOWN_COLOR);
      if (putEntry) entries.push(putEntry);
    }

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