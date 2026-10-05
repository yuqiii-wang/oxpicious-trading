/**
 * Slope/Curvature overlay scale (the header switch's slope/curv
 * positions) — the RSI-style band offset: one scale SHARED by all the
 * family's lines maps the signed derivative range into the [0,100] band
 * of the fixed 0-400 right axis, zero pinned at DERIV_ZERO_POS (50), so
 * the lines hug the bottom quarter of the plot. Computed from the full
 * series (deterministic — no rescale when the dataZoom window moves,
 * same contract as the RSI overlay's fixed 0-100 range).
 */
export const DERIV_ZERO_POS = 50;

/** Max |value| across the family's lines (finite values only); 1 when
 *  the family is empty or all-null so the scale stays finite. */
export function derivMaxAbsOf(
  lines: Array<{ data: Array<number | null> }>,
): number {
  let maxAbs = 0;
  for (const l of lines) {
    for (const v of l.data) {
      if (v != null && Number.isFinite(v)) {
        maxAbs = Math.max(maxAbs, Math.abs(v));
      }
    }
  }
  if (!(maxAbs > 0) || !Number.isFinite(maxAbs)) maxAbs = 1;
  return maxAbs;
}

/** Raw derivative value → its band position on the 0-400 axis. */
export function toDerivBand(v: number, maxAbs: number): number {
  return DERIV_ZERO_POS + v * (DERIV_ZERO_POS / maxAbs);
}

/** Axis tick position inside the band → back in original units. */
export function derivBandToOriginal(v: number, maxAbs: number): number {
  return ((v - DERIV_ZERO_POS) / DERIV_ZERO_POS) * maxAbs;
}
