/**
 * chart/types — shared INTERNAL types for the Margin Trends chart layer.
 *
 * Everything here is derived client-side from MarginIndustrySeriesResponse
 * by chart/derive.ts and consumed by the option builders
 * (trendChartOption / hypedDrainedOption) and the tooltip builder
 * (trendTooltip). None of it crosses the API boundary.
 */
import type { MarketRegime } from "@shared/types";

/** Per-(code, date) derived analytics point.
 *
 *  ret       — daily % return of the security's own close series
 *  fwd1/5/20 — FORWARD % returns (close→close) over the next 1/5/20 rows,
 *              measured FROM this date; null at the series tail
 *  vol20     — trailing 20-bar sample std of ret (%); null with <5 values
 *  buyShare  — rz_buy / trading_amount (fraction). For index attribution a
 *              scale-offset share (weighted-AVG rz_buy vs whole-index
 *              turnover) — valid for RANK-based regime bucketing only.
 *  tercile   — per-code buyShare rank tercile: 0 drained / 1 mid / 2 hyped;
 *              -1 unknown (null share or too few share days) */
export interface DerivedPoint {
  ret: number | null;
  fwd1: number | null;
  fwd5: number | null;
  fwd20: number | null;
  vol20: number | null;
  buyShare: number | null;
  tercile: number;
}

/** One margin-intensity regime bin (buyShare tercile) of one code. */
export interface HypedBin {
  n: number;
  meanAbsRet: number | null;
  meanRet: number | null;
  /** Share of days with ret > 0 (0..1). */
  hitRate: number | null;
}

/** Per canonical market-regime (calm/hot/panic/quiet) bin PAIR of one
 *  code: [drained (low buyShare tercile), hyped (high)] — the Mid tercile
 *  carries no contrast and is dropped from the regime split. */
export type RegimeBins = Record<MarketRegime, [HypedBin, HypedBin]>;

/** All derived analytics for one security code. */
export interface CodeDerived {
  code: string;
  /** date → derived point (only dates where the code has a row). */
  points: Map<string, DerivedPoint>;
  /** date → close (code's own rows). */
  close: Map<string, number | null>;
  /** [low, mid, high] buyShare-tercile bins; null when terciles unavailable. */
  bins: [HypedBin, HypedBin, HypedBin] | null;
  /** Drained/hyped bin pair per canonical market regime; null when
   *  terciles unavailable. The regime×tercile amplification view. */
  regimeBins: RegimeBins | null;
  /** q33 / q66 buyShare cutpoints (fractions); null when unavailable. */
  cutpoints: [number, number] | null;
}

/** Derived analytics for the whole plot (all codes in the response). */
export interface MarginAnalytics {
  byCode: Map<string, CodeDerived>;
}

/** Pivoted plot values: margin series (in 亿) + close per code per date. */
export interface PivotResult {
  dates: string[];
  codeMargin: Map<string, Map<string, number | null>>;
  codeClose: Map<string, Map<string, number | null>>;
}

// ---- Shared series-name suffixes (option builders + tooltip MUST agree) ----
// Chart labels are ENGLISH (user preference); security names stay as data.
export const RET_SUFFIX = " · Ret%";
export const VOL_SUFFIX = " · Vol20d";

/** RZ-buy-share display unit: ‱ (per-ten-thousand, U+2031). Index attribution
 *  shares are scale-offset (weighted-AVG constituent rz_buy vs WHOLE-index
 *  turnover) and run ~0.4–1.0‱ — % needed 3+ decimals to show anything; ETF
 *  true shares are ~500–1500‱. 2 decimals in ‱ = 6 in %. */
export const SHARE_UNIT = "‱";

/** Format a share FRACTION (0..1) in ‱ with `decimals` places; "—" for null. */
export function fmtShare(frac: number | null, decimals = 2): string {
  return frac != null && Number.isFinite(frac)
    ? `${(frac * 10000).toFixed(decimals)}${SHARE_UNIT}`
    : "—";
}
