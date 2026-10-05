/**
 * Top-10% OHLC Days group (the "spike fade" / "flush recover" toggles) —
 * the gaps store's per-day anatomy aligned to the chart's date axis.
 * Both option builders (pair chart + Amt Envelope) build the same
 * tooltip info; the chart-side shades for the toggles are assembled in
 * the panel and rendered through the shared overlay carrier.
 */
import type { MovAveSpreadOhlcGapDay } from "@shared/types";

/** The gaps store's per-date anatomy, index-aligned with the chart's
 *  dates — null when the group is off or not loaded. */
export interface GapTooltipInfo {
  gap: Array<number | null>;
  intra: Array<number | null>;
  uw: Array<number | null>;
  lw: Array<number | null>;
  pattern: Array<string | null>;
}

/** Align the gaps rows to the chart's date axis (active only when a
 *  toggle is on and the rows landed). */
export function buildGapInfo(
  ohlcGapRows: MovAveSpreadOhlcGapDay[] | null,
  ohlcGapActive: boolean,
  dates: string[],
): GapTooltipInfo | null {
  if (ohlcGapRows == null || !ohlcGapActive) return null;
  const gapByDate = new Map(ohlcGapRows.map((d) => [d.date, d]));
  const pick = (
    pickFn: (d: MovAveSpreadOhlcGapDay) => number | null,
  ) => dates.map((dt) => {
    const d = gapByDate.get(dt);
    return d == null ? null : pickFn(d);
  });
  return {
    gap: pick((d) => d.gap_open),
    intra: pick((d) => d.intra),
    uw: pick((d) => d.uw),
    lw: pick((d) => d.lw),
    pattern: dates.map((dt) => gapByDate.get(dt)?.pattern ?? null),
  };
}
