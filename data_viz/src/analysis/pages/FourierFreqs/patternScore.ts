/**
 * Consolidated periodic-pattern SCORE for the Fourier Frequencies page.
 *
 * The FFT amp spectrum says WHICH day freqs carry energy; recurrence
 * audits say which actually repeat. This module consolidates BOTH into
 * ONE bar per integer day freq d — the "periodic noticeable highs and
 * lows" score:
 *
 *   score(d) = (amp(d) / σ_band) × recEXT(d) × acfFrac(d)
 *
 *   • amp(d)/σ_band — NOTICEABILITY: the day's energy-merged FFT
 *     amplitude in units of the swing-band σ (σ_band² = Σ_{d'≤N/4}
 *     amp(d')² / 2). A cycle must stand out from the band's total
 *     swing energy before it is worth calling a pattern.
 *   • recEXT(d) — EXTREMA EVIDENCE: fraction of the window's
 *     prominence-filtered alternating swing highs/lows whose
 *     FULL-CYCLE period estimates (consecutive-gap sums, plus doubled
 *     single gaps as half-cycle corroboration) land within ±15% of d,
 *     normalized by the max possible cycles floor((N−d)/d) and capped
 *     at 1. Only extrema standing out by ≥ 1.5× the daily-change σ
 *     count — this is the "noticeable highs and lows" factor.
 *   • acfFrac(d) — ACF COHERENCE (the noise gate): fraction of the
 *     multiples m·d at which the MA-detrended window is significantly
 *     self-similar (biased acf(m·d) ≥ 1.96/√N, ≥ 1 full period of
 *     overlap). A recurring pattern is self-similar at EVERY multiple;
 *     noise at none — this factor alone takes pure-noise scores to ~0.
 *
 * Detrending: centered-MA residual (window L = odd(⌊N/4⌋)) removes
 * periods ≥ ~N/4 — the trend that would otherwise dilute both the ACF
 * and the extrema evidence. O(N·L), no FFT needed client-side.
 *
 * Auditable range: d ≤ N/3 — a period needs ≥ ~3 cycles in the window
 * before "always repeats" can be claimed; longer periods score 0 and
 * are flagged not auditable.
 *
 * Validation anchors (analyze/fourier_freqs/_study_consolidated.py):
 * a pure 10d sine scores ≈ 0.89 at 10d; 10d sine + strong trend + noise
 * still peaks at 10d; pure noise scores ≈ 0 everywhere; 000300's real
 * ~39-58d swing family reaches ~0.10 on a 750d window.
 */

/** Evidence-vs-day tolerance (a pool entry within ±15% of d is a hit). */
const TOL = 0.15;
/** Extrema prominence threshold in units of the daily-change σ. */
const K_PROM = 1.5;
/** 95% white-noise ACF confidence multiplier. */
const Z95 = 1.96;

/** Consolidated score audit for ONE integer day frequency. */
export interface PatternScoreAudit {
  /** Day period in trading days. */
  day: number;
  /** Consolidated score: ampNorm × recEXT × acfFrac (0 when not auditable). */
  score: number;
  /** amp(d) / σ_band — noticeability (can exceed 1; ≤ √2 for a pure tone). */
  ampNorm: number;
  /** Extrema-evidence fraction (pool hits / max possible cycles, cap 1). */
  recEXT: number;
  /** ACF coherence fraction (significant multiples / max possible). */
  acfFrac: number;
  /** Raw evidence-pool hits within ±15% of this day. */
  evidence: number;
  /** Max possible cycles within the window: max(1, floor((N−day)/day)). */
  maxRepeats: number;
  /** MEASURED repeats — multiples m with acf(m·day) ≥ 1.96/√N. */
  repeats: number;
  /** Mean acf across all multiples ∈ [−1, 1]. */
  avgAcf: number;
  /** False when day > N/3 — too few cycles to claim recurrence. */
  auditable: boolean;
}

/** Window-level results of the consolidated score audit. */
export interface PatternScoreResult {
  /** Per-day audits keyed by integer day freq (spectrum-merged days). */
  audits: Map<number, PatternScoreAudit>;
  /** Swing-band σ (yuan): sqrt(Σ_{d≤N/4} amp(d)² / 2). */
  sigmaBand: number;
  /** Number of prominence-filtered alternating extrema in the window. */
  nExtrema: number;
}

/** Centered-MA detrended residual — removes periods ≥ ~N/4 (the trend). */
function maResidual(w: readonly number[]): Float64Array {
  const N = w.length;
  const L = Math.max(3, Math.floor(N / 4) | 1); // odd window ~N/4
  const half = L >> 1;
  const cs = new Float64Array(N + 1); // prefix sums
  for (let i = 0; i < N; i++) cs[i + 1] = cs[i] + w[i];
  const r = new Float64Array(N);
  for (let i = 0; i < N; i++) {
    const lo = Math.max(0, i - half);
    const hi = Math.min(N, i + half + 1);
    r[i] = w[i] - (cs[hi] - cs[lo]) / (hi - lo);
  }
  return r;
}

/** Per-day ACF recurrence audit of a (detrended) series.
 *  Biased ACF; a multiple counts when acf(m·d) ≥ 1.96/√N. */
function acfRecurrence(
  x: Float64Array,
): Map<number, { repeats: number; maxRepeats: number; frac: number; avgAcf: number }> {
  const N = x.length;
  const out = new Map<number, { repeats: number; maxRepeats: number; frac: number; avgAcf: number }>();
  const maxDay = Math.floor(N / 2);
  let mean = 0;
  for (let i = 0; i < N; i++) mean += x[i];
  mean /= N;
  const xc = new Float64Array(N);
  let denom = 0;
  for (let i = 0; i < N; i++) {
    xc[i] = x[i] - mean;
    denom += xc[i] * xc[i];
  }
  const tau = Z95 / Math.sqrt(N);
  if (denom > 0) {
    const acf = new Float64Array(N);
    for (let lag = 0; lag < N; lag++) {
      let s = 0;
      for (let t = lag; t < N; t++) s += xc[t] * xc[t - lag];
      acf[lag] = s / denom;
    }
    for (let day = 2; day <= maxDay; day++) {
      const maxRepeats = Math.floor((N - day) / day);
      if (maxRepeats < 1) {
        out.set(day, { repeats: 0, maxRepeats: 0, frac: 0, avgAcf: 0 });
        continue;
      }
      let repeats = 0;
      let sum = 0;
      for (let m = 1; m <= maxRepeats; m++) {
        const a = acf[m * day];
        sum += a;
        if (a >= tau) repeats += 1;
      }
      out.set(day, { repeats, maxRepeats, frac: repeats / maxRepeats, avgAcf: sum / maxRepeats });
    }
  } else {
    for (let day = 2; day <= maxDay; day++) {
      out.set(day, { repeats: 0, maxRepeats: Math.floor((N - day) / day), frac: 0, avgAcf: 0 });
    }
  }
  return out;
}

/** Topographic prominence of w[i] as an extremum (sign +1 max, −1 min):
 *  height minus the higher of the two flanking interval minima, where
 *  each interval extends until the signal crosses the extremum's level
 *  (a higher peak / deeper valley) or the window border. */
function signedProminence(w: readonly number[], i: number, sign: 1 | -1): number {
  const N = w.length;
  const h = sign * w[i];
  let leftMin = h;
  for (let j = i - 1; j >= 0; j--) {
    const v = sign * w[j];
    if (v > h) break;
    if (v < leftMin) leftMin = v;
  }
  let rightMin = h;
  for (let j = i + 1; j < N; j++) {
    const v = sign * w[j];
    if (v > h) break;
    if (v < rightMin) rightMin = v;
  }
  return h - Math.max(leftMin, rightMin);
}

/** Alternating-extrema evidence pool of the window.
 *
 * Swing highs/lows must (a) be strict local extrema and (b) stand out
 * by ≥ K_PROM × σ(daily change). Consecutive kept extrema are forced
 * to alternate (first-of-run wins). The POOL of full-cycle period
 * estimates = consecutive-gap sums (hi→lo→hi) + doubled single gaps
 * (half-cycle corroboration). */
function extremaEvidencePool(w: readonly number[]): { pool: number[]; nExtrema: number } {
  const N = w.length;
  if (N < 3) return { pool: [], nExtrema: 0 };
  // σ of daily changes (population, ddof=0) — the noise floor
  const d = new Float64Array(N - 1);
  let dmean = 0;
  for (let i = 0; i < N - 1; i++) {
    d[i] = w[i + 1] - w[i];
    dmean += d[i];
  }
  dmean /= N - 1;
  let varc = 0;
  for (let i = 0; i < N - 1; i++) {
    const v = d[i] - dmean;
    varc += v * v;
  }
  const prom = K_PROM * Math.sqrt(varc / (N - 1));

  // strict local maxima / minima passing the prominence filter
  const ext: Array<{ idx: number; typ: 1 | -1 }> = [];
  for (let i = 1; i < N - 1; i++) {
    if (w[i] > w[i - 1] && w[i] > w[i + 1]) {
      if (signedProminence(w, i, 1) >= prom) ext.push({ idx: i, typ: 1 });
    } else if (w[i] < w[i - 1] && w[i] < w[i + 1]) {
      if (signedProminence(w, i, -1) >= prom) ext.push({ idx: i, typ: -1 });
    }
  }
  ext.sort((a, b) => a.idx - b.idx);
  // force alternation — keep the first of each same-type run
  const kept: Array<{ idx: number; typ: 1 | -1 }> = [];
  for (const e of ext) {
    if (kept.length === 0 || kept[kept.length - 1].typ !== e.typ) kept.push(e);
  }
  // half-cycle gaps between consecutive alternating extrema
  const gaps: number[] = [];
  for (let i = 1; i < kept.length; i++) gaps.push(kept[i].idx - kept[i - 1].idx);
  // full-cycle period estimates (empty when fewer than 2 gaps)
  const pool: number[] = [];
  if (gaps.length > 1) {
    for (let i = 1; i < gaps.length; i++) pool.push(gaps[i - 1] + gaps[i]);
    for (const g of gaps) pool.push(2 * g);
  }
  return { pool, nExtrema: kept.length };
}

/** Consolidated periodic-pattern score audit.
 *
 * @param closes Window close prices (chronological, ending at the
 *  spectrum's last_date). Drives all time-domain factors.
 * @param dayAmps Energy-merged FFT amplitude per integer day freq
 *  (day → amp, yuan) — the Fourier reference, already merged by the
 *  chart builder.
 * @param rangeDays The spectrum window length N (bin count basis).
 */
export function auditPatternScores(
  closes: readonly number[],
  dayAmps: ReadonlyMap<number, number>,
  rangeDays: number,
): PatternScoreResult {
  const N = closes.length;

  // σ_band — total swing energy of the band (periods ≤ N/4)
  let sb2 = 0;
  const bandMaxDay = Math.floor(rangeDays / 4);
  for (const [day, a] of dayAmps) {
    if (day <= bandMaxDay) sb2 += a * a;
  }
  const sigmaBand = Math.sqrt(sb2 / 2);

  const audits = new Map<number, PatternScoreAudit>();
  if (N < 8) return { audits, sigmaBand, nExtrema: 0 };

  // time-domain factors over d = 2..⌊N/2⌋
  const { pool, nExtrema } = extremaEvidencePool(closes);
  const acf = acfRecurrence(maResidual(closes));

  for (const day of dayAmps.keys()) {
    const amp = dayAmps.get(day) ?? 0;
    const ampNorm = sigmaBand > 0 ? amp / sigmaBand : 0;
    const auditable = day <= Math.floor(N / 3);
    if (day < 2 || day > Math.floor(N / 2)) {
      // beyond the auditable recurrence range (incl. the k=1 full-window
      // bin) — amp shows, but no recurrence claim is possible
      audits.set(day, {
        day, score: 0, ampNorm, recEXT: 0, acfFrac: 0,
        evidence: 0, maxRepeats: 0, repeats: 0, avgAcf: 0,
        auditable: false,
      });
      continue;
    }
    // extrema evidence: pool hits within ±TOL·day, normalized by the
    // max possible cycles, capped at 1
    const tol = TOL * day;
    let evidence = 0;
    for (const p of pool) {
      if (Math.abs(p - day) <= tol) evidence += 1;
    }
    const maxRepeats = Math.max(1, Math.floor((N - day) / day));
    const recEXT = Math.min(evidence / maxRepeats, 1);
    const a = acf.get(day) ?? { repeats: 0, maxRepeats, frac: 0, avgAcf: 0 };
    const score = auditable ? ampNorm * recEXT * a.frac : 0;
    audits.set(day, {
      day, score, ampNorm, recEXT, acfFrac: a.frac,
      evidence, maxRepeats, repeats: a.repeats, avgAcf: a.avgAcf,
      auditable,
    });
  }
  return { audits, sigmaBand, nExtrema };
}
