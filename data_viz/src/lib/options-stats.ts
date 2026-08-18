/**
 * Options market sentiment computation — port of compute_*() helpers in
 * plot_szse_options.py.
 *
 * All functions take a snapshot of options rows for a single underlying on a
 * single date.
 */
import type { OptionsRow } from "@shared/types";
import { CONTRACT_SIZE, PRICE_SCALE } from "../theme/chart-palette";
import { computeGreeks, impliedVolDefault } from "./iv";

export interface SmileSkewness {
  expiry: string;
  callSkew: number | null;
  putSkew: number | null;
  overallSkew: number | null;
}

/**
 * OI-weighted skewness (3rd standardized moment) of IV values across strikes.
 *
 * Skewness measures the asymmetry of the volatility smile:
 *   • Negative → higher IV on downside (puts richer than calls, typical equity pattern)
 *   • Positive → higher IV on upside (calls richer than puts)
 *
 * Computed per expiry month, separately for CALL and PUT.
 */
export function computeSmileSkewness(snap: OptionsRow[]): SmileSkewness[] {
  const valid = snap.filter(
    (r) => r.implied_vol != null && r.implied_vol > 0 && r.implied_vol < 5,
  );
  if (valid.length === 0) return [];

  const expiryMonths = Array.from(new Set(valid.map((r) => r.expiry_month))).sort(
    (a, b) => parseInt(a.replace("月", "")) - parseInt(b.replace("月", "")),
  );

  const results: SmileSkewness[] = [];

  for (const em of expiryMonths) {
    const emData = valid.filter((r) => r.expiry_month === em);
    const calls = emData.filter((r) => r.option_type === "CALL");
    const puts = emData.filter((r) => r.option_type === "PUT");

    const callSkew = computeWeightedSkewness(calls);
    const putSkew = computeWeightedSkewness(puts);

    // Overall: pool CALL + PUT with OI weights
    const overallSkew = computeWeightedSkewness(emData);

    results.push({
      expiry: em,
      callSkew,
      putSkew,
      overallSkew,
    });
  }

  return results;
}

/**
 * OI-weighted skewness (3rd standardized moment) of a set of IV values.
 * Returns null if fewer than 3 data points or zero total weight.
 */
function computeWeightedSkewness(rows: OptionsRow[]): number | null {
  if (rows.length < 3) return null;

  const xs = rows.map((r) => (r.implied_vol as number) * 100); // convert to %
  const ws = rows.map((r) => Math.max(1, r.open_interest)); // at least weight=1

  const wSum = ws.reduce((a, b) => a + b, 0);
  if (wSum === 0) return null;

  const mean = xs.reduce((s, x, i) => s + ws[i] * x, 0) / wSum;
  const variance = xs.reduce((s, x, i) => s + ws[i] * (x - mean) ** 2, 0) / wSum;
  const std = Math.sqrt(variance);
  if (std < 1e-8) return null;

  const skewness = xs.reduce((s, x, i) => s + ws[i] * ((x - mean) / std) ** 3, 0) / wSum;
  return skewness;
}

/**
 * Max pain price — the strike that minimizes total option holder payout.
 */
export function computeMaxPain(snap: OptionsRow[]): number | null {
  const callOi = new Map<number, number>();
  const putOi = new Map<number, number>();
  for (const r of snap) {
    if (r.option_type === "CALL") {
      callOi.set(r.strike_price, (callOi.get(r.strike_price) ?? 0) + r.open_interest);
    } else {
      putOi.set(r.strike_price, (putOi.get(r.strike_price) ?? 0) + r.open_interest);
    }
  }
  const strikes = Array.from(new Set([...callOi.keys(), ...putOi.keys()])).sort((a, b) => a - b);
  if (strikes.length === 0) return null;
  let minPayout = Infinity;
  let maxPain = strikes[0];
  for (const p of strikes) {
    let cp = 0;
    let pp = 0;
    for (const [k, oi] of callOi.entries()) cp += Math.max(0, p - k) * oi;
    for (const [k, oi] of putOi.entries()) pp += Math.max(0, k - p) * oi;
    const total = cp + pp;
    if (total < minPayout) {
      minPayout = total;
      maxPain = p;
    }
  }
  return maxPain;
}

/**
 * Gamma exposure by strike (yuan per 1% spot move).
 * GEX > 0 → dealers dampen volatility; GEX < 0 → dealers amplify.
 */
export function computeGexProfile(snap: OptionsRow[]): Record<number, number> {
  const gex: Record<number, number> = {};
  for (const row of snap) {
    let sigma = row.implied_vol;
    if (sigma == null || sigma <= 0) {
      // Recompute IV if missing
      const S = row.underlying_close / PRICE_SCALE;
      const K = row.strike_price / PRICE_SCALE;
      const price = row.settle / CONTRACT_SIZE;
      const T = row.days_to_expiry / 365;
      sigma = impliedVolDefault(price, S, K, T, row.option_type);
      if (sigma == null || !Number.isFinite(sigma) || sigma <= 0) continue;
    }
    const S = row.underlying_close / PRICE_SCALE;
    const K = row.strike_price / PRICE_SCALE;
    const T = row.days_to_expiry / 365;
    if (T <= 0 || sigma <= 0 || S <= 0 || K <= 0) continue;
    const { gamma } = computeGreeks(S, K, T, 0.02, sigma, row.option_type);
    const sign = row.option_type === "CALL" ? 1 : -1;
    const contribution = sign * row.open_interest * gamma * S * S * 0.01 * CONTRACT_SIZE;
    gex[row.strike_price] = (gex[row.strike_price] ?? 0) + contribution;
  }
  return gex;
}

/**
 * Find the ATM option row — the contract with moneyness_ratio closest to 1.0
 * (preferred: with valid IV + Greeks; falls back to any row if none qualify).
 */
export function findAtmOption(snap: OptionsRow[]): OptionsRow | null {
  if (snap.length === 0) return null;
  const valid = snap.filter(
    (r) => r.implied_vol != null && r.implied_vol > 0 && r.implied_vol < 5,
  );
  const pool = valid.length > 0 ? valid : snap;
  let atmRow = pool[0];
  let atmDist = Math.abs(pool[0].moneyness_ratio - 1.0);
  for (const r of pool) {
    const d = Math.abs(r.moneyness_ratio - 1.0);
    if (d < atmDist) {
      atmDist = d;
      atmRow = r;
    }
  }
  return atmRow;
}

/** Aggregated ATM Greeks for the snapshot (from the ATM option row). */
export interface AtmGreeks {
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
  rho: number | null;
}

/**
 * IV skew: OTM put IV - ATM IV (positive = downside protection demand).
 * Returns [atm_iv, otm_put_iv, skew, atm_greeks].
 */
export function computeIvSkew(
  snap: OptionsRow[],
): [number | null, number | null, number | null, AtmGreeks] {
  const empty: AtmGreeks = {
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    rho: null,
  };
  const valid = snap.filter(
    (r) => r.implied_vol != null && r.implied_vol > 0 && r.implied_vol < 5,
  );
  if (valid.length === 0) return [null, null, null, empty];
  // ATM = row with moneyness_ratio closest to 1.0
  let atmRow = valid[0];
  let atmDist = Math.abs(valid[0].moneyness_ratio - 1.0);
  for (const r of valid) {
    const d = Math.abs(r.moneyness_ratio - 1.0);
    if (d < atmDist) {
      atmDist = d;
      atmRow = r;
    }
  }
  const atmIv = atmRow.implied_vol;
  const atmGreeks: AtmGreeks = {
    delta: atmRow.delta,
    gamma: atmRow.gamma,
    theta: atmRow.theta,
    vega: atmRow.vega,
    rho: atmRow.rho,
  };
  // OTM put = PUT with moneyness closest to 0.90 and < 0.95
  const otmPuts = valid.filter(
    (r) => r.option_type === "PUT" && r.moneyness_ratio < 0.95,
  );
  if (otmPuts.length === 0) return [atmIv, null, null, atmGreeks];
  let otmRow = otmPuts[0];
  let otmDist = Math.abs(otmPuts[0].moneyness_ratio - 0.9);
  for (const r of otmPuts) {
    const d = Math.abs(r.moneyness_ratio - 0.9);
    if (d < otmDist) {
      otmDist = d;
      otmRow = r;
    }
  }
  const otmPutIv = otmRow.implied_vol;
  return [atmIv, otmPutIv, (otmPutIv ?? 0) - (atmIv ?? 0), atmGreeks];
}

/**
 * OI-weighted average strike — market's implied center.
 */
export function computeOiWeightedStrike(snap: OptionsRow[]): number | null {
  const byStrike = new Map<number, number>();
  for (const r of snap) {
    byStrike.set(r.strike_price, (byStrike.get(r.strike_price) ?? 0) + r.open_interest);
  }
  let totalOi = 0;
  let weightedSum = 0;
  for (const [k, oi] of byStrike.entries()) {
    totalOi += oi;
    weightedSum += k * oi;
  }
  if (totalOi === 0) return null;
  return weightedSum / totalOi;
}

/**
 * Net OI positioning: (Call_OI - Put_OI) / Total_OI. Range [-1, +1].
 */
export function computeNetOiPositioning(snap: OptionsRow[]): number {
  let callTotal = 0;
  let putTotal = 0;
  for (const r of snap) {
    if (r.option_type === "CALL") callTotal += r.open_interest;
    else putTotal += r.open_interest;
  }
  const total = callTotal + putTotal;
  if (total === 0) return 0;
  return (callTotal - putTotal) / total;
}

/**
 * Herfindahl concentration index of OI by strike (0=dispersed, 1=monopoly).
 */
export function computeOiConcentration(snap: OptionsRow[]): number {
  const byStrike = new Map<number, number>();
  for (const r of snap) {
    byStrike.set(r.strike_price, (byStrike.get(r.strike_price) ?? 0) + r.open_interest);
  }
  const total = Array.from(byStrike.values()).reduce((a, b) => a + b, 0);
  if (total === 0) return 0;
  let sumSq = 0;
  for (const oi of byStrike.values()) {
    const share = oi / total;
    sumSq += share * share;
  }
  return sumSq;
}

export interface SnapshotStats {
  snap: OptionsRow[];
  S: number; // underlying close in yuan
  S_raw: number; // underlying close in 厘
  callWall: number | null;
  putWall: number | null;
  callWallOi: number;
  putWallOi: number;
  maxPain: number | null;
  atmIv: number | null;
  ivSkew: number | null;
  smileSkewness: SmileSkewness[];
  atmGreeks: AtmGreeks;
  netGex: number;
  oiWeighted: number | null;
  netPos: number;
  concentration: number;
  pcRatio: number;
  totalCall: number;
  totalPut: number;
}

/**
 * Compute all sentiment stats for one snapshot date.
 */
export function computeSnapshotStats(snap: OptionsRow[]): SnapshotStats | null {
  if (snap.length === 0) return null;
  const S_raw = snap[0].underlying_close;
  const S = S_raw / PRICE_SCALE;

  // Per-strike OI aggregation
  const callOi = new Map<number, number>();
  const putOi = new Map<number, number>();
  for (const r of snap) {
    if (r.option_type === "CALL") {
      callOi.set(r.strike_price, (callOi.get(r.strike_price) ?? 0) + r.open_interest);
    } else {
      putOi.set(r.strike_price, (putOi.get(r.strike_price) ?? 0) + r.open_interest);
    }
  }
  // Wall detection: a strike is a "wall" only when its call/put OI is
  // significantly larger than the opposing side at the same strike (ratio > 1.33,
  // i.e. >33% more). Among qualifying strikes, pick the one with the highest OI.
  const WALL_RATIO_THRESHOLD = 1.33;
  let callWall: number | null = null;
  let callWallOi = 0;
  for (const [k, oi] of callOi.entries()) {
    const opposing = putOi.get(k) ?? 0;
    if (opposing > 0 && oi / opposing < WALL_RATIO_THRESHOLD) continue;
    if (oi > callWallOi) {
      callWallOi = oi;
      callWall = k;
    }
  }
  let putWall: number | null = null;
  let putWallOi = 0;
  for (const [k, oi] of putOi.entries()) {
    const opposing = callOi.get(k) ?? 0;
    if (opposing > 0 && oi / opposing < WALL_RATIO_THRESHOLD) continue;
    if (oi > putWallOi) {
      putWallOi = oi;
      putWall = k;
    }
  }

  const maxPain = computeMaxPain(snap);
  const [atmIv, _otmPutIv, ivSkew, atmGreeks] = computeIvSkew(snap);
  const smileSkewness = computeSmileSkewness(snap);
  const gexProfile = computeGexProfile(snap);
  const netGex = Object.values(gexProfile).reduce((a, b) => a + b, 0);
  const oiWeighted = computeOiWeightedStrike(snap);
  const netPos = computeNetOiPositioning(snap);
  const concentration = computeOiConcentration(snap);
  const totalCall = Array.from(callOi.values()).reduce((a, b) => a + b, 0);
  const totalPut = Array.from(putOi.values()).reduce((a, b) => a + b, 0);
  const pcRatio = totalCall > 0 ? totalPut / totalCall : NaN;

  return {
    snap,
    S,
    S_raw,
    callWall,
    putWall,
    callWallOi,
    putWallOi,
    maxPain,
    atmIv,
    ivSkew,
    smileSkewness,
    atmGreeks,
    netGex,
    oiWeighted,
    netPos,
    concentration,
    pcRatio,
    totalCall,
    totalPut,
  };
}
