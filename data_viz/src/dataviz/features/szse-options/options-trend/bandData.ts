/**
 * Band data shaping — expiry cohort grouping and per-strike cell generation.
 *
 * The key change from the original ExpiryOiBandsPanel: `buildCells` now
 * accepts the FULL trading date range and produces null/empty BandCell
 * entries for dates where the selected expiry has no data, so the x-axis
 * aligns with the other trend charts (P/C Ratio, Total OI).
 */
import type { OptionsRow } from "@shared/types";

export interface ExpiryCohort {
  key: string;
  label: string;
  totalOi: number;
}

export interface BandCell {
  value: [number, number];
  date: string;
  strikeY: number;
  callOi: number;
  putOi: number;
  totalOi: number;
  putPct: number;
  h: number;
  strength: number;
}

/** Band thickness range in px — thickness ∝ sqrt-scaled total OI. */
export const BAND_H_MIN = 3;
export const BAND_H_MAX = 26;
export const COLOR_STRENGTH_MIN = 0.3;
export const COLOR_STRENGTH_POWER = 0.65;

/** Put% thresholds for the dominance boundary curves (putPct is per-strike). */
export const PUT_PCT_GREEN = 20; // bullish side: calls > 80% (putPct ≤ 20%)
export const PUT_PCT_RED = 80; // bearish side: puts > 80% (putPct ≥ 80%)

export const BULL_THRESHOLD_SERIES_NAME = ">80% Calls (Bull)";
export const BEAR_THRESHOLD_SERIES_NAME = ">80% Puts (Bear)";

export function buildCohorts(rows: OptionsRow[]): ExpiryCohort[] {
  const byExpiry = new Map<string, number>();
  for (const r of rows) {
    byExpiry.set(r.expiry_date, (byExpiry.get(r.expiry_date) ?? 0) + r.open_interest);
  }
  return Array.from(byExpiry.entries())
    .map(([key, totalOi]) => ({
      key,
      label: key.length >= 7 ? key.slice(0, 7) : key,
      totalOi,
    }))
    .sort((a, b) => a.key.localeCompare(b.key));
}

/**
 * Build cells across one or more expiry cohorts, aggregated onto the FULL
 * trading date range. Returns cells with valid OI data for dates that have
 * any of the selected expiries, plus the full spot price series aligned to
 * allDates. Aggregating multiple expiries sums OI per (date, strike).
 */
export function buildCells(
  rows: OptionsRow[],
  expiryKeys: string[],
  allDates: string[],
): {
  dates: string[];
  cells: BandCell[];
  spot: (number | null)[];
  oiMax: number;
} {
  const expirySet = new Set(expiryKeys);
  const byDate = new Map<
    string,
    { strikes: Map<number, { c: number; p: number }>; spotRaw: number }
  >();

  for (const r of rows) {
    if (!expirySet.has(r.expiry_date)) continue;
    let d = byDate.get(r.date);
    if (!d) {
      d = { strikes: new Map(), spotRaw: r.underlying_close };
      byDate.set(r.date, d);
    }
    const cell = d.strikes.get(r.strike_price) ?? { c: 0, p: 0 };
    if (r.option_type === "CALL") cell.c += r.open_interest;
    else cell.p += r.open_interest;
    d.strikes.set(r.strike_price, cell);
  }

  // Use the passed-in allDates (full trading range) for x-axis alignment.
  // Only include cells for dates that have data for this expiry.
  const cells: BandCell[] = [];
  let oiMax = 1;

  allDates.forEach((date, xi) => {
    const d = byDate.get(date);
    if (!d) return; // No data for this expiry on this date — skip cell
    for (const [k, cell] of d.strikes) {
      const totalOi = cell.c + cell.p;
      if (totalOi <= 0) continue;
      if (totalOi > oiMax) oiMax = totalOi;
      cells.push({
        value: [xi, k / 10000],
        date,
        strikeY: k / 10000,
        callOi: cell.c,
        putOi: cell.p,
        totalOi,
        putPct: (cell.p / totalOi) * 100,
        h: 0,
        strength: 0,
      });
    }
  });

  // Second pass — thickness + darkness
  for (const cell of cells) {
    const frac = cell.totalOi / oiMax;
    cell.h = BAND_H_MIN + (BAND_H_MAX - BAND_H_MIN) * Math.sqrt(frac);
    cell.strength =
      COLOR_STRENGTH_MIN + (1 - COLOR_STRENGTH_MIN) * Math.pow(frac, COLOR_STRENGTH_POWER);
  }

  // Spot series aligned to allDates (null for dates with no data)
  const spot: (number | null)[] = allDates.map((dt) => {
    const d = byDate.get(dt);
    return d ? d.spotRaw / 10000 : null;
  });

  return { dates: allDates, cells, spot, oiMax };
}

/**
 * Remap cells from one date array to another (e.g. from allDates to
 * brokenDates which may have extra gap-break entries). Also remaps the
 * spot series to match the broken dates array.
 */
export function remapCells(
  cells: BandCell[],
  spot: (number | null)[],
  fromDates: string[],
  toDates: string[],
): { cells: BandCell[]; spot: (number | null)[] } {
  // Build index map: for each date in toDates, find its position in fromDates
  // (first occurrence only, since duplicates are gap-break markers)
  const dateToFromIdx = new Map<number, number>(); // toIdx -> fromIdx
  const seenFromIdx = new Set<number>();
  toDates.forEach((date, toIdx) => {
    const fromIdx = fromDates.indexOf(date);
    if (fromIdx >= 0 && !seenFromIdx.has(fromIdx)) {
      dateToFromIdx.set(toIdx, fromIdx);
      seenFromIdx.add(fromIdx);
    }
  });

  // Remap cells: only keep cells whose original index maps to a new index
  const remappedCells: BandCell[] = [];
  for (const cell of cells) {
    const origIdx = cell.value[0];
    // Find which toIdx this fromIdx maps to
    let newIdx = -1;
    for (const [toIdx, fi] of dateToFromIdx) {
      if (fi === origIdx) {
        newIdx = toIdx;
        break;
      }
    }
    if (newIdx < 0) continue;
    remappedCells.push({
      ...cell,
      value: [newIdx, cell.value[1]],
    });
  }

  // Remap spot: align to toDates, using original spot values at matching positions
  const remappedSpot: (number | null)[] = toDates.map((_, toIdx) => {
    const fromIdx = dateToFromIdx.get(toIdx);
    if (fromIdx == null) return null;
    return spot[fromIdx] ?? null;
  });

  return { cells: remappedCells, spot: remappedSpot };
}
