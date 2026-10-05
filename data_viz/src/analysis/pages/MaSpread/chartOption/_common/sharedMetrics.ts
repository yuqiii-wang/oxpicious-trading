/**
 * Null-safe reads from the chart response's shared per-date metrics row
 * (RSI windows, trading-amt slopes/shares, rolling-window OHLC) — one
 * copy per date, index-aligned with every pair's rows.
 */
import type { MovAveSpreadSharedMetricsRow } from "@shared/types";

/** Numeric read from the shared row at index `i`; missing → null. */
export function sharedNum(
  shared: MovAveSpreadSharedMetricsRow[] | null,
  i: number,
  key: keyof MovAveSpreadSharedMetricsRow,
): number | null {
  return (shared?.[i]?.[key] as number | null | undefined) ?? null;
}

/** A whole shared column, index-aligned with the chart rows. */
export function sharedColumn(
  shared: MovAveSpreadSharedMetricsRow[] | null,
  n: number,
  key: keyof MovAveSpreadSharedMetricsRow,
): Array<number | null> {
  return Array.from({ length: n }, (_, i) => sharedNum(shared, i, key));
}
