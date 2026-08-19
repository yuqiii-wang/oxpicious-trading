/**
 * Black-Scholes option pricing + implied volatility + Greeks.
 * Ported from plot_szse_options.py — uses bisection (brentq is not in JS).
 *
 * Note: the options_combined.csv already contains pre-computed implied_vol +
 * Greeks columns, so these functions are only called when recomputing IV for
 * snapshots not present in the CSV (rare).
 */
import { RISK_FREE_RATE } from "@/theme/chart-palette";

/** Standard normal PDF. */
function normPdf(x: number): number {
  return Math.exp(-0.5 * x * x) / Math.sqrt(2 * Math.PI);
}

/** Standard normal CDF via Abramowitz & Stegun approximation (max err 7.5e-8). */
function normCdf(x: number): number {
  const t = 1 / (1 + 0.2316419 * Math.abs(x));
  const d = 0.3989423 * Math.exp(-0.5 * x * x);
  const p =
    d *
    t *
    (0.3193815 +
      t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))));
  return x > 0 ? 1 - p : p;
}

export function bsCallPrice(S: number, K: number, T: number, r: number, sigma: number): number {
  if (T <= 0 || sigma <= 0) return Math.max(0, S - K);
  const d1 = (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * Math.sqrt(T));
  const d2 = d1 - sigma * Math.sqrt(T);
  return S * normCdf(d1) - K * Math.exp(-r * T) * normCdf(d2);
}

export function bsPutPrice(S: number, K: number, T: number, r: number, sigma: number): number {
  if (T <= 0 || sigma <= 0) return Math.max(0, K - S);
  const d1 = (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * Math.sqrt(T));
  const d2 = d1 - sigma * Math.sqrt(T);
  return K * Math.exp(-r * T) * normCdf(-d2) - S * normCdf(-d1);
}

export type OptionType = "CALL" | "PUT";

/**
 * Implied volatility via bisection. Returns NaN if no solution exists.
 */
export function impliedVol(
  price: number,
  S: number,
  K: number,
  T: number,
  r: number,
  optType: OptionType,
): number {
  if (T <= 0 || price <= 0 || S <= 0 || K <= 0) return NaN;
  const fn = optType === "CALL" ? bsCallPrice : bsPutPrice;
  const intrinsic = optType === "CALL" ? Math.max(0, S - K) : Math.max(0, K - S);
  if (price < intrinsic) return NaN;

  let lo = 1e-6;
  let hi = 5.0;
  let fLo = fn(S, K, T, r, lo) - price;
  let fHi = fn(S, K, T, r, hi) - price;
  if (fLo * fHi > 0) return NaN;

  for (let i = 0; i < 200; i++) {
    const mid = (lo + hi) / 2;
    const fMid = fn(S, K, T, r, mid) - price;
    if (Math.abs(fMid) < 1e-8 || (hi - lo) < 1e-8) return mid;
    if (fLo * fMid < 0) {
      hi = mid;
      fHi = fMid;
    } else {
      lo = mid;
      fLo = fMid;
    }
  }
  return (lo + hi) / 2;
}

export interface Greeks {
  delta: number;
  theta: number;
  gamma: number;
  vega: number;
  rho: number;
}

/**
 * Compute Black-Scholes Greeks. Annualized theta.
 */
export function computeGreeks(
  S: number,
  K: number,
  T: number,
  r: number,
  sigma: number,
  optType: OptionType,
): Greeks {
  if (T <= 0 || sigma <= 0 || S <= 0 || K <= 0) {
    return { delta: 0, theta: 0, gamma: 0, vega: 0, rho: 0 };
  }
  const sqrtT = Math.sqrt(T);
  const d1 = (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT);
  const d2 = d1 - sigma * sqrtT;
  const pdfD1 = normPdf(d1);
  const discount = Math.exp(-r * T);
  const gamma = pdfD1 / (S * sigma * sqrtT);
  const vega = S * pdfD1 * sqrtT / 100; // per 1% change in sigma
  let delta: number;
  let theta: number;
  let rho: number;
  if (optType === "CALL") {
    delta = normCdf(d1);
    theta = (-S * pdfD1 * sigma / (2 * sqrtT) - r * K * discount * normCdf(d2)) / 365;
    rho = K * T * discount * normCdf(d2) / 100;
  } else {
    delta = normCdf(d1) - 1;
    theta = (-S * pdfD1 * sigma / (2 * sqrtT) + r * K * discount * normCdf(-d2)) / 365;
    rho = -K * T * discount * normCdf(-d2) / 100;
  }
  return { delta, theta, gamma, vega, rho };
}

/**
 * Convenience: use project default risk-free rate.
 */
export function impliedVolDefault(
  price: number,
  S: number,
  K: number,
  T: number,
  optType: OptionType,
  r = RISK_FREE_RATE,
): number {
  return impliedVol(price, S, K, T, r, optType);
}
