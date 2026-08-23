/**
 * Greek skew spec adapter — converts the DB-persisted daily skewness
 * series (analysis.options_skewness_stats, skew_type='greek_<name>',
 * fetched via /skewness-series) into the unified SharedSkewSpec
 * consumed by sharedSkewOption.ts.
 *
 * The greek_* metrics are PAIR-level CALL-vs-PUT contrasts computed in
 * the DB pipeline (analyze/options/compute/, NOT in-browser):
 *   greek_delta: dpcr = Σ_put OI·|Δ| / Σ_all OI·|Δ|  (whole chain,
 *                neutral 0.5 — delta-weighted put/call ratio)
 *   greek_gamma: (Σ_call OI·Γ − Σ_put OI·Γ)/(Σ_call OI·Γ + Σ_put OI·Γ)
 *                (whole chain, neutral 0 — GEX-style balance)
 *   greek_vega:  same balance on the 0<|Δ|<0.5 OTM wings (neutral 0 —
 *                the open-interest mirror of the 25d risk reversal)
 *
 * Display mapping (neutral-anchored rebase):
 *   skewPrice = S × (1 + (skewness − neutral) × 0.10)
 *   skewPct   = (skewness − neutral) × 100
 * so one full unit of tilt maps to ±10% of price and the neutral value
 * sits exactly on the spot curve.
 *
 * The browser only joins the stored skewness values with spot prices
 * (from the quote rows) and the stored cross counts.
 */
import { PRICE_SCALE } from "@/theme/chart-palette";
import { modeMeta } from "./skewSpec";
import type {
  OptionsRow,
  SkewnessCrossCountRow,
  SkewnessSeriesRow,
} from "@shared/types";
import {
  GREEK_NEUTRAL,
  GREEK_SKEW_PRICE_K,
  type GreekSkewMode,
  type SharedSkewPerExpiry,
  type SharedSkewPoint,
  type SharedSkewSpec,
} from "./types";

/** Spot (yuan) per date from quote rows (underlying_close is in 厘). */
export function spotByDateFromRows(rows: OptionsRow[]): Map<string, number> {
  const m = new Map<string, number>();
  for (const r of rows) {
    if (!m.has(r.date) && r.underlying_close > 0) {
      m.set(r.date, r.underlying_close / PRICE_SCALE);
    }
  }
  return m;
}

export function greekSpecFromSeries(
  mode: GreekSkewMode,
  seriesRows: SkewnessSeriesRow[],
  spotByDate: Map<string, number>,
  crossCounts?: SkewnessCrossCountRow[],
): SharedSkewSpec {
  // Cross counts keyed (date, YYYY-MM) — from options_skewness_stats
  // rows of THIS mode's skew_type.
  const crossCountMap = new Map<string, number>();
  if (crossCounts) {
    for (const c of crossCounts) {
      crossCountMap.set(
        `${c.date}|${c.expiry_month.slice(0, 7)}`,
        c.count_skewness_curve_crossed_spot,
      );
    }
  }

  // date → stored skewness rows (expiry-month granularity).
  const byDate = new Map<string, SkewnessSeriesRow[]>();
  for (const r of seriesRows) {
    if (r.skewness == null) continue;
    if (!byDate.has(r.date)) byDate.set(r.date, []);
    byDate.get(r.date)!.push(r);
  }

  const points: SharedSkewPoint[] = [];
  for (const date of Array.from(byDate.keys()).sort()) {
    const spot = spotByDate.get(date);
    if (spot == null) continue;

    const neutral = GREEK_NEUTRAL[mode];
    const perExpiry: SharedSkewPerExpiry[] = [];
    const skewVals: number[] = [];
    for (const r of byDate.get(date)!) {
      const skew = r.skewness as number;
      skewVals.push(skew);
      const crossCount = crossCountMap.get(`${date}|${r.expiry_month.slice(0, 7)}`);
      perExpiry.push({
        expiry: r.expiry_month.slice(0, 7),
        expiryDate: r.expiry_date ?? "",
        skewPrice: spot * (1 + (skew - neutral) * GREEK_SKEW_PRICE_K),
        rawSkew: skew,
        skewPct: (skew - neutral) * 100,
        ...(crossCount != null ? { countSkewnessCurveCrossedSpot: crossCount } : {}),
      });
    }

    const meanSkew =
      skewVals.length > 0
        ? skewVals.reduce((a, b) => a + b, 0) / skewVals.length
        : null;
    points.push({
      date,
      spot,
      skewPrice:
        meanSkew != null
          ? spot * (1 + (meanSkew - neutral) * GREEK_SKEW_PRICE_K)
          : null,
      rawSkew: meanSkew,
      skewPct: meanSkew != null ? (meanSkew - neutral) * 100 : null,
      perExpiry,
    });
  }

  return { mode, points, ...modeMeta(mode) };
}
