/**
 * Percentage display-mode base: the "first valid value" both option
 * builders rebase against, and the shared fmtPrice factory (% change
 * from the base in "percentage" mode, raw number otherwise).
 */
import { fmtNum, fmtPct } from "@/lib/series";
import type { OhlcMode } from "@/lib/ohlc";

/** The first finite, non-near-zero value of the series — the % rebase
 *  base ("first valid close" semantics). */
export function firstValidBase(values: Array<number | null>): number | null {
  for (const v of values) {
    if (v != null && Number.isFinite(v) && Math.abs(v) >= 1e-9) return v;
  }
  return null;
}

export interface PctBase {
  baseVal: number | null;
  /** ⇔ baseVal != null — the selection above already enforces finite
   *  and |v| ≥ 1e-9. */
  hasBase: boolean;
}

/** Base + hasBase from the series. */
export function pctBaseOf(values: Array<number | null>): PctBase {
  const baseVal = firstValidBase(values);
  return { baseVal, hasBase: baseVal != null };
}

/** fmtPrice: % change vs the base in percentage mode, raw number in
 *  absolute mode; "—" when the value or the base is missing. */
export function makeFmtPrice(
  ohlcMode: OhlcMode,
  base: PctBase,
): (v: number | null | undefined) => string {
  return (v) => {
    if (v == null || !Number.isFinite(v) || !base.hasBase || base.baseVal == null) {
      return "—";
    }
    if (ohlcMode === "percentage") {
      return fmtPct((v / base.baseVal - 1) * 100, 2);
    }
    return fmtNum(v);
  };
}
