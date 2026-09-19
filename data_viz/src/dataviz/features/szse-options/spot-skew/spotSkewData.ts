/**
 * spot-skew data aggregation — computes the smile skew chronology
 * in-browser from raw per-contract quote rows (stats.v_options_quote via
 * /api/szse-options/combined), grouped by REAL expiry dates.
 *
 * Why in-browser: the persisted analysis.options_iv_skew_stats collapses
 * still-open expiry groups to a synthetic mean expiry date, so recent
 * dates carry a single merged group — fine for whole-history lines, but
 * it destroys the expiry term structure this panel's Today view needs.
 * The raw rows carry the real expiry per contract.
 *
 * All skew values are risk reversals in vol points (percent):
 *   RR<delta> = IV(<delta>Δ OTM call) − IV(<delta>Δ OTM put)
 * measured in delta space — a spot-centered (sticky-delta) coordinate,
 * so the series is comparable across dates regardless of where spot
 * moved the strike grid (mirrors analyze/options/compute/iv_skew.py's
 * nearest-|delta| selection — no interpolation). See
 * docs/options_vol_smile_study.md.
 */
import type { OptionsRow } from "@shared/types";

/** Selectable OTM wing for the risk reversal. */
export type SkewDelta = 25 | 10;

/** Front-expiry minimum days to expiry (skip expiry-week IV noise). */
export const FRONT_MIN_DTE = 7;

/** Rolling window (sessions) for the RR extreme-skew ±2σ envelope. */
export const RR_SIGMA_WINDOW = 20;

/** Nearest-anchor candidate accumulator (mirrors _nearest_row_metric). */
interface Anchor {
  dist: number;
  iv: number;
}

/** Per (date × real expiry) group accumulators for both wings + ATM. */
export interface ExpiryAgg {
  expiryDate: string;
  minDte: number;
  spot: number | null; // yuan
  atm: Anchor | null;
  call25: Anchor | null;
  put25: Anchor | null;
  call10: Anchor | null;
  put10: Anchor | null;
}

/** date → expiry groups (sorted by expiry date). */
export type SkewGroupsByDate = Map<string, ExpiryAgg[]>;

export interface SkewDayPoint {
  date: string;
  /** Front-expiry risk reversal (vol pts, null when a wing is missing). */
  rrFront: number | null;
  /** Mean risk reversal across the day's expiry groups (vol pts). */
  rrMean: number | null;
  /** Front-expiry ATM IV (vol pts) — the smile LEVEL (VIX analog). */
  atmIvFront: number | null;
  /** Underlying close (yuan, null when absent). */
  spot: number | null;
  /** ±2σ envelope around the front RR MA20 (null while warming up). */
  rrMa20: number | null;
  rrBandLow: number | null;
  rrBandHigh: number | null;
}

export interface SkewExpirySlice {
  /** Display label (MM-DD of the real expiry date). */
  label: string;
  expiryDate: string;
  dte: number;
  rr: number | null;
  atmIv: number | null;
}

const OTM_DELTA_MAX = 0.5;

function keepAnchor(cur: Anchor | null, dist: number, iv: number): Anchor {
  return cur == null || dist < cur.dist ? { dist, iv } : cur;
}

/**
 * Single pass over the quote rows → per (date × real expiry) groups with
 * the nearest-|delta| 25Δ/10Δ OTM wing candidates and the ATM anchor.
 * Row validity mirrors the pipeline's _IV_SKEW_VALID_WHERE.
 *
 * priceScale: SZSE ETF quotes are stored in 厘 (÷1000 → yuan); CFFEX
 * index quotes are native index points (÷1).
 */
export function buildExpiryGroupAgg(
  rows: OptionsRow[],
  priceScale: number = 1000.0,
): SkewGroupsByDate {
  const byDate = new Map<string, Map<string, ExpiryAgg>>();

  for (const r of rows) {
    const iv = r.implied_vol;
    const d = r.delta;
    if (
      iv == null || d == null || iv <= 0 || iv >= 5 ||
      r.strike_price <= 0 || r.underlying_close <= 0
    ) {
      continue;
    }
    const ivPct = iv * 100.0;

    let groups = byDate.get(r.date);
    if (!groups) {
      groups = new Map();
      byDate.set(r.date, groups);
    }
    let g = groups.get(r.expiry_date);
    if (!g) {
      g = {
        expiryDate: r.expiry_date,
        minDte: r.days_to_expiry,
        spot: r.underlying_close / priceScale,
        atm: null,
        call25: null,
        put25: null,
        call10: null,
        put10: null,
      };
      groups.set(r.expiry_date, g);
    }
    if (r.days_to_expiry < g.minDte) g.minDte = r.days_to_expiry;

    g.atm = keepAnchor(g.atm, Math.abs(r.moneyness_ratio - 1.0), ivPct);

    if (r.option_type === "CALL" && d > 0 && d < OTM_DELTA_MAX) {
      g.call25 = keepAnchor(g.call25, Math.abs(d - 0.25), ivPct);
      g.call10 = keepAnchor(g.call10, Math.abs(d - 0.1), ivPct);
    } else if (r.option_type === "PUT" && d < 0 && d > -OTM_DELTA_MAX) {
      g.put25 = keepAnchor(g.put25, Math.abs(d + 0.25), ivPct);
      g.put10 = keepAnchor(g.put10, Math.abs(d + 0.1), ivPct);
    }
  }

  const out: SkewGroupsByDate = new Map();
  for (const [date, groups] of byDate) {
    out.set(
      date,
      Array.from(groups.values()).sort((a, b) =>
        a.expiryDate.localeCompare(b.expiryDate),
      ),
    );
  }
  return out;
}

function groupRr(g: ExpiryAgg, delta: SkewDelta): number | null {
  const call = delta === 25 ? g.call25 : g.call10;
  const put = delta === 25 ? g.put25 : g.put10;
  if (call == null || put == null) return null;
  return call.iv - put.iv;
}

/** Front expiry = nearest expiry group with ≥ FRONT_MIN_DTE days left
 *  (falls back to the nearest group inside the expiry week). */
function pickFront(groups: ExpiryAgg[]): ExpiryAgg | null {
  if (groups.length === 0) return null;
  const eligible = groups.filter((g) => g.minDte >= FRONT_MIN_DTE);
  return (eligible.length > 0 ? eligible : groups)[0];
}

/**
 * Chronological per-date series: front-expiry RR, all-expiry mean RR,
 * front ATM IV, spot and the ±2σ(20d) envelope around the front RR MA20.
 */
export function buildSkewDayPoints(
  groupsByDate: SkewGroupsByDate,
  delta: SkewDelta,
): SkewDayPoint[] {
  const dates = Array.from(groupsByDate.keys()).sort();
  const points: SkewDayPoint[] = dates.map((date) => {
    const groups = groupsByDate.get(date)!;
    const front = pickFront(groups);

    const rrValues = groups
      .map((g) => groupRr(g, delta))
      .filter((v): v is number => v != null);

    return {
      date,
      rrFront: front != null ? groupRr(front, delta) : null,
      rrMean:
        rrValues.length > 0
          ? rrValues.reduce((a, b) => a + b, 0) / rrValues.length
          : null,
      atmIvFront: front?.atm?.iv ?? null,
      spot: front?.spot ?? groups[0].spot,
      rrMa20: null,
      rrBandLow: null,
      rrBandHigh: null,
    };
  });

  // Rolling MA20 / σ20 over the front RR (null-skipping) → the ±2σ
  // extreme-skew envelope.
  const frontRaw = points.map((p) => p.rrFront);
  for (let i = 0; i < points.length; i++) {
    const win: number[] = [];
    for (let j = Math.max(0, i - RR_SIGMA_WINDOW + 1); j <= i; j++) {
      const v = frontRaw[j];
      if (v != null) win.push(v);
    }
    if (win.length >= Math.min(10, RR_SIGMA_WINDOW)) {
      const ma = win.reduce((a, b) => a + b, 0) / win.length;
      const variance =
        win.reduce((a, b) => a + (b - ma) ** 2, 0) / (win.length - 1);
      const sigma = Math.sqrt(variance);
      points[i].rrMa20 = ma;
      points[i].rrBandLow = ma - 2 * sigma;
      points[i].rrBandHigh = ma + 2 * sigma;
    }
  }

  return points;
}

/** Today-style slice: one date's skew term structure by real expiry. */
export function buildSkewTermStructure(
  groupsByDate: SkewGroupsByDate,
  date: string,
  delta: SkewDelta,
): SkewExpirySlice[] {
  const groups = groupsByDate.get(date);
  if (!groups) return [];
  return groups.map((g) => ({
    label: g.expiryDate.slice(5), // MM-DD
    expiryDate: g.expiryDate,
    dte: g.minDte,
    rr: groupRr(g, delta),
    atmIv: g.atm?.iv ?? null,
  }));
}

/** Sorted master date list (for click-to-select-date index mapping). */
export function skewAxisDates(points: SkewDayPoint[]): string[] {
  return points.map((p) => p.date);
}
