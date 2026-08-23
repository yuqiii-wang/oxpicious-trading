/**
 * In-browser IV smile skewness computation — mirrors the Python
 * _smile_skewness_by_group (analyze/options/compute.py): OI-weighted 3rd
 * standardized moment of implied vol across strikes, per
 * (date, option_type, expiry group), with CALL/PUT averaged per group
 * (same aggregation as the iv-skew API's AVG over option types).
 *
 * Unlike the DB pipeline (which collapses open expiry groups beyond the
 * dataset max date into one synthetic mean-expiry group), expiry groups
 * here keep their REAL expiry dates — so the shared chart renders
 * per-expiry lines + shade bands on recent dates too, matching the
 * in-browser OI moneyness skew reference chart architecture.
 *
 * Display rebase: skewPrice = S × (1 + (skew − 1)/100) — a smile
 * skewness of 1 sits exactly on the spot curve; each unit = ±1% of price.
 */
import { PRICE_SCALE } from "@/theme/chart-palette";
import { expiryToYyyyMm } from "../vol-smile/expiryUtils";
import { modeMeta } from "./skewSpec";
import type { OptionsRow, SkewnessCrossCountRow } from "@shared/types";
import type {
  SharedSkewPerExpiry,
  SharedSkewPoint,
  SharedSkewSpec,
} from "./types";

/** Smile skewness level that sits exactly on the price curve. */
const NEUTRAL_SKEW = 1;
/** Price offset per skewness unit above/below neutral (1 → 1%). */
const PCT_PER_UNIT = 1;
/** Minimum contracts for the 3rd-moment smile skewness. */
const MIN_CONTRACTS = 3;

function rebase(spot: number, skew: number): number {
  return spot * (1 + (skew - NEUTRAL_SKEW) * (PCT_PER_UNIT / 100));
}

/** OI-weighted 3rd standardized moment of IV (vol points) across strikes. */
function thirdMomentSkew(rows: OptionsRow[]): number | null {
  let n = 0;
  let w = 0;
  let wx = 0;
  let wxx = 0;
  let wxxx = 0;
  for (const r of rows) {
    const wi = Math.max(1, r.open_interest);
    const x = (r.implied_vol as number) * 100;
    w += wi;
    wx += wi * x;
    wxx += wi * x * x;
    wxxx += wi * x * x * x;
    n += 1;
  }
  if (n < MIN_CONTRACTS || w <= 0) return null;
  const mean = wx / w;
  const m2 = wxx / w - mean * mean;
  const m3 = wxxx / w - 3 * mean * (wxx / w) + 2 * mean ** 3;
  const std = Math.sqrt(Math.max(m2, 0));
  if (std <= 1e-8) return null;
  return m3 / (std * std * std);
}

export function ivSmileSpecFromRows(
  rows: OptionsRow[],
  crossCounts?: SkewnessCrossCountRow[],
): SharedSkewSpec {
  // Cross counts keyed (date, YYYY-MM) — from options_skewness_stats
  // skew_type='iv_smile'.
  const crossCountMap = new Map<string, number>();
  if (crossCounts) {
    for (const c of crossCounts) {
      crossCountMap.set(
        `${c.date}|${c.expiry_month.slice(0, 7)}`,
        c.count_skewness_curve_crossed_spot,
      );
    }
  }

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

    // Expiry month → option type → contract rows; month → latest expiry date.
    const groups = new Map<string, Map<string, OptionsRow[]>>();
    const expiryDateByMonth = new Map<string, string>();
    for (const r of valid) {
      const em = expiryToYyyyMm(r.expiry_date);
      if (!groups.has(em)) groups.set(em, new Map());
      const byType = groups.get(em)!;
      if (!byType.has(r.option_type)) byType.set(r.option_type, []);
      byType.get(r.option_type)!.push(r);
      const prev = expiryDateByMonth.get(em);
      if (!prev || r.expiry_date > prev) expiryDateByMonth.set(em, r.expiry_date);
    }

    const perExpiry: SharedSkewPerExpiry[] = [];
    const skewVals: number[] = [];
    for (const em of Array.from(groups.keys()).sort()) {
      const byType = groups.get(em)!;
      const typeSkews: number[] = [];
      for (const t of Array.from(byType.keys()).sort()) {
        const s = thirdMomentSkew(byType.get(t)!);
        if (s != null && Number.isFinite(s)) typeSkews.push(s);
      }
      const skew =
        typeSkews.length > 0
          ? typeSkews.reduce((a, b) => a + b, 0) / typeSkews.length
          : null;
      if (skew != null) skewVals.push(skew);
      const crossCount = crossCountMap.get(`${date}|${em}`);
      perExpiry.push({
        expiry: em,
        expiryDate: expiryDateByMonth.get(em) ?? "",
        skewPrice: skew != null ? rebase(spot, skew) : null,
        rawSkew: skew,
        skewPct: skew != null ? (skew - NEUTRAL_SKEW) * PCT_PER_UNIT : null,
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
      skewPrice: meanSkew != null ? rebase(spot, meanSkew) : null,
      rawSkew: meanSkew,
      skewPct: meanSkew != null ? (meanSkew - NEUTRAL_SKEW) * PCT_PER_UNIT : null,
      perExpiry,
    });
  }

  return { mode: "iv_smile", points, ...modeMeta("iv_smile") };
}
