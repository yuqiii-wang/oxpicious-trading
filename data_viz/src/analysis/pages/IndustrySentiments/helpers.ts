/**
 * Pure helper functions for the Industry Sentiments analysis page.
 */
import type { PoolSize } from "./types";

/**
 * Classify a stock_num into a pool_size bucket. NULL → null (no bucket).
 * small <51, mid 51-180, large >180.
 */
export function classifyPoolSize(stockNum: number | null): PoolSize | null {
  if (stockNum == null) return null;
  if (stockNum < 51) return "small";
  if (stockNum <= 180) return "mid";
  return "large";
}

/**
 * Rebase an index's close series to 100 at the first non-null close within
 * the visible window [lo, hi].
 */
export function rebaseTo100(
  closes: Array<number | null>,
  lo: number,
  hi: number,
): Array<number | null> {
  const n = closes.length;
  const start = Math.max(0, Math.min(lo, n - 1));
  const end = Math.max(0, Math.min(hi, n - 1));
  let rebasePoint: number | null = null;
  for (let i = start; i <= end; i++) {
    const v = closes[i];
    if (v != null && Number.isFinite(v) && Math.abs(v) > 1e-9) {
      rebasePoint = v;
      break;
    }
  }
  if (rebasePoint == null) return closes.map(() => null);
  return closes.map((v) =>
    v == null || !Number.isFinite(v) ? null : (v / rebasePoint) * 100,
  );
}
