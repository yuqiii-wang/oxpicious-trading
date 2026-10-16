/**
 * families/macross — the ma_cross family (signal_type "ma_cross"): the
 * display label + the window-only record labels ("ma20_retest" →
 * "MA 20"); the sub-type's KIND word (cross / retest / gap...) never
 * surfaces in history records — the side / action columns carry
 * buy/sell. (The curve-axis + threshold-bar machinery was retired with
 * the curve overlay 2026-10-15.)
 */
import type { SignalFamily } from "./index";

/** The picker GROUP of one sub_type literal — the window cell
 *  ("ma20_cross" → "ma20", EMA twins → "ema20"); null for non-ma
 *  literals. */
export function maSeriesGroup(subType: string): string | null {
  const m = /^(ma\d+)_/.exec(subType);
  if (!m) return null;
  return /^ma\d+_e/.test(subType) ? `ema${m[1]!.slice(2)}` : m[1]!;
}

/** One GROUP literal's display word ("ma20" → "MA 20", "ema120" →
 *  "EMA 120") — the window only, NEVER the kind word. */
export function seriesGroupWord(group: string): string {
  const m = /^(ma|ema)(\d+)$/.exec(group);
  if (!m) return group;
  return `${m[1] === "ema" ? "EMA" : "MA"} ${m[2]!}`;
}

/** The window-only display word for one record's sub_type —
 *  "ma20_retest" → "MA 20", "ma255_ed8pct" → "EMA 255". Null for
 *  non-ma literals. */
export function maSubTypeLabel(subType: string): string | null {
  const g = maSeriesGroup(subType);
  return g == null ? null : seriesGroupWord(g);
}

export const maCrossFamily: SignalFamily = {
  label: "MA Cross",
  recordLabel: maSubTypeLabel,
};
