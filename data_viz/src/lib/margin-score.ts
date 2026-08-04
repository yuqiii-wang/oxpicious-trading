/**
 * Margin score computation — port of load_etf_margin() in plot_szse_sse_etf_and_margin.py.
 *
 * RZ (融资 cash borrow) is always positive: shift by min so the lowest valid
 * value sits at 0, then percentile-clip 0.5–99.5.
 * RQ (融券 sec borrow) is always negative: flip sign, shift by max so the
 * highest valid value sits at 0, then percentile-clip 0.5–99.5.
 *
 * Both are 5-day smoothed before shifting.
 *
 * Missing data handling: rows whose raw balance is null OR zero are treated as
 * "no margin data" — their score is forced to null so the chart breaks the
 * line at those positions instead of interpolating across them. Smoothing
 * still skips null inputs, but the output is explicitly nulled at missing
 * positions so no interpolation (visual or via the rolling mean) occurs.
 */
import { percentileClip, rollingMean } from "./series";

export interface MarginScoreRow {
  date: string;
  rz_balance: number | null;
  rq_balance_amt: number | null;
  rz_score: number | null;
  rq_score: number | null;
}

export interface MarginRowInput {
  date: string;
  rz_balance: number | null;
  rq_balance_amt: number | null;
}

/**
 * Compute rz_score (always >= 0) and rq_score (always <= 0) for a series of
 * margin rows. Returns null scores if both rz and rq are entirely zero/null.
 * Individual rows with null/zero balances produce null scores (no data) so
 * the chart does not interpolate across them.
 */
export function computeMarginScores(rows: MarginRowInput[]): MarginScoreRow[] {
  if (rows.length === 0) return [];

  // Track which positions have missing margin data (null or zero). These
  // positions will be forced to null in the output so the chart line breaks
  // rather than interpolating.
  const rzMissing: boolean[] = rows.map(
    (r) => r.rz_balance == null || r.rz_balance === 0,
  );
  const rqMissing: boolean[] = rows.map(
    (r) => r.rq_balance_amt == null || r.rq_balance_amt === 0,
  );

  // Build raw arrays: missing positions become null so smoothing skips them.
  const rzRaw: Array<number | null> = rows.map((r) =>
    r.rz_balance == null || r.rz_balance === 0 ? null : r.rz_balance,
  );
  const rqRaw: Array<number | null> = rows.map((r) =>
    r.rq_balance_amt == null || r.rq_balance_amt === 0 ? null : r.rq_balance_amt,
  );

  // If no margin data at all, return null scores
  const rzSum = rzRaw.reduce((a, b) => a + (Number.isFinite(b as number) ? (b as number) : 0), 0);
  const rqSum = rqRaw.reduce((a, b) => a + (Number.isFinite(b as number) ? (b as number) : 0), 0);
  if (rzSum === 0 && rqSum === 0) {
    return rows.map((r) => ({
      date: r.date,
      rz_balance: r.rz_balance,
      rq_balance_amt: r.rq_balance_amt,
      rz_score: null,
      rq_score: null,
    }));
  }
  // 5-day smoothing
  const rzSm = rollingMean(rzRaw, 5);
  const rqSm = rollingMean(rqRaw, 5);
  // RZ: shift so min valid value = 0
  const rzFinite = rzSm.filter((v): v is number => v != null && Number.isFinite(v));
  const rzMin = rzFinite.length > 0 ? Math.min(...rzFinite) : 0;
  const rzPlot = rzSm.map((v) => {
    if (v == null || !Number.isFinite(v)) return null;
    const shifted = v - rzMin;
    return Math.max(0, shifted);
  });
  // RQ: flip sign, shift so max valid value = 0
  const rqNeg = rqSm.map((v) => (v == null || !Number.isFinite(v) ? null : -v));
  const rqFinite = rqNeg.filter((v): v is number => v != null && Number.isFinite(v));
  const rqMax = rqFinite.length > 0 ? Math.max(...rqFinite) : 0;
  const rqPlot = rqNeg.map((v) => {
    if (v == null || !Number.isFinite(v)) return null;
    const shifted = v - rqMax;
    return Math.min(0, shifted);
  });
  // Percentile clip
  const rzClipped = percentileClip(rzPlot, 0.5, 99.5);
  const rqClipped = percentileClip(rqPlot, 0.5, 99.5);
  return rows.map((r, i) => ({
    date: r.date,
    rz_balance: r.rz_balance,
    rq_balance_amt: r.rq_balance_amt,
    // Force null at missing positions so the chart breaks the line and does
    // not interpolate across null/zero margin samples.
    rz_score: rzMissing[i] ? null : rzClipped[i],
    rq_score: rqMissing[i] ? null : rqClipped[i],
  }));
}
