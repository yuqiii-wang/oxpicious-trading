/**
 * Band EVENT markers — read straight from the bands table's ANCHOR-BAND
 * event DATE arrays (analysis.mov_ave_spreads_roof_and_floor_bands,
 * populated by the analysis.mov_ave_spread pipeline): the ANCHOR row's
 * breach/bounce arrays per window are the stored events of the EXACT
 * band the shade rects draw for that anchor — crossings of the band's
 * NEAR edge (roof bottom / floor top), classified ±1σ py-side (bounce
 * = poke within +1σ + next-day turn-back; breach = a DIRECTIONAL
 * entry from OUTSIDE — prior close at/below the roof's drawn edge,
 * day closing beyond edge + 1σ upward; mirrored downward on the
 * floor; a close already inside the band never breaches). The
 * UI only RENDERS them: SOLID triangle = bounce, NAIL pin (tip in
 * the break's direction) = breach, both ON the window's drawn near
 * edge.
 *
 * Markers re-anchor with the clicked date by construction (each row's
 * events belong to its own band — no client-side recomputation or
 * windowing); the cut filter hides windows whose band was cut away
 * (drawnRoofBot/drawnFloorTop only hold survivors). The 60d guard
 * fallback band carries no markers (a display fallback, not an anchor
 * band — no stored events).
 */
import type { MovAveSpreadBandsRow } from "@shared/types";

export interface BandEventItems {
  bounceUp: Array<{ value: [number, number] }>;
  bounceDn: Array<{ value: [number, number] }>;
  breachUp: Array<{ value: [number, number] }>;
  breachDn: Array<{ value: [number, number] }>;
}

/** Map the anchor row's stored event-date arrays to scatter items, one
 *  marker per (event day, window, side) sitting on that window's drawn
 *  near edge. Stacked markers from several windows on the same day =
 *  confluence. */
export function readBandEvents(args: {
  n: number;
  dates: string[];
  bandRows: MovAveSpreadBandsRow[] | null;
  shadeAnchorIdx: number;
  drawnRoofBot: Map<number, number>;
  drawnFloorTop: Map<number, number>;
  shadeWindows: readonly number[] | null;
}): BandEventItems {
  const {
    n, dates, bandRows, shadeAnchorIdx,
    drawnRoofBot, drawnFloorTop, shadeWindows,
  } = args;
  const items: BandEventItems = {
    bounceUp: [], bounceDn: [], breachUp: [], breachDn: [],
  };
  if (shadeWindows == null || bandRows == null || bandRows.length !== n) return items;
  const anchorRow = bandRows[shadeAnchorIdx] as unknown as
    | Record<string, unknown>
    | undefined;
  if (anchorRow == null) return items;
  const dateIdx = new Map<string, number>();
  for (let i = 0; i < n; i++) dateIdx.set(dates[i], i);
  const take = (
    key: string, y: number, out: Array<{ value: [number, number] }>,
  ): void => {
    const arr = anchorRow[key];
    if (!Array.isArray(arr)) return;
    for (const d of arr) {
      const i = dateIdx.get(d);
      // The stored days are inside the row's own window by construction;
      // the index guards are belt-and-braces against off-chart dates.
      if (i != null && i >= 1 && i <= shadeAnchorIdx) out.push({ value: [i, y] });
    }
  };
  for (const w of shadeWindows) {
    const rBot = drawnRoofBot.get(w);
    const fTop = drawnFloorTop.get(w);
    if (rBot != null) {
      take(`upper_bounce_dates_${w}d`, rBot, items.bounceUp);
      take(`upper_breach_dates_${w}d`, rBot, items.breachUp);
    }
    if (fTop != null) {
      take(`lower_bounce_dates_${w}d`, fTop, items.bounceDn);
      take(`lower_breach_dates_${w}d`, fTop, items.breachDn);
    }
  }
  return items;
}
