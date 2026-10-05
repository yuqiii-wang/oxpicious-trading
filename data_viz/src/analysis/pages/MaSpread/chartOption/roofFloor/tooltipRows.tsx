/**
 * Roof/floor tooltip rows (the pair chart) — the enabled OHLC-window
 * overlay's rolling O/H/L at the hovered date plus its (top, 2nd)
 * extrema with dates (the four points that determine the roof/floor
 * trendlines), the DB-persisted line slopes, and the move since each
 * side's LAST anchor.
 */
import React from "react";
import { fmtNum } from "@/lib/series";
import { UP_COLOR, DOWN_COLOR } from "@/theme/chart-palette";
import { Row } from "../_common/tooltipKit";
import { fmtVal } from "../_common/tooltipFmt";
import type { TooltipContext, TooltipFmtHelpers } from "../_common";

/** The OHLC-window overlay rows when the overlay is on. */
export function appendRoofFloorRows(
  children: React.ReactNode[],
  ctx: TooltipContext,
  idx: number,
  helpers: TooltipFmtHelpers,
): void {
  const { roofFloorWinArrays, roofFloorWindow } = ctx;
  if (roofFloorWinArrays == null || roofFloorWindow == null) return;
  const w = roofFloorWindow;
  const A = roofFloorWinArrays;
  const o = A.open[idx];
  const h = A.high[idx];
  const l = A.low[idx];
  children.push(
    React.createElement(Row, {
      key: "ohlcOverlay",
      style: { marginTop: "2px", opacity: 0.85 },
    }, `OHLC ${w}d: O ${helpers.fmtPrice(o)} · H ${helpers.fmtPrice(h)} · L ${helpers.fmtPrice(l)}`),
  );
  const hDate = A.highDate[idx];
  const h2 = A.high2nd[idx];
  const h2Date = A.high2ndDate[idx];
  const hSlope = A.highSlope[idx];
  if (hDate != null || h2Date != null || hSlope != null) {
    children.push(
      React.createElement(Row, {
        key: "ohlcPeaks",
        style: { color: "#FB8C00", opacity: 0.95 },
      }, `Roof pts: top ${helpers.fmtPrice(h)} @ ${hDate ?? "—"} · 2nd ${helpers.fmtPrice(h2)} @ ${h2Date ?? "—"} · slope ${helpers.fmtSlope(hSlope)}/d`),
    );
  }
  const lDate = A.lowDate[idx];
  const l2 = A.low2nd[idx];
  const l2Date = A.low2ndDate[idx];
  const lSlope = A.lowSlope[idx];
  if (lDate != null || l2Date != null || lSlope != null) {
    children.push(
      React.createElement(Row, {
        key: "ohlcTroughs",
        style: { color: "#1E88E5", opacity: 0.95 },
      }, `Floor pts: top ${helpers.fmtPrice(l)} @ ${lDate ?? "—"} · 2nd ${helpers.fmtPrice(l2)} @ ${l2Date ?? "—"} · slope ${helpers.fmtSlope(lSlope)}/d`),
    );
  }
  // Move since the LAST high anchor and since the LAST low anchor
  // (each the later-dated of that side's two roof/floor anchors —
  // see the roof/floor table's change/days_since_last_high|low
  // columns). The stored change is a price DELTA — fmtPrice assumes
  // a price level, so pct mode rebases it with the shared
  // slopeScale (like the line slopes), absolute mode shows raw
  // price units.
  const fmtSince = (chg: number | null): string =>
    chg == null
      ? "—"
      : helpers.isPctSlope
        ? fmtVal(chg * helpers.slopeScale) + "%"
        : fmtNum(chg);
  const sinceRow = (
    key: string,
    label: string,
    chg: number | null,
    days: number | null,
  ): React.ReactNode => {
    const chgColor = chg != null && chg < 0 ? DOWN_COLOR : UP_COLOR;
    const daysStr = days != null ? `${days} td` : "—";
    return React.createElement(Row, {
      key,
      style: { opacity: 0.85 },
    }, [
      `${label}: `,
      React.createElement("span", {
        key: "chg",
        style: { color: chgColor, fontWeight: 600 },
      }, fmtSince(chg)),
      ` · ${daysStr}`,
    ]);
  };
  const hiChg = A.changeSinceLastHigh[idx];
  const hiDays = A.daysSinceLastHigh[idx];
  if (hiChg != null || hiDays != null) {
    children.push(
      sinceRow("ohlcSinceLastHigh", "Since last high", hiChg, hiDays),
    );
  }
  const loChg = A.changeSinceLastLow[idx];
  const loDays = A.daysSinceLastLow[idx];
  if (loChg != null || loDays != null) {
    children.push(
      sinceRow("ohlcSinceLastLow", "Since last low", loChg, loDays),
    );
  }
}
