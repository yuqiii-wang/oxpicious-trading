/**
 * Band data shaping — expiry cohort grouping and per-strike cell generation.
 *
 * The key change from the original ExpiryOiBandsPanel: `buildCells` now
 * accepts the FULL trading date range and produces null/empty BandCell
 * entries for dates where the selected expiry has no data, so the x-axis
 * aligns with the other trend charts (P/C Ratio, Total OI).
 */
import type { OptionsRow, OptionsWallRow } from "@shared/types";

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
export const CALL_ZONE_SERIES_NAME = "Call Zone Wall";
export const PUT_ZONE_SERIES_NAME = "Put Zone Wall";

/** One dominant zone wall per (date, side), scaled to the chart's
 *  strike axis (raw strike / 10000, same as BandCell.strikeY). */
export interface ZoneWallPoint {
  date: string;
  optionType: "CALL" | "PUT";
  expiryDate: string;
  low: number;
  high: number;
  center: number;
  wallOi: number;
  massShare: number | null;
  gapPct: number | null;
  daysPersisted: number | null;
  state: "ACTIVE" | "ERODED" | "BREACHED" | null;
  strength: number | null;
}

/** Per-date dominant zone walls for each side, aligned to the dates array
 *  (null where the selection has no zone on that date). */
export interface ZoneWallSeries {
  call: (ZoneWallPoint | null)[];
  put: (ZoneWallPoint | null)[];
}

/**
 * Build per-date dominant zone walls from analysis.options_walls rows,
 * restricted to the selected expiry cohorts.
 *
 * The backend emits the DOMINANT zone per (date, expiry, side) — but the
 * walls build collapses all OPEN expiries (expiry > dataset max date)
 * into a single pseudo group whose expiry_date is the mean of the real
 * expiry dates (never present in the quote rows' real expiry set). Wall
 * rows therefore match the selection when:
 *   - expiry_date equals a selected cohort's real expiry (closed
 *     cohorts), or
 *   - the row belongs to the open-chain pseudo group (expiry_date not in
 *     `realExpiries`) AND the selection contains open cohorts.
 *
 * Among matching groups the zone with the highest strength score
 * (tie-break: wall OI) is shown per (date, side).
 */
export function buildZoneWalls(
  wallRows: OptionsWallRow[],
  expiryKeys: string[],
  allDates: string[],
  opts: { realExpiries: Set<string>; includeOpenChain: boolean },
): ZoneWallSeries {
  const expirySet = new Set(expiryKeys);
  const { realExpiries, includeOpenChain } = opts;
  const dateIdx = new Map<string, number>();
  allDates.forEach((d, i) => {
    if (!dateIdx.has(d)) dateIdx.set(d, i);
  });

  const call: (ZoneWallPoint | null)[] = new Array(allDates.length).fill(null);
  const put: (ZoneWallPoint | null)[] = new Array(allDates.length).fill(null);

  const rank = (r: OptionsWallRow): number =>
    (r.strength_score ?? -1) * 1e9 + (r.wall_oi ?? 0);

  for (const r of wallRows) {
    if (r.wall_type !== "zone") continue;
    if (expirySet.has(r.expiry_date)) {
      // closed cohort — exact match
    } else if (includeOpenChain && !realExpiries.has(r.expiry_date)) {
      // collapsed open chain (pseudo expiry) — covers open cohorts
    } else {
      continue;
    }
    const xi = dateIdx.get(r.date);
    if (xi == null) continue;
    if (r.wall_low == null || r.wall_high == null || r.wall_center == null) continue;

    const point: ZoneWallPoint = {
      date: r.date,
      optionType: r.option_type,
      expiryDate: r.expiry_date,
      low: r.wall_low / 10000,
      high: r.wall_high / 10000,
      center: r.wall_center / 10000,
      wallOi: r.wall_oi ?? 0,
      massShare: r.mass_share,
      gapPct: r.gap_pct,
      daysPersisted: r.days_persisted,
      state: r.state,
      strength: r.strength_score,
    };
    const target = r.option_type === "PUT" ? put : call;
    const cur = target[xi];
    if (cur == null || rank(r) > (cur.strength ?? -1) * 1e9 + cur.wallOi) {
      target[xi] = point;
    }
  }

  return { call, put };
}

/**
 * Remap zone wall series from one date array to another (e.g. allDates →
 * brokenDates with extra gap-break entries), keeping the first occurrence
 * of each source index (same rule as remapCells).
 */
export function remapZones(
  zones: ZoneWallSeries,
  fromDates: string[],
  toDates: string[],
): ZoneWallSeries {
  const remap = (arr: (ZoneWallPoint | null)[]): (ZoneWallPoint | null)[] => {
    const out: (ZoneWallPoint | null)[] = new Array(toDates.length).fill(null);
    const seen = new Set<number>();
    toDates.forEach((date, toIdx) => {
      const fromIdx = fromDates.indexOf(date);
      if (fromIdx >= 0 && !seen.has(fromIdx)) {
        seen.add(fromIdx);
        out[toIdx] = arr[fromIdx] ?? null;
      }
    });
    return out;
  };
  return { call: remap(zones.call), put: remap(zones.put) };
}

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
