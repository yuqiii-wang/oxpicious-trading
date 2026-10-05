/**
 * Roof/floor group state — everything the Roof/Floor Trendlines row's
 * overlays need, built once per option render: the rolling envelope +
 * trendlines + anchor markers (the period buttons), the extreme↔2nd-
 * extreme shade zones (the "shade" toggle), the band EVENT markers, and
 * the dashed anchor mark line. Series assembly lives in series.ts; the
 * tooltip rows read `roofFloorWinArrays` through the TooltipContext.
 */
import type { MovAveSpreadBandsRow, MovAveSpreadOhlcRow } from "@shared/types";
import type { StreakMarkAreaDatum } from "@/shared/charts/streakBands";
import type { TooltipContext } from "../_common";
import { pickRoofFloorWinFields } from "./winFields";
import { rollingExtremes, trendlineSegment } from "./envelope";
import { buildExtremeShades } from "./shades";
import { readBandEvents, type BandEventItems } from "./eventMarkers";
import { ROOF_COLOR, FLOOR_COLOR } from "./colors";

/** Per-anchor scatter items: filled marker for the top extreme, hollow for
 *  the 2nd; triangles point up for peaks and down for troughs. */
export interface RoofFloorAnchorItem {
  value: [number, number];
  symbol: string;
  symbolRotate: number;
  itemStyle: { color: string; borderColor: string; borderWidth: number };
}

/** Vertical "stop" line at the clicked date — the roof/floor trendlines
 *  converge toward and stop at this line; also marks the shade anchor.
 *  Attached to the long-MA series. */
export interface AnchorMarkLine {
  markLine: {
    silent: true;
    symbol: [string, string];
    label: { show: false };
    lineStyle: { type: "dashed"; color: string; width: 1; opacity: 0.75 };
    data: Array<{ xAxis: string }>;
  };
}

export interface RoofFloorState {
  rfW: number | null;
  rfAligned: boolean;
  /** Rolling window bound curves: the true rolling MAX/MIN WITHIN the
   *  window (intraday high / low of each date) — NOT the (top, 2nd)
   *  anchor values, which only drive the Roof/Floor trendlines. */
  roofHighLine: Array<number | null>;
  floorLowLine: Array<number | null>;
  roofSeg: Array<[number, number]> | null;
  floorSeg: Array<[number, number]> | null;
  anchorItems: RoofFloorAnchorItem[];
  /** Per-date OHLC extrema arrays for the enabled window — null when
   *  the overlay is off (consumed by the tooltip rows). */
  roofFloorWinArrays: TooltipContext["roofFloorWinArrays"];
  shadeOn: boolean;
  shadeTopData: StreakMarkAreaDatum[];
  shadeBottomData: StreakMarkAreaDatum[];
  events: BandEventItems;
  anchorMarkLine: AnchorMarkLine | null;
}

export interface RoofFloorBuildArgs {
  n: number;
  dates: string[];
  shorts: Array<number | null>;
  highs: Array<number | null>;
  lows: Array<number | null>;
  /** Enabled rolling-OHLC window (trading days) — null/undefined = off. */
  roofFloorWindow: number | null;
  /** Index into the chart rows of the clicked chart date — the
   *  roof/floor trendlines are drawn from the window's (top, 2nd)
   *  extrema at this date and stop here. */
  roofFloorClickIdx: number | null;
  /** Rolling-window OHLC extrema rows (index-aligned with the rows). */
  roofFloorRows: MovAveSpreadOhlcRow[] | null;
  /** Roof/floor shade-band rows from the bands table (index-aligned). */
  bandRows: MovAveSpreadBandsRow[] | null;
  /** "shade" toggle windows — non-empty shades EVERY listed window. */
  roofFloorShadeWindows: readonly number[] | null;
  /** Theme text color (the dashed anchor mark line). */
  textColor: string;
}

export function buildRoofFloorState(args: RoofFloorBuildArgs): RoofFloorState {
  const {
    n, dates, shorts, highs, lows,
    roofFloorWindow, roofFloorClickIdx, roofFloorRows, bandRows,
    roofFloorShadeWindows, textColor,
  } = args;

  // ---- Roof/floor-window overlay (rolling envelope + trendlines) --------
  // Active only when a window button is enabled AND the extrema rows are
  // index-aligned with the pair's rows (all pairs share one date axis, so
  // chartData.ohlc aligns with every pair).
  const rfW = roofFloorWindow ?? null;
  const rfAligned = rfW != null && roofFloorRows != null && roofFloorRows.length === n;
  let roofHighLine: Array<number | null> = [];
  let floorLowLine: Array<number | null> = [];
  let roofSeg: Array<[number, number]> | null = null;
  let floorSeg: Array<[number, number]> | null = null;
  let roofFloorWinArrays: TooltipContext["roofFloorWinArrays"] = null;
  const anchorItems: RoofFloorAnchorItem[] = [];

  if (rfAligned && rfW != null && roofFloorRows != null) {
    const fields = roofFloorRows.map((r) => pickRoofFloorWinFields(r, rfW));
    // High/Low bound curves: the true rolling MAX/MIN WITHIN the window
    // (intraday high / low of each date) — NOT the (top, 2nd) anchor
    // values, which only drive the Roof/Floor trendlines below.
    roofHighLine = rollingExtremes(highs, rfW, 1);
    floorLowLine = rollingExtremes(lows, rfW, -1);
    roofFloorWinArrays = {
      open: fields.map((f) => f.open),
      high: fields.map((f) => f.high),
      highDate: fields.map((f) => f.highDate),
      high2nd: fields.map((f) => f.high2nd),
      high2ndDate: fields.map((f) => f.high2ndDate),
      highSlope: fields.map((f) => f.highSlope),
      low: fields.map((f) => f.low),
      lowDate: fields.map((f) => f.lowDate),
      low2nd: fields.map((f) => f.low2nd),
      low2ndDate: fields.map((f) => f.low2ndDate),
      lowSlope: fields.map((f) => f.lowSlope),
      changeSinceLastHigh: fields.map((f) => f.changeSinceLastHigh),
      daysSinceLastHigh: fields.map((f) => f.daysSinceLastHigh),
      changeSinceLastLow: fields.map((f) => f.changeSinceLastLow),
      daysSinceLastLow: fields.map((f) => f.daysSinceLastLow),
    };

    // Roof/floor trendlines at the clicked date: two points (the window's
    // top and 2nd extrema) determine each line; the segment starts at the
    // earlier anchor, passes through the later one, and stops at the
    // clicked date.
    const clickIdx = roofFloorClickIdx ?? null;
    if (clickIdx != null && clickIdx >= 0 && clickIdx < n) {
      const win = fields[clickIdx];
      if (
        win != null && win.high != null && win.highDate != null &&
        win.high2nd != null && win.high2ndDate != null
      ) {
        const i1 = dates.indexOf(win.highDate);
        const i2 = dates.indexOf(win.high2ndDate);
        roofSeg = trendlineSegment(clickIdx, i1, win.high, i2, win.high2nd);
        if (roofSeg != null) {
          anchorItems.push(
            { value: [i1, win.high], symbol: "triangle", symbolRotate: 0, itemStyle: { color: ROOF_COLOR, borderColor: ROOF_COLOR, borderWidth: 1 } },
            { value: [i2, win.high2nd], symbol: "triangle", symbolRotate: 0, itemStyle: { color: "transparent", borderColor: ROOF_COLOR, borderWidth: 1.5 } },
          );
        }
      }
      if (
        win != null && win.low != null && win.lowDate != null &&
        win.low2nd != null && win.low2ndDate != null
      ) {
        const i1 = dates.indexOf(win.lowDate);
        const i2 = dates.indexOf(win.low2ndDate);
        floorSeg = trendlineSegment(clickIdx, i1, win.low, i2, win.low2nd);
        if (floorSeg != null) {
          anchorItems.push(
            { value: [i1, win.low], symbol: "triangle", symbolRotate: 180, itemStyle: { color: FLOOR_COLOR, borderColor: FLOOR_COLOR, borderWidth: 1 } },
            { value: [i2, win.low2nd], symbol: "triangle", symbolRotate: 180, itemStyle: { color: "transparent", borderColor: FLOOR_COLOR, borderWidth: 1.5 } },
          );
        }
      }
    }
  }

  // ---- Extreme↔2nd-extreme zone shades ("shade" toggle) ------------------
  const shades = buildExtremeShades({
    dates, shorts, highs, lows,
    roofFloorClickIdx, roofFloorRows, bandRows, roofFloorShadeWindows,
  });

  // ---- Band EVENT markers (the bands table's anchor-band events) -------
  const events = readBandEvents({
    n, dates, bandRows,
    shadeAnchorIdx: shades.shadeAnchorIdx,
    drawnRoofBot: shades.drawnRoofBot,
    drawnFloorTop: shades.drawnFloorTop,
    shadeWindows: roofFloorShadeWindows,
  });

  // Dashed vertical mark line at the clicked date.
  const anchorMarkLine: AnchorMarkLine | null =
    (rfAligned || shades.shadeOn) && roofFloorClickIdx != null && roofFloorClickIdx >= 0 && roofFloorClickIdx < n
      ? {
          markLine: {
            silent: true,
            symbol: ["none", "none"],
            label: { show: false },
            lineStyle: { type: "dashed", color: textColor, width: 1, opacity: 0.75 },
            data: [{ xAxis: dates[roofFloorClickIdx] }],
          },
        }
      : null;

  return {
    rfW,
    rfAligned,
    roofHighLine,
    floorLowLine,
    roofSeg,
    floorSeg,
    anchorItems,
    roofFloorWinArrays,
    shadeOn: shades.shadeOn,
    shadeTopData: shades.shadeTopData,
    shadeBottomData: shades.shadeBottomData,
    events,
    anchorMarkLine,
  };
}
