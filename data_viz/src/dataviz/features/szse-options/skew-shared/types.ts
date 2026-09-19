/**
 * Shared skew-over-time model — one shape for ALL skew data sources
 * (skew_type in analysis.options_skewness_stats):
 *
 *   • 'oi_moneyness' — OI-weighted mean moneyness (strike/spot), a
 *     POSITIONING metric. Skew price = S × E[M], Δ% = (E[M] − 1) × 100.
 *   • 'greek_delta'  — delta-weighted put/call OI ratio dpcr (whole
 *     chain, neutral 0.5 = balanced directional book; the delta-weighted
 *     refinement of the plain put/call ratio). PAIR-level CALL-vs-PUT
 *     contrast computed in the DB pipeline.
 *   • 'greek_gamma'  — normalized GEX-style call-minus-put gamma balance
 *     (whole chain, neutral 0; call gamma positive / put gamma negative
 *     per the dealer-positioning sign convention).
 *   • 'greek_vega'   — OTM-wing vega balance (0<|Δ|<0.5 wings, neutral 0;
 *     the open-interest mirror of the 25-delta risk reversal).
 *
 * The greek_* metrics are dimensionless balances in [0,1] / [−1,1]; the
 * display rebase is S × (1 + (skew − neutral) × 0.10) — one full unit of
 * tilt maps to ±10% of price. theta/rho have no industry-standard
 * positioning skew and are not computed.
 *
 * (The IV-smile pricing skew — 25Δ/10Δ risk reversal vs spot over
 * time — lives in spot-skew/, not here; see
 * docs/options_vol_smile_study.md.)
 *
 * Adapters in skewSpec.ts / greekSpec.ts produce SharedSkewSpec from
 * each source; sharedSkewOption.ts renders it identically (spot +
 * per-expiry thin lines + expiry shade bands + mean skew curve + expiry
 * closing lines).
 */
import type { OtmOiShare } from "../vol-smile/types";

/** DB-driven greek skew modes (skew_type = 'greek_<name>'). */
export type GreekSkewMode =
  | "greek_delta"
  | "greek_gamma"
  | "greek_vega";

/** Neutral (no-tilt) anchor of each greek_* skew mode. */
export const GREEK_NEUTRAL: Record<GreekSkewMode, number> = {
  greek_delta: 0.5,
  greek_gamma: 0,
  greek_vega: 0,
};

/** Price-space rebase scale: 1 unit of (skew − neutral) = ±10% of spot. */
export const GREEK_SKEW_PRICE_K = 0.1;

export type SharedSkewMode = "oi_moneyness" | GreekSkewMode;

/** True when the mode is one of the DB-driven greek_* skew types. */
export function isGreekSkewMode(mode: SharedSkewMode): mode is GreekSkewMode {
  return mode.startsWith("greek_");
}

export interface SharedSkewPerExpiry {
  /** Expiry group label (YYYY-MM). */
  expiry: string;
  /** Actual expiry boundary date of the group (shade end). */
  expiryDate: string;
  /** Price-space skew curve value (mode-specific translation). */
  skewPrice: number | null;
  /** Raw skewness value of the data source. */
  rawSkew: number | null;
  /** Deviation from neutral in percent (mode-specific scaling). */
  skewPct: number | null;
  /**
   * Share of the group's OI that would expire worthless at the date's
   * close (oi_moneyness only — raw-quote derived; greek_* adapters have
   * no per-contract OI, so it stays undefined there).
   */
  otmShare?: OtmOiShare | null;
}

export interface SharedSkewPoint {
  date: string;
  /** Spot price (price-scaled). */
  spot: number;
  skewPrice: number | null;
  rawSkew: number | null;
  skewPct: number | null;
  perExpiry: SharedSkewPerExpiry[];
}

export interface SharedSkewSpec {
  mode: SharedSkewMode;
  points: SharedSkewPoint[];
  /** Chart title. */
  chartTitle: string;
  /** Legend/series name of the mean (aggregate) skew curve. */
  meanSeriesName: string;
}
