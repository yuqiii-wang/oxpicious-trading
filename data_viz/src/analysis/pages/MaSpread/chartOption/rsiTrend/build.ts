/**
 * RSI Trends overlay (the header switch's "rsi" position) — the shared
 * block's Wilder-RSI window lines (3/6/10/14/20/60d), replacing the
 * trading-amt bars on the pair chart's offset right axis.
 */
import type { MovAveSpreadSharedMetricsRow } from "@shared/types";
import { MUTED_PALETTE } from "@/theme/chart-palette";
import { sharedColumn } from "../_common/sharedMetrics";

/** One Wilder-RSI window line (legend name + per-date values + color). */
export interface RsiTrendLine {
  name: string;
  data: Array<number | null>;
  color: string;
}

/** The six RSI windows in draw order, keyed by window length. */
export type RsiArrays = Record<3 | 6 | 10 | 14 | 20 | 60, Array<number | null>>;

/** The six Wilder-RSI window arrays from the shared metrics block —
 *  built for every pair (the tooltip's RSI row reads them even while
 *  the overlay is off). */
export function buildRsiArrays(
  shared: MovAveSpreadSharedMetricsRow[] | null,
  n: number,
): RsiArrays {
  return {
    3: sharedColumn(shared, n, "rsi_3days"),
    6: sharedColumn(shared, n, "rsi_6days"),
    10: sharedColumn(shared, n, "rsi_10days"),
    14: sharedColumn(shared, n, "rsi_14days"),
    20: sharedColumn(shared, n, "rsi_20days"),
    60: sharedColumn(shared, n, "rsi_60days"),
  };
}

/** The six per-window tail-episode flag arrays from the shared metrics
 *  block (1 = the first-touch pass's days / 0 = the re-entry pass's
 *  days / null = out of band — the rsi analysis layer's
 *  is_typical_{W}days; the gap-1 continuation days inherit their
 *  episode pass's flag). Same shape as RsiArrays. */
export function buildRsiFlagArrays(
  shared: MovAveSpreadSharedMetricsRow[] | null,
  n: number,
): RsiArrays {
  return {
    3: sharedColumn(shared, n, "is_typical_3days"),
    6: sharedColumn(shared, n, "is_typical_6days"),
    10: sharedColumn(shared, n, "is_typical_10days"),
    14: sharedColumn(shared, n, "is_typical_14days"),
    20: sharedColumn(shared, n, "is_typical_20days"),
    60: sharedColumn(shared, n, "is_typical_60days"),
  };
}

/** Legend name → window array, colored from the muted multi-series
 *  palette short (blue) → long (teal). `windows` (when non-empty)
 *  restricts the drawn lines to those window lengths (the RSI Trends
 *  section's days row pick). `population` (when set) further nulls
 *  every date whose day-level tail-episode flag does not match —
 *  "typical" keeps the first-touch episodes' days (flag 1: each
 *  episode's first tail day AND its gap-1 continuation days — the
 *  deep path), "re-entry" keeps the same-episode re-registrations'
 *  days (flag 0, their continuations included) — so the drawn line
 *  visits only that population's days. */
export function rsiTrendLines(
  arrays: RsiArrays,
  windows?: readonly number[] | null,
  population?: "typical" | "re-entry" | null,
  flags?: RsiArrays | null,
): RsiTrendLine[] {
  const all: RsiTrendLine[] = [
    { name: "RSI3", data: arrays[3], color: MUTED_PALETTE[0] },
    { name: "RSI6", data: arrays[6], color: MUTED_PALETTE[1] },
    { name: "RSI10", data: arrays[10], color: MUTED_PALETTE[2] },
    { name: "RSI14", data: arrays[14], color: MUTED_PALETTE[3] },
    { name: "RSI20", data: arrays[20], color: MUTED_PALETTE[4] },
    { name: "RSI60", data: arrays[60], color: MUTED_PALETTE[5] },
  ];
  let picked = all;
  if (windows != null && windows.length > 0) {
    const set = new Set(windows);
    picked = picked.filter((l) => set.has(Number(l.name.slice(3))));
  }
  if (population == null || flags == null) return picked;
  const want = population === "typical" ? 1 : 0;
  return picked.map((l) => {
    const w = Number(l.name.slice(3)) as keyof RsiArrays;
    const f = flags[w];
    return {
      ...l,
      data: l.data.map((v, i) => (f[i] === want ? v : null)),
    };
  });
}
