/**
 * In-browser IV smile skew computation — the 25Δ risk reversal
 * (iv_call25 − iv_put25, vol points) per (date, expiry month), computed
 * from the same quote rows as the Volatility Smile snapshot so the
 * skew-over-time panel aligns with the smile's visible tilt: OTM call
 * wing richer (rr25 > 0) → skew price ABOVE spot; OTM put wing richer
 * (rr25 < 0) → BELOW spot. Same metric as the DB pipeline's
 * risk_reversal_25d (analyze/options/compute/iv_skew.py: OTM contracts
 * nearest |delta| = 0.25 within 0 < |delta| < 0.5) and as the
 * "IV Skew · 25Δ Risk Reversal (C−P)" chart on this tab.
 *
 * (Formerly the OI-weighted 3rd standardized moment of IV — dropped
 * because the 3rd moment of IV levels is mathematically invariant to the
 * smile's call-vs-put direction on a symmetric strike grid: it measures
 * wing convexity, not tilt, so the rebased curve sat pinned on one side
 * of spot regardless of what the smile did.)
 *
 * Unlike the DB pipeline (which collapses open expiry groups beyond the
 * dataset max date into one synthetic mean-expiry group), expiry groups
 * here keep their REAL expiry dates — so the shared chart renders
 * per-expiry lines + shade bands on recent dates too, matching the
 * in-browser OI moneyness skew reference chart architecture.
 *
 * Display rebase: skewPrice = S × (1 + rr25 × 0.5%) — a balanced smile
 * (rr25 = 0) sits exactly on the spot curve; each vol point of risk
 * reversal = ±0.5% of price.
 */
import { PRICE_SCALE } from "@/theme/chart-palette";
import { expiryToYyyyMm } from "../vol-smile/expiryUtils";
import { modeMeta } from "./skewSpec";
import type { OptionsRow } from "@shared/types";
import type {
  SharedSkewPerExpiry,
  SharedSkewPoint,
  SharedSkewSpec,
} from "./types";

/** rr25 level that sits exactly on the price curve (balanced wings). */
const NEUTRAL_RR = 0;
/** Price offset per vol point of risk reversal (1 vol pt → 0.5%). */
const PCT_PER_VOLPT = 0.5;
/** |delta| target for the OTM wings (mirrors _DELTA_TARGET). */
const DELTA_TARGET = 0.25;
/** OTM delta band 0 < |delta| < 0.5 (mirrors _DELTA_OTM_MAX). */
const DELTA_OTM_MAX = 0.5;

function rebase(spot: number, rr25: number): number {
  return spot * (1 + (rr25 - NEUTRAL_RR) * (PCT_PER_VOLPT / 100));
}

/** IV (%) of the contract nearest |delta| = 0.25 inside the OTM band. */
function nearestOtmIv(rows: OptionsRow[], sign: 1 | -1): number | null {
  let best: OptionsRow | null = null;
  let bestDist = Infinity;
  for (const r of rows) {
    if (r.delta == null) continue;
    const d = r.delta * sign; // OTM: delta ∈ (0, 0.5) calls, (−0.5, 0) puts
    if (d <= 0 || d >= DELTA_OTM_MAX) continue;
    const dist = Math.abs(d - DELTA_TARGET);
    if (dist < bestDist) {
      bestDist = dist;
      best = r;
    }
  }
  if (best == null || best.implied_vol == null) return null;
  return (best.implied_vol as number) * 100;
}

/** 25Δ risk reversal of one expiry group's chain (vol points). */
function riskReversal25d(rows: OptionsRow[]): number | null {
  const callIv = nearestOtmIv(
    rows.filter((r) => r.option_type === "CALL"),
    1,
  );
  const putIv = nearestOtmIv(
    rows.filter((r) => r.option_type === "PUT"),
    -1,
  );
  if (callIv == null || putIv == null) return null;
  return callIv - putIv;
}

export function ivSmileSpecFromRows(
  rows: OptionsRow[],
): SharedSkewSpec {
  const byDate = new Map<string, OptionsRow[]>();
  for (const r of rows) {
    if (!byDate.has(r.date)) byDate.set(r.date, []);
    byDate.get(r.date)!.push(r);
  }

  const points: SharedSkewPoint[] = [];
  for (const date of Array.from(byDate.keys()).sort()) {
    const snap = byDate.get(date)!;
    const first = snap[0];
    if (first.underlying_close == null || first.underlying_close <= 0) continue;
    const spot = first.underlying_close / PRICE_SCALE;

    // Valid active contracts (mirrors _IV_SKEW_VALID_WHERE + active filter).
    const valid = snap.filter(
      (r) =>
        r.expiry_date >= date &&
        r.strike_price > 0 &&
        r.implied_vol != null &&
        r.implied_vol > 0 &&
        r.implied_vol < 5 &&
        r.delta != null,
    );

    // Expiry month → contract rows; month → latest expiry date.
    const groups = new Map<string, OptionsRow[]>();
    const expiryDateByMonth = new Map<string, string>();
    for (const r of valid) {
      const em = expiryToYyyyMm(r.expiry_date);
      if (!groups.has(em)) groups.set(em, []);
      groups.get(em)!.push(r);
      const prev = expiryDateByMonth.get(em);
      if (!prev || r.expiry_date > prev) expiryDateByMonth.set(em, r.expiry_date);
    }

    const perExpiry: SharedSkewPerExpiry[] = [];
    const rrVals: number[] = [];
    for (const em of Array.from(groups.keys()).sort()) {
      const rr = riskReversal25d(groups.get(em)!);
      if (rr != null && Number.isFinite(rr)) rrVals.push(rr);
      perExpiry.push({
        expiry: em,
        expiryDate: expiryDateByMonth.get(em) ?? "",
        skewPrice: rr != null ? rebase(spot, rr) : null,
        rawSkew: rr,
        skewPct: rr != null ? (rr - NEUTRAL_RR) * PCT_PER_VOLPT : null,
      });
    }

    const meanRr =
      rrVals.length > 0
        ? rrVals.reduce((a, b) => a + b, 0) / rrVals.length
        : null;
    points.push({
      date,
      spot,
      skewPrice: meanRr != null ? rebase(spot, meanRr) : null,
      rawSkew: meanRr,
      skewPct: meanRr != null ? (meanRr - NEUTRAL_RR) * PCT_PER_VOLPT : null,
      perExpiry,
    });
  }

  return { mode: "iv_smile", points, ...modeMeta("iv_smile") };
}
