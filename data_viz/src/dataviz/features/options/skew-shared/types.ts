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
  /**
   * The group's total open interest on the date in contracts (calls +
   * puts; oi_moneyness only). Carries the ABSOLUTE position size: the
   * per-expiry curves' line thickness scales with it (peak OI over the
   * curve) while the curve VALUE stays the ratio-based skew. Greek_*
   * adapters leave it undefined.
   */
  oiTotal?: number | null;
  /**
   * Change of the group's total OI vs 5/20 trading sessions earlier, in
   * contracts (positive = positions added, negative = closed/rolled
   * away; null when the group has no OI record at the offset session).
   * oi_moneyness only — greek_* adapters leave it undefined.
   */
  oiDelta5d?: number | null;
  oiDelta20d?: number | null;
  /**
   * Max of the group's total OI over the trailing 20 sessions incl. the
   * date, in contracts (null when no OI record in the window). Current
   * OI == this value ⇒ the group sits at a 20-session OI high.
   * oi_moneyness only.
   */
  oiMax20d?: number | null;
}

export interface SharedSkewPoint {
  date: string;
  /** Spot price (price-scaled). null when the date has no underlying
   * close (loader 0 = missing) — renders as a chart gap, not a 0 point. */
  spot: number | null;
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
  /** Display unit of spot/skewPrice: "yuan" (SSE/SZSE ETF venues,
   *  closes stored in 厘) or "index points" (CFFEX, native points). */
  priceUnit?: string;
}

/** Options-implied corridor event marker (analysis.options_bands v2): a
 *  fresh cross of the OI-mass corridor vs the PRIOR session's corridor
 *  (the corridor re-centres daily). Rendered as a scatter marker on the
 *  spot curve of the skew chart. */
export interface SharedBandEvent {
  date: string;
  /** "lower" = EXIT_LOW (green up-triangle below the line — snap-back-up
   *  side), "upper" = EXIT_HIGH (red down-triangle above), "none" =
   *  REENTER (neutral diamond on the line). */
  side: "lower" | "upper" | "none";
  kind: "EXIT_LOW" | "EXIT_HIGH" | "REENTER";
  /** OI share at strikes <= spot ([0,1]) and the validated lean at the
   *  event date — tooltip context. */
  spotPct: number | null;
  lean: string | null;
  /**
   * Edge-significance amplifier (analysis.options_bands.edge_sig): true
   * when this event's side was breached/approached while the exchange
   * had JUST extended the strike grid on the same side (>= 2 new
   * strikes in the trailing 40 sessions) — the cross is into
   * freshly-listed price territory and renders as a bigger glowing
   * marker. REENTER is never amplified.
   */
  sig?: boolean;
  /** Count of new same-side strikes in the trailing 40 sessions at the
   *  event date (tooltip context for `sig`; null when unknown). */
  sigNewStrikes?: number | null;
}

/** OI-mass corridor bounds per date (analysis.options_bands v2),
 *  PRICE-SCALED to the chart's unit — the shaded bounded region between
 *  the OI-CDF P10 and P90 strikes. */
export interface SharedCorridorPoint {
  date: string;
  low: number | null;
  high: number | null;
}

/** One SIGNAL-history event to mark on the spot curve (the options
 *  page's IN-SIGNAL marker mode — analysis_signals.history_signals,
 *  the options_wall family): actionable events draw THE shared
 *  buy/sell triangle; a rejected event (is_rejection — the strategy
 *  gate's explicit no-action record) draws the grey diamond instead,
 *  never a triangle. */
export interface SharedSkewSignalEvent {
  date: string;
  /** "buy" / "sell" — the event's recorded action. */
  action: string;
  /** TRUE = the VRP-rejected no-action record (the grey diamond). */
  is_rejection?: boolean;
}

/** One chain-wide zone wall per (date, side) — the DOMINANT
 *  wall_type='zone' group of analysis.options_walls across all expiry
 *  cohorts that date (same strength ranking the Options Trend OI Bands
 *  chart applies per cohort), strike levels PRICE-SCALED to the chart's
 *  unit. Rendered as the light-shaded low→high wall band with the
 *  OI-weighted center line. */
export interface SharedWallZone {
  date: string;
  side: "CALL" | "PUT";
  low: number;
  high: number;
  /** OI-weighted center strike of the zone. */
  center: number;
  /** Total OI of the dominant zone (contracts). */
  wallOi: number;
  /** zone OI / total chain OI (call+put), 0..1. */
  massShare: number | null;
  /** Signed away-from-spot distance of the center, % of spot. */
  gapPct: number | null;
  state: "ACTIVE" | "ERODED" | "BREACHED" | null;
  strength: number | null;
}

/** Dominant chain-wide zone walls per date, per side — the wall/zone
 *  light-shade layer of the options-bands toggle (analysis.options_walls,
 *  the same source the Options Trend OI Bands chart overlays). */
export interface SharedWallZones {
  call: Map<string, SharedWallZone>;
  put: Map<string, SharedWallZone>;
}

/** Forecast trigger-day highlight overlay (the Options Bands signal
 *  page's forecast row clicks): the same visual language the Recent
 *  Movements trend chart uses — a purple pinpoint on the spot curve at
 *  each trigger day, a dark purple band over each merged signal's
 *  qualifying streak period, and a light band over each trigger day's
 *  forward forecast window. */
export interface SharedSkewHighlights {
  /** Trigger day dates (pinpoint circles at the spot values). */
  dates: readonly string[];
  /** Each merged signal's qualifying-run [start, end] calendar dates +
   *  trading-day count (dark streak-period bands). */
  spans: ReadonlyArray<{ start: string; end: string; days: number | null }>;
  /** Forward-window width in trading days from each trigger day (the
   *  clicked horizon's period — light bands). */
  horizonDays: number;
}
