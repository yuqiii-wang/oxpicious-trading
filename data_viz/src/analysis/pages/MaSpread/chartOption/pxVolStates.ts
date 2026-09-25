/**
 * Client-side px_vol state computation + shading for the MA-Spread charts.
 *
 * Source: the price speed × trading-amount STATE categories of
 * analysis.mov_ave_price_vs_amt (the mov_ave_spread registry, see
 * analyze/mov_ave_spread/px_vol.py add_px_vol_features and
 * database/sql/analysis/19_price_vs_amt.sql). The panel recomputes the
 * per-date state client-side from the chart's own rows. The inputs match
 * the registry exactly:
 *   price  = short_value of a price pair = COALESCE(adj_close, close)
 *            (the same COALESCE(a.adj_close, b.close) the registry's price
 *            source uses)
 *   amount = trading_amount (basic_stats) carried on every detail row
 *
 * Per (code, date), a day joins a state when BOTH legs hold, evaluated
 * with information available at that day (every rolling stat shifted 1
 * row → no look-ahead):
 *   t = ret_1d / σ_ret(code, 255 rows ending t-1, min 60, ddof=1)
 *       sharp_up t > 2.0 | slow_up 1.26 < t <= 2.0 | flat | slow_dn | sharp_dn
 *       (never fires when σ_ret is NaN or below sigma_floor 0.005)
 *   z = (log(amount[t]) - μ) / σ  — the z-scored log AMOUNT LEVEL (what
 *       "Amt Up/Down" claim: the day's amount vs the code's OWN trailing
 *       amount distribution), μ/σ = the rolling-255 (min 60) moments of
 *       log(amount), shifted 1
 *       heavy z > 2.0 | normal -0.92 <= z <= 2.0 | shrink z < -0.92
 *       (the retired 量比-ratio z — amount / its own 5-day trailing mean,
 *       z vs the ratio's moments — fired "Amt Up" on drought bounces: in
 *       a declining-volume regime the 5-day base collapses, so a day
 *       whose amount sat far below the code's level scored ratio ≈ 1.7
 *       → z > 2)
 */

/** Price-speed states in PX_VOL_SPEEDS order (sql/comments canonical). */
export type PxVolSpeed = "sharp_up" | "slow_up" | "flat" | "slow_dn" | "sharp_dn";
/** Trading-amount states in PX_VOL_VOL_STATES order. */
export type PxVolVolState = "heavy" | "normal" | "shrink";

/** Registry thresholds (19_price_vs_amt.sql recorded build parameters). */
export const PX_VOL_SIGMA_WINDOW = 255;
export const PX_VOL_SIGMA_MIN_DAYS = 60;
export const PX_VOL_K_SHARP = 2.0;
export const PX_VOL_K_SLOW_UP = 1.26;
export const PX_VOL_K_SLOW_DN = 1.29;
export const PX_VOL_Z_HEAVY = 2.0;
export const PX_VOL_Z_SHRINK = -0.92;
export const PX_VOL_SIGMA_FLOOR = 0.005;

/** Top button row — price speed (user-facing labels). */
export const PX_VOL_SPEED_OPTIONS: Array<{
  key: PxVolSpeed;
  label: string;
  /** Weight driving the shade strength (sharp 3 / slow 2 / flat 1). */
  weight: number;
}> = [
  { key: "sharp_up", label: "Sharp↑", weight: 3 },
  { key: "slow_up", label: "Slow↑", weight: 2 },
  { key: "flat", label: "Flat", weight: 1 },
  { key: "slow_dn", label: "Slow↓", weight: 2 },
  { key: "sharp_dn", label: "Sharp↓", weight: 3 },
];

/** Second button row — trading-amount state (heavy/increasing, flat/normal,
 *  shrink/decreasing). */
export const PX_VOL_VOL_OPTIONS: Array<{
  key: PxVolVolState;
  label: string;
  /** Weight driving the shade strength (heavy 3 / normal 2 / shrink 1). */
  weight: number;
}> = [
  { key: "heavy", label: "Amt Up", weight: 3 },
  { key: "normal", label: "Amt Flat", weight: 2 },
  { key: "shrink", label: "Amt Down", weight: 1 },
];

/** Base shade hue per speed direction: green = rise (growth), red = drop,
 *  gray = flat (no directional claim). */
const PX_VOL_BASE_COLORS: Record<PxVolSpeed, string> = {
  sharp_up: "46, 125, 50",   // green  (#2E7D32)
  slow_up: "46, 125, 50",
  flat: "117, 117, 117",     // gray   (#757575)
  slow_dn: "198, 40, 40",    // red    (#C62828)
  sharp_dn: "198, 40, 40",
};

/** Shade alpha for one (speed, vol) pick — the strength tiers: the strongest
 *  combo (sharp × heavy, weight 3×3=9) shades darkest; weaker speeds and
 *  weaker amount states lighten toward 0.10. */
export function pxVolShadeColor(speed: PxVolSpeed, vol: PxVolVolState): string {
  const sw = PX_VOL_SPEED_OPTIONS.find((o) => o.key === speed)?.weight ?? 1;
  const vw = PX_VOL_VOL_OPTIONS.find((o) => o.key === vol)?.weight ?? 1;
  const alpha = 0.1 + 0.03 * (sw * vw); // 0.13 (flat×shrink) … 0.37 (sharp×heavy)
  return `rgba(${PX_VOL_BASE_COLORS[speed]}, ${alpha.toFixed(2)})`;
}

/** A solid accent for the legend marker / active chip (the base hue). */
export function pxVolAccentColor(speed: PxVolSpeed): string {
  return `rgb(${PX_VOL_BASE_COLORS[speed]})`;
}

/** One per-date px_vol state (null when the day joins no bucket — invalid
 *  σ/amt_ratio, below the floor, or missing inputs). */
export interface PxVolDayState {
  speed: PxVolSpeed;
  vol: PxVolVolState;
}

/** Compute the per-date px_vol state for one code's chart rows.
 *  Replicates add_px_vol_features exactly (same windows / min_periods /
 *  ddof / shifts). `closes` and `amounts` are index-aligned arrays. */
export function computePxVolStates(
  closes: Array<number | null>,
  amounts: Array<number | null>,
): Array<PxVolDayState | null> {
  const n = closes.length;
  const out: Array<PxVolDayState | null> = new Array(n).fill(null);
  if (n === 0) return out;

  // --- ret_1d -----------------------------------------------------------
  const ret: Array<number | null> = new Array(n).fill(null);
  for (let i = 1; i < n; i++) {
    const p = closes[i];
    const prev = closes[i - 1];
    if (p == null || prev == null || Math.abs(prev) <= 1e-12) continue;
    const r = p / prev - 1;
    if (Number.isFinite(r)) ret[i] = r;
  }

  // --- σ_ret: rolling sample std (ddof=1) over the trailing W rows ending
  //     at i (min_periods), then SHIFTED 1 row (px_sigma[i] = σ[i-1]) -----
  const sigmaLag: Array<number | null> = new Array(n).fill(null);
  {
    // Sliding sums for O(n) mean/m2 (Welford-free: sum & sum-of-squares
    // with NaN-free compaction via a ring of valid values is overkill —
    // a simple recompute per step over the ≤255-row window is ~1.3M ops).
    for (let i = 0; i < n; i++) {
      const lo = Math.max(0, i - PX_VOL_SIGMA_WINDOW + 1);
      let sum = 0;
      let sum2 = 0;
      let cnt = 0;
      for (let j = lo; j <= i; j++) {
        const v = ret[j];
        if (v == null) continue;
        sum += v;
        sum2 += v * v;
        cnt += 1;
      }
      if (cnt < PX_VOL_SIGMA_MIN_DAYS || cnt < 2) continue;
      const mean = sum / cnt;
      const variance = (sum2 - cnt * mean * mean) / (cnt - 1); // ddof=1
      const sigma = variance > 0 ? Math.sqrt(variance) : null;
      if (sigma != null && Number.isFinite(sigma)) {
        // Shift 1 row: σ over [lo, i] is row i's own stat — row i+1 uses
        // it as its (look-ahead-free) bar.
        if (i + 1 < n) sigmaLag[i + 1] = sigma;
      }
    }
  }

  // --- 量能水平 z (log-amount LEVEL z): log(amount) moments over the
  //     trailing W rows (min_periods), SHIFTED 1 row. The LEVEL statement
  //     is what "Amt Up/Flat/Down" claim — NOT a 5-day-baseline surge
  //     (the retired ratio z fired Amt Up on drought bounces). -----------
  const logAmt: Array<number | null> = new Array(n).fill(null);
  for (let i = 0; i < n; i++) {
    const ta = amounts[i];
    if (ta == null || ta <= 0) continue;
    const l = Math.log(ta);
    if (Number.isFinite(l)) logAmt[i] = l;
  }
  const muLag: Array<number | null> = new Array(n).fill(null);
  const sigLag: Array<number | null> = new Array(n).fill(null);
  for (let i = 0; i < n; i++) {
    const lo = Math.max(0, i - PX_VOL_SIGMA_WINDOW + 1);
    let sum = 0;
    let sum2 = 0;
    let cnt = 0;
    for (let j = lo; j <= i; j++) {
      const v = logAmt[j];
      if (v == null) continue;
      sum += v;
      sum2 += v * v;
      cnt += 1;
    }
    if (cnt < PX_VOL_SIGMA_MIN_DAYS || cnt < 2) continue;
    const mean = sum / cnt;
    const variance = (sum2 - cnt * mean * mean) / (cnt - 1); // ddof=1
    const sig = variance > 0 ? Math.sqrt(variance) : null;
    if (!Number.isFinite(mean) || sig == null || !Number.isFinite(sig)) continue;
    if (i + 1 < n) {
      muLag[i + 1] = mean;
      sigLag[i + 1] = sig;
    }
  }

  // --- classify -----------------------------------------------------------
  for (let i = 0; i < n; i++) {
    const r = ret[i];
    const sigma = sigmaLag[i];
    if (r == null || sigma == null || sigma <= 0 || sigma < PX_VOL_SIGMA_FLOOR) continue;
    const t = r / sigma;
    let speed: PxVolSpeed;
    if (t > PX_VOL_K_SHARP) speed = "sharp_up";
    else if (t > PX_VOL_K_SLOW_UP) speed = "slow_up";
    else if (t >= -PX_VOL_K_SLOW_DN && t <= PX_VOL_K_SLOW_UP) speed = "flat";
    else if (t >= -PX_VOL_K_SHARP) speed = "slow_dn";
    else speed = "sharp_dn";

    const lv = logAmt[i];
    const mu = muLag[i];
    const sig = sigLag[i];
    if (lv == null || mu == null || sig == null || sig <= 1e-12) continue;
    const z = (lv - mu) / sig;
    let vol: PxVolVolState;
    if (z > PX_VOL_Z_HEAVY) vol = "heavy";
    else if (z >= PX_VOL_Z_SHRINK) vol = "normal";
    else vol = "shrink";

    out[i] = { speed, vol };
  }
  return out;
}

/** One consecutive run of dates satisfying the selected (speed, vol) combo. */
export interface PxVolRun {
  startDate: string;
  endDate: string;
  days: number;
}

/** Merge the per-date states into consecutive RUNS matching the pick
 *  (single-day runs allowed). Returns [] when nothing matches. */
export function pxVolMatchRuns(
  dates: string[],
  states: Array<PxVolDayState | null>,
  speed: PxVolSpeed,
  vol: PxVolVolState,
): PxVolRun[] {
  const runs: PxVolRun[] = [];
  let cur: PxVolRun | null = null;
  for (let i = 0; i < dates.length; i++) {
    const s = states[i];
    if (s != null && s.speed === speed && s.vol === vol) {
      if (cur == null) {
        cur = { startDate: dates[i], endDate: dates[i], days: 1 };
        runs.push(cur);
      } else {
        cur.endDate = dates[i];
        cur.days += 1;
      }
    } else {
      cur = null;
    }
  }
  return runs;
}

/** ECharts markArea rectangle datum (same shape as the trend shading). */
export type PxVolMarkAreaDatum = [
  { xAxis: string; itemStyle: { color: string } },
  { xAxis: string },
];

/** Per-combo trading reading (docs/px_vol_signal_family.md §1.2/§1.3 —
 *  the 15-cell study: fwd5/fwd20 behavior of each speed × amount cell). */
const PX_VOL_READINGS: Record<string, string> = {
  "sharp_up|heavy": "strong growth — attack with volume (momentum favored)",
  "sharp_up|normal": "attack thinning out — weaker growth",
  "sharp_up|shrink": "suspect rally — short-term top divergence, fade within 5d",
  "slow_up|heavy": "slow-bull advance (best over 20d)",
  "slow_up|normal": "≈ noise — inertia mostly gone",
  "slow_up|shrink": "top divergence — reduce within 5d",
  "flat|heavy": "mildly bullish bias (needs amount-trend filter)",
  "flat|normal": "baseline state — no edge",
  "flat|shrink": "no edge (low-amount dip-buying does not hold)",
  "slow_dn|heavy": "distribution / stop-loss — the only sustained negative cell",
  "slow_dn|normal": "grinding decline — slowest recovery",
  "slow_dn|shrink": "early shrink decline only (first ~2 days)",
  "sharp_dn|heavy": "panic washout reversal — strongest bottom signal",
  "sharp_dn|normal": "washout reversal, weaker",
  "sharp_dn|shrink": "extreme panic — mean-reversion edge accumulates",
};

/** One-line reading for the selected (speed, vol) combo. */
export function pxVolReading(speed: PxVolSpeed, vol: PxVolVolState): string {
  return PX_VOL_READINGS[`${speed}|${vol}`] ?? "no reading";
}

/** Convert the matched runs into markArea rects (full-plot-height shades). */
export function pxVolRunsToMarkArea(
  runs: PxVolRun[],
  color: string,
): PxVolMarkAreaDatum[] {
  return runs.map((r) => [
    { xAxis: r.startDate, itemStyle: { color } },
    { xAxis: r.endDate },
  ]);
}
