/**
 * The change-since-last-anchor right-axis plot — the Roof/Floor
 * Trendlines row's exclusive right-axis slot: while a window button is
 * enabled (and no higher-precedence overlay — RSI Trends, Slopes,
 * Curvatures — holds the slot), the trading-amt bars are REPLACED by the
 * enabled window's two DB-persisted change lines around one dashed zero
 * central line:
 *   Δ Low(Wd)  = change_since_last_low_Wd  (close − the LAST low anchor's
 *              value) — the side ABOVE the line, positive = price grown
 *              off the floor anchor;
 *   Δ High(Wd) = change_since_last_high_Wd (close − the LAST high anchor's
 *              value) — the side BELOW the line, negative = price dropped
 *              off the roof anchor.
 * Both lines ride the RSI/deriv-style band offset (one shared scale maps
 * the signed range into the [0,100] band of the fixed 0-400 right axis,
 * zero pinned mid-band at 50) so the pair hugs the bottom quarter of the
 * plot, away from the price trend, and the pinned zero IS the central
 * separation line. A line crossing it = price beyond that side's anchor
 * (breakout). Colors follow the side's trendline: Δ Low in the floor
 * blue, Δ High in the roof orange. Raw price units — NOT rebased in
 * percentage ohlcMode (the tooltip row rebases for display), mirroring
 * the derivTrend convention; the band mapping is re-declared here (group
 * internals are not shared across groups).
 */
import { fmtNum } from "@/lib/series";
import type { AxisColors, SeriesList } from "../_common";
import type { RoofFloorState } from "./state";
import { ROOF_COLOR, FLOOR_COLOR } from "./colors";

/** Legend names of the two change lines — the Δ marks "change since the
 *  LAST anchor of the side" (the later-dated of the side's two roof/floor
 *  anchors; the 2nd whenever present, else the top). */
export const changeSinceLowName = (w: number) => `Δ Low(${w}d)`;
export const changeSinceHighName = (w: number) => `Δ High(${w}d)`;

/** The band geometry mirrors derivTrend/scale.ts: the [0,100] band of the
 *  fixed 0-400 axis, zero pinned at 50. */
const BAND_ZERO_POS = 50;

/** Max |change| across BOTH sides (finite values only) — one shared
 *  scale keeps the two sides' magnitudes comparable; 1 when both are
 *  empty/all-null so the scale stays finite. */
export function changeSinceMaxAbsOf(
  arrays: Array<Array<number | null>>,
): number {
  let maxAbs = 0;
  for (const arr of arrays) {
    for (const v of arr) {
      if (v != null && Number.isFinite(v)) {
        maxAbs = Math.max(maxAbs, Math.abs(v));
      }
    }
  }
  if (!(maxAbs > 0) || !Number.isFinite(maxAbs)) maxAbs = 1;
  return maxAbs;
}

/** Raw change value → its band position on the 0-400 axis. */
const toChangeSinceBand = (v: number, maxAbs: number): number =>
  BAND_ZERO_POS + v * (BAND_ZERO_POS / maxAbs);

/** Axis tick position inside the band → back in original units. */
const changeSinceBandToOriginal = (v: number, maxAbs: number): number =>
  ((v - BAND_ZERO_POS) / BAND_ZERO_POS) * maxAbs;

/** The two change lines on the offset right axis (z 3, band-mapped);
 *  the Δ Low line carries the dashed zero guide — the central
 *  separation line between the sides. `maxAbs` is the shared scale from
 *  changeSinceMaxAbsOf (computed once by the caller for the axis too). */
export function appendChangeSinceSeries(
  series: SeriesList,
  state: RoofFloorState,
  axisLineColor: string,
  maxAbs: number,
): void {
  if (!(state.rfAligned && state.rfW != null) || state.roofFloorWinArrays == null) return;
  const w = state.rfW;
  const lowData = state.roofFloorWinArrays.changeSinceLastLow;
  const highData = state.roofFloorWinArrays.changeSinceLastHigh;
  const zeroGuide = {
    markLine: {
      silent: true as const,
      symbol: ["none", "none"] as [string, string],
      label: { show: false },
      lineStyle: {
        type: "dashed" as const,
        color: axisLineColor,
        width: 1,
        opacity: 0.55,
      },
      data: [{ yAxis: BAND_ZERO_POS }],
    },
  };
  series.push({
    type: "line",
    name: changeSinceLowName(w),
    data: lowData.map((v) =>
      v != null && Number.isFinite(v) ? toChangeSinceBand(v, maxAbs) : null,
    ),
    symbol: "none",
    yAxisIndex: 1,
    lineStyle: { color: FLOOR_COLOR, width: 1.2, opacity: 0.85 },
    z: 3,
    clip: true,
    ...zeroGuide,
  });
  series.push({
    type: "line",
    name: changeSinceHighName(w),
    data: highData.map((v) =>
      v != null && Number.isFinite(v) ? toChangeSinceBand(v, maxAbs) : null,
    ),
    symbol: "none",
    yAxisIndex: 1,
    lineStyle: { color: ROOF_COLOR, width: 1.2, opacity: 0.85 },
    z: 3,
    clip: true,
  });
}

/** Band-offset change axis (the RSI/deriv overlay's pattern): fixed 0-400
 *  range so the transformed lines (band [0,100], zero pinned at 50) hug
 *  the bottom quarter of the shared grid; tick labels inside the band
 *  read back in ORIGINAL units — −maxAbs at the band bottom, 0 at the
 *  central line, +maxAbs at its top; labels above 100 stay blank (the
 *  100-400 stretch is reserved headroom, not data). No axis name — the
 *  legend already reaches this corner and the name collided with its
 *  last entry (the caption + tooltip carry the meaning). */
export function buildChangeSinceRightAxis(
  c: AxisColors,
  maxAbs: number,
) {
  return {
    type: "value" as const,
    min: 0,
    max: 400,
    interval: 50,
    nameTextStyle: { color: c.textColor, fontSize: 9 },
    axisLine: { lineStyle: { color: c.axisLineColor } },
    axisLabel: {
      color: c.textColor,
      fontSize: 9,
      formatter: (v: number) =>
        v <= 100 ? fmtNum(changeSinceBandToOriginal(v, maxAbs)) : "",
    },
    splitLine: { show: false },
  };
}
