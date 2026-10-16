/**
 * Roof/floor REBOUND-AUDIT chart marks — the "rfaudit" events drawn on
 * the pair chart for the enabled roof/floor window W:
 *
 *   ▲/▼ TRIANGLE at (eventDate, level) for every recorded extreme that
 *      REBOUNDED ≥5% within 60 trading days (roof events point up in
 *      ROOF_COLOR, floor events point down in FLOOR_COLOR — the anchor
 *      markers' orientation convention), drawn LARGER than a bare
 *      extreme so the events read from a distance.
 *
 *   Two cohort filters keep the chart uncluttered — an event draws only
 *   when it happened STRICTLY BEFORE the chosen date (the clicked
 *   anchor date, latest by default), and its level sits within ±5% of
 *   its side's median event level (the MEDIAN ROOF / MEDIAN FLOOR of
 *   that before-date cohort; the same 5% threshold the rebound
 *   definition uses). Events after the chosen date and events at
 *   far-from-typical levels draw nothing.
 *
 * The markers are an annotation layer like the anchor markers: not in
 * the legend, gone when the window is off. Values are raw price units
 * on the shared value axis (the % mode only relabels the axis).
 */
import type { MovAveSpreadRoofFloorRebound } from "@shared/types";
import type { SeriesList } from "../_common";
import { ROOF_COLOR, FLOOR_COLOR } from "./colors";

/** Marker size — larger than the 7px it replaced so the qualifying
 *  events read from a distance. */
const EVENT_SIZE = 11;

/** A level counts as "nearby the median roof/floor" when it sits within
 *  this fraction of its side's median event level. */
const MEDIAN_TOLERANCE = 0.05;

/** One audit scatter item (value = [dateIndex, level]). */
export interface ReboundMarkItem {
  value: [number, number];
  symbol: string;
  symbolRotate: number;
  symbolSize: number;
  itemStyle: { color: string };
}

export interface ReboundAuditMarks {
  /** Triangles at the qualifying rebounded extremes (roof up / floor
   *  down) — after the date + median-level filters. */
  items: ReboundMarkItem[];
  nEvents: number;
  nSkipped: number;
}

/** Median of a numeric list (mean of the two middles for even length). */
function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = sorted.length >> 1;
  return sorted.length % 2 === 1
    ? sorted[mid]
    : (sorted[mid - 1] + sorted[mid]) / 2;
}

export function buildReboundAuditMarks(
  rows: MovAveSpreadRoofFloorRebound[] | null,
  w: number,
  n: number,
  dates: string[],
  clickIdx: number | null,
): ReboundAuditMarks | null {
  if (rows == null || rows.length === 0) return null;
  const dateIdx = new Map(dates.map((d, i) => [d, i]));
  const chosenIdx =
    clickIdx != null && clickIdx >= 0 && clickIdx < n ? clickIdx : n - 1;

  // Cohort: rebounded events strictly before the chosen date, mapped to
  // chart positions. Events at/after the chosen date are dropped here so
  // they never enter the per-side median below; unmappable dates (outside
  // the chart's range) count as skipped.
  const positioned: Array<{ roof: boolean; i: number; level: number }> = [];
  let nSkipped = 0;
  for (const r of rows) {
    if (r.period !== w || r.outcome === "no_rebound") continue;
    const i = dateIdx.get(r.eventDate);
    if (i == null || i < 0 || i >= n) {
      nSkipped += 1;
      continue;
    }
    if (i >= chosenIdx) continue;
    positioned.push({ roof: r.side === "roof", i, level: r.level });
  }

  // Per-side median level of the cohort — the median roof / median floor.
  const medianRoof = median(positioned.filter((e) => e.roof).map((e) => e.level));
  const medianFloor = median(positioned.filter((e) => !e.roof).map((e) => e.level));

  const items: ReboundMarkItem[] = [];
  for (const e of positioned) {
    const med = e.roof ? medianRoof : medianFloor;
    if (med == null || Math.abs(e.level - med) > MEDIAN_TOLERANCE * Math.abs(med)) {
      continue;
    }
    items.push({
      value: [e.i, e.level],
      symbol: "triangle",
      symbolRotate: e.roof ? 0 : 180,
      symbolSize: EVENT_SIZE,
      itemStyle: { color: e.roof ? ROOF_COLOR : FLOOR_COLOR },
    });
  }
  return { items, nEvents: items.length, nSkipped };
}

/** Push the audit mark series (one annotation layer, z=14; no legend
 *  entry and no item tooltips — the anchor markers' convention). */
export function appendReboundAuditSeries(
  series: SeriesList,
  marks: ReboundAuditMarks | null,
  w: number,
): void {
  if (marks == null || marks.items.length === 0) return;
  series.push({
    type: "scatter",
    name: `_rfReboundMarks${w}`,
    data: marks.items,
    symbolSize: EVENT_SIZE,
    z: 14,
    clip: true,
    tooltip: { show: false },
  });
}
