/**
 * In-browser FULL-SMILE skew computation — the smile_slope mode. Where
 * ivSmileCompute.ts reads exactly TWO anchor points off each day's smile
 * (the 25Δ OTM call and put), this adapter uses the WHOLE smile: an
 * OI-weighted least-squares fit of IV% on log-moneyness m = ln(K/S) per
 * (date, expiry month) — the standard full-curve skew/slope measure
 * (Xing–Zhang–Zhao 2010 smirk regression; the model-free analogue of the
 * two-point risk reversal).
 *
 * Why a slope instead of a 3rd-moment "skewness": a linear tilt of the
 * smile has ZERO 3rd central moment on a (roughly) symmetric strike grid
 * — the moment only picks up asymmetric WINGS (convexity), not direction
 * (the reason the former OI-wtd 3rd moment of IV was dropped from the
 * iv_smile mode). The regression slope is the direct estimate of the
 * smile's tilt and keeps the call-vs-put sign.
 *
 * Display metric: tilt = β × (ln 1.1 − ln 0.9) — the fitted IV at K =
 * 1.10·S minus the fitted IV at K = 0.90·S, in vol points, so the number
 * is directly comparable to the 25Δ risk reversal (same unit, same sign
 * convention: positive = call wing richer). Rebase to price space uses
 * the SAME 0.5%-per-vol-pt scale as the 25Δ RR chart, so the two panels
 * are visually comparable: skewPrice = S × (1 + tilt × 0.5%).
 *
 * Correlations: the DB pipelines (options_iv_skew_stats /
 * options_skewness_stats) have no smile_slope column, so the whole-period
 * (expanding) correlation of MA_w(tilt) vs MA_w(spot) is computed here
 * with the same semantics as analyze/options/compute/_shared._expanding_corr
 * (expanding Pearson, min_periods = w, per expiry group).
 */
import { PRICE_SCALE } from "@/theme/chart-palette";
import { expiryToYyyyMm } from "../vol-smile/expiryUtils";
import { modeMeta } from "./skewSpec";
import type { OptionsRow, SkewnessCorrRow } from "@shared/types";
import type {
  SharedSkewPerExpiry,
  SharedSkewPoint,
  SharedSkewSpec,
} from "./types";

/** Tilt level that sits exactly on the price curve (flat smile). */
const NEUTRAL_TILT = 0;
/** Price offset per vol point of smile tilt (1 vol pt → 0.5%). */
const PCT_PER_VOLPT = 0.5;
/** Wing anchors of the display metric: K = ±10% around spot. */
const WING_HI = 1.1;
const WING_LO = 0.9;
/** Minimum contracts for a trusted per-group fit (_SMILE_MIN_CONTRACTS). */
const MIN_CONTRACTS = 3;

const CORR_WINDOWS = [5, 20, 60] as const;

function rebase(spot: number, tilt: number): number {
  return spot * (1 + (tilt - NEUTRAL_TILT) * (PCT_PER_VOLPT / 100));
}

/**
 * OI-weighted least-squares slope of iv_pct on m = ln(K/S) across ALL
 * valid contracts of one expiry group's chain, expressed as the fitted
 * wing difference IV(K=1.1·S) − IV(K=0.9·S) in vol points.
 */
function smileWingTilt(rows: OptionsRow[], spot: number): number | null {
  if (rows.length < MIN_CONTRACTS) return null;
  const sRaw = spot * PRICE_SCALE; // ln(K/S) is scale-free; keep raw units
  let wSum = 0;
  let wm = 0;
  let wy = 0;
  const pts: Array<{ m: number; y: number; w: number }> = [];
  for (const r of rows) {
    if (r.strike_price <= 0 || r.implied_vol == null) continue;
    if (r.implied_vol <= 0 || r.implied_vol >= 5) continue;
    const w = Math.max(1, r.open_interest);
    const m = Math.log(r.strike_price / sRaw);
    const y = r.implied_vol * 100;
    pts.push({ m, y, w });
    wSum += w;
    wm += w * m;
    wy += w * y;
  }
  if (pts.length < MIN_CONTRACTS || wSum <= 0) return null;
  const mBar = wm / wSum;
  const yBar = wy / wSum;
  let num = 0;
  let den = 0;
  for (const p of pts) {
    num += p.w * (p.m - mBar) * (p.y - yBar);
    den += p.w * (p.m - mBar) ** 2;
  }
  if (den <= 1e-12) return null;
  const beta = num / den; // vol pts per unit log-moneyness
  return beta * (Math.log(WING_HI) - Math.log(WING_LO));
}

/** Expanding Pearson correlation of two arrays (null = not enough data). */
function expandingCorr(xs: (number | null)[], ys: (number | null)[]): number {
  let n = 0;
  let sx = 0;
  let sy = 0;
  let sxx = 0;
  let syy = 0;
  let sxy = 0;
  for (let i = 0; i < xs.length; i++) {
    const x = xs[i];
    const y = ys[i];
    if (x == null || y == null) continue;
    n += 1;
    sx += x;
    sy += y;
    sxx += x * x;
    syy += y * y;
    sxy += x * y;
  }
  if (n < 2) return NaN;
  const cov = sxy / n - (sx / n) * (sy / n);
  const vx = sxx / n - (sx / n) ** 2;
  const vy = syy / n - (sy / n) ** 2;
  if (vx <= 1e-12 || vy <= 1e-12) return NaN;
  return cov / Math.sqrt(vx * vy);
}

export interface SmileSlopeComputed {
  /** Spec for buildSharedSkewOption / buildSkewConvergenceOption. */
  spec: SharedSkewSpec;
  /** Whole-period correlation rows (MA_w tilt vs MA_w spot per expiry). */
  corrRows: SkewnessCorrRow[];
}

/**
 * Compute the full-smile skew series + correlation rows from raw quote
 * rows. Grouping/validity mirrors ivSmileSpecFromRows so both panels see
 * the same per-expiry-month population on every date.
 */
export function computeSmileSlope(rows: OptionsRow[]): SmileSlopeComputed {
  const byDate = new Map<string, OptionsRow[]>();
  for (const r of rows) {
    if (!byDate.has(r.date)) byDate.set(r.date, []);
    byDate.get(r.date)!.push(r);
  }

  // Per expiry month: chronological tilt + spot series (for corr).
  const tiltSeries = new Map<string, (number | null)[]>();
  const spotSeries = new Map<string, number[]>();
  const dateSeries = new Map<string, string[]>();
  const expiryDateByMonth = new Map<string, string>();

  const points: SharedSkewPoint[] = [];
  for (const date of Array.from(byDate.keys()).sort()) {
    const snap = byDate.get(date)!;
    const first = snap[0];
    if (first.underlying_close == null || first.underlying_close <= 0) continue;
    const spot = first.underlying_close / PRICE_SCALE;

    // Valid active contracts (mirrors ivSmileSpecFromRows, minus the
    // delta requirement — the smile fit is moneyness-based).
    const valid = snap.filter(
      (r) =>
        r.expiry_date >= date &&
        r.strike_price > 0 &&
        r.implied_vol != null &&
        r.implied_vol > 0 &&
        r.implied_vol < 5,
    );

    // Expiry month → contract rows; month → latest expiry date.
    const groups = new Map<string, OptionsRow[]>();
    for (const r of valid) {
      const em = expiryToYyyyMm(r.expiry_date);
      if (!groups.has(em)) groups.set(em, []);
      groups.get(em)!.push(r);
      const prev = expiryDateByMonth.get(em);
      if (!prev || r.expiry_date > prev) expiryDateByMonth.set(em, r.expiry_date);
    }

    const perExpiry: SharedSkewPerExpiry[] = [];
    const tiltVals: number[] = [];
    for (const em of Array.from(groups.keys()).sort()) {
      const tilt = smileWingTilt(groups.get(em)!, spot);
      if (tilt != null && Number.isFinite(tilt)) {
        tiltVals.push(tilt);
        if (!tiltSeries.has(em)) {
          tiltSeries.set(em, []);
          spotSeries.set(em, []);
          dateSeries.set(em, []);
        }
        tiltSeries.get(em)!.push(tilt);
        spotSeries.get(em)!.push(spot);
        dateSeries.get(em)!.push(date);
      } else if (tiltSeries.has(em)) {
        // Keep series gapless (positional rolling windows in the corr).
        tiltSeries.get(em)!.push(null);
        spotSeries.get(em)!.push(spot);
        dateSeries.get(em)!.push(date);
      }
      perExpiry.push({
        expiry: em,
        expiryDate: expiryDateByMonth.get(em) ?? "",
        skewPrice: tilt != null ? rebase(spot, tilt) : null,
        rawSkew: tilt,
        skewPct: tilt != null ? (tilt - NEUTRAL_TILT) * PCT_PER_VOLPT : null,
      });
    }

    const meanTilt =
      tiltVals.length > 0
        ? tiltVals.reduce((a, b) => a + b, 0) / tiltVals.length
        : null;
    points.push({
      date,
      spot,
      skewPrice: meanTilt != null ? rebase(spot, meanTilt) : null,
      rawSkew: meanTilt,
      skewPct: meanTilt != null ? (meanTilt - NEUTRAL_TILT) * PCT_PER_VOLPT : null,
      perExpiry,
    });
  }

  // Whole-period (expanding) correlation per expiry group: MA_w of the
  // raw tilt (vol pts) vs MA_w of spot — the same basis as the DB's
  // corr_rr25_maW_vs_spot_maW columns (raw metric vs spot MA).
  const corrRows: SkewnessCorrRow[] = [];
  for (const em of Array.from(tiltSeries.keys()).sort()) {
    const tilts = tiltSeries.get(em)!;
    const spots = spotSeries.get(em)!;
    const dates = dateSeries.get(em)!;
    const corr: Record<string, (number | null)[]> = { ma5: [], ma20: [], ma60: [] };
    for (const w of CORR_WINDOWS) {
      const maTilt: (number | null)[] = [];
      const maSpot: (number | null)[] = [];
      for (let i = 0; i < tilts.length; i++) {
        if (i < w - 1) {
          maTilt.push(null);
          maSpot.push(null);
          continue;
        }
        let ok = true;
        let st = 0;
        let ss = 0;
        for (let j = i - w + 1; j <= i; j++) {
          if (tilts[j] == null) {
            ok = false;
            break;
          }
          st += tilts[j] as number;
          ss += spots[j];
        }
        maTilt.push(ok ? st / w : null);
        maSpot.push(ok ? ss / w : null);
      }
      for (let i = 0; i < tilts.length; i++) {
        const c = expandingCorr(maTilt.slice(0, i + 1), maSpot.slice(0, i + 1));
        corr[`ma${w}`].push(Number.isFinite(c) ? c : null);
      }
    }
    for (let i = 0; i < dates.length; i++) {
      corrRows.push({
        date: dates[i],
        expiry_month: em,
        corr_skewness_ma5_vs_spot_ma5: corr.ma5[i] ?? null,
        corr_skewness_ma20_vs_spot_ma20: corr.ma20[i] ?? null,
        corr_skewness_ma60_vs_spot_ma60: corr.ma60[i] ?? null,
      });
    }
  }

  return { spec: { mode: "smile_slope", points, ...modeMeta("smile_slope") }, corrRows };
}
