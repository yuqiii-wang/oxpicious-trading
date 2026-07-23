/**
 * Numeric helpers ported from _plot_commons.py:
 *   - safeMa: rolling mean with graceful fallback for short series
 *   - retPct: rebase series to percentage change from first value (start = 0%)
 *   - breakArraysAtGaps: insert nulls at date gaps > N days so ECharts lines
 *     don't interpolate across weekends/holidays
 */

/**
 * Rebase a series of numbers to percentage change from the first value.
 * Returns null array if input is empty or first value is 0/invalid.
 * Mirrors ret_pct() in _plot_commons.py.
 */
export function retPct(values: Array<number | null | undefined>): Array<number | null> {
  if (!values.length) return [];
  const v0 = values[0];
  if (v0 == null || !Number.isFinite(v0) || Math.abs(v0) < 1e-9) {
    return values.map(() => null);
  }
  return values.map((v) =>
    v == null || !Number.isFinite(v) ? null : (v / v0 - 1.0) * 100.0,
  );
}

/**
 * Rolling mean with `min_periods = max(3, w/4)`. Returns null array if input
 * is shorter than `w`. Mirrors safe_ma() in _plot_commons.py.
 */
export function safeMa(
  values: Array<number | null | undefined>,
  w: number,
): Array<number | null> {
  const n = values.length;
  if (n === 0 || n < w) return values.map(() => null);
  const minPeriods = Math.max(3, Math.floor(w / 4));
  const out: Array<number | null> = new Array(n).fill(null);
  let runningSum = 0;
  let runningCount = 0;
  // Sliding window
  for (let i = 0; i < n; i++) {
    const v = values[i];
    if (v != null && Number.isFinite(v)) {
      runningSum += v;
      runningCount += 1;
    }
    if (i >= w) {
      const oldV = values[i - w];
      if (oldV != null && Number.isFinite(oldV)) {
        runningSum -= oldV;
        runningCount -= 1;
      }
    }
    if (i >= w - 1 && runningCount >= minPeriods) {
      out[i] = runningSum / runningCount;
    }
  }
  return out;
}

/**
 * Insert null into parallel arrays at date gaps > gapDays so chart lines break.
 * Mirrors break_arrays_at_gaps() in _plot_commons.py.
 *
 * @param dates ISO date strings "YYYY-MM-DD"
 * @param arrays parallel value arrays (numbers or null)
 * @returns tuple of [dates_with_inserts, ...arrays_with_inserts]
 */
export function breakArraysAtGaps(
  dates: string[],
  arrays: Array<Array<number | null>>,
  gapDays = 4,
): { dates: string[]; arrays: Array<Array<number | null>> } {
  const n = dates.length;
  if (n <= 2) {
    return { dates: [...dates], arrays: arrays.map((a) => [...a]) };
  }
  const insertIdx: number[] = [];
  for (let i = 1; i < n; i++) {
    const t0 = new Date(dates[i - 1] + "T00:00:00Z").getTime();
    const t1 = new Date(dates[i] + "T00:00:00Z").getTime();
    const diffDays = (t1 - t0) / 86400000;
    if (diffDays > gapDays) insertIdx.push(i);
  }
  if (insertIdx.length === 0) {
    return { dates: [...dates], arrays: arrays.map((a) => [...a]) };
  }
  const outDates: string[] = [];
  const outArrays: number[][] = arrays.map(() => []);
  let cursor = 0;
  for (const ins of insertIdx) {
    for (let i = cursor; i < ins; i++) {
      outDates.push(dates[i]);
      arrays.forEach((arr, j) => outArrays[j].push(arr[i] ?? NaN));
    }
    // Insert null marker: same date repeated, values = NaN
    outDates.push(dates[ins]);
    arrays.forEach((_arr, j) => outArrays[j].push(NaN));
    cursor = ins;
  }
  // Push remaining tail
  for (let i = cursor; i < n; i++) {
    outDates.push(dates[i]);
    arrays.forEach((arr, j) => outArrays[j].push(arr[i] ?? NaN));
  }
  return { dates: outDates, arrays: outArrays };
}

/**
 * Compute a percentile value from a numeric array (ignoring NaN/null).
 */
export function quantile(
  values: Array<number | null>,
  q: number,
): number {
  const nums = values.filter((v): v is number => v != null && Number.isFinite(v));
  if (nums.length === 0) return 0;
  nums.sort((a, b) => a - b);
  const pos = (nums.length - 1) * q;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  if (lo === hi) return nums[lo];
  const w = pos - lo;
  return nums[lo] * (1 - w) + nums[hi] * w;
}

/**
 * Clip values to [lo, hi] quantile range. NaN values preserved.
 */
export function percentileClip(
  values: Array<number | null>,
  loPct = 0.5,
  hiPct = 99.5,
): Array<number | null> {
  const lo = quantile(values, loPct / 100);
  const hi = quantile(values, hiPct / 100);
  return values.map((v) => {
    if (v == null || !Number.isFinite(v)) return v;
    return Math.min(Math.max(v, lo), hi);
  });
}

/**
 * Compute N-day rolling mean (forward fill of nulls first, optional).
 */
export function rollingMean(
  values: Array<number | null>,
  w: number,
): Array<number | null> {
  return safeMa(values, w);
}

/**
 * Format a number with up to `digits` decimals, returning "—" for null.
 * Trailing zeros are stripped so "1.500" becomes "1.5" and "12.000" becomes "12".
 */
export function fmtNum(v: number | null | undefined, digits = 3): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toFixed(digits).replace(/\.?0+$/, "");
}

/**
 * Format a value that is already in percent (e.g., 12.345 → "12.345%").
 * Uses at most `digits` decimals (trailing zeros stripped).
 */
export function fmtPct(v: number | null | undefined, digits = 3): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return fmtNum(v, digits) + "%";
}

/**
 * Format a large raw number as "X mil" (1 mil = 1,000,000).
 * Uses at most `digits` decimals. Returns "—" for null/NaN.
 */
export function fmtMil(v: number | null | undefined, digits = 3): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return fmtNum(v / 1e6, digits) + " mil";
}

/**
 * Format a 万-unit volume (1 万 = 10,000) as "X mil" (1 mil = 100 万).
 * Uses at most `digits` decimals. Returns "—" for null/NaN.
 */
export function fmtMilFromWan(v: number | null | undefined, digits = 3): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return fmtNum(v / 100, digits) + " mil";
}

/**
 * Format a yuan value in 亿元 unit (1e8).
 */
export function fmtYi(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return (v / 1e8).toFixed(digits) + "亿";
}

/**
 * Format a volume value (in 万 unit) — auto-scale to 亿 when large.
 */
export function fmtWan(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a >= 10000) return (v / 10000).toFixed(1) + "亿";
  if (a >= 100) return v.toFixed(0) + "万";
  return v.toFixed(1) + "万";
}

/**
 * Add days to an ISO date string.
 */
export function addDays(dateStr: string, days: number): string {
  const d = new Date(dateStr + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}
