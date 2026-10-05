/**
 * Roof/floor trendline + rolling-envelope math: the two-point anchor
 * line extrapolated to the clicked date, and the O(n) monotonic-deque
 * rolling max/min within a window.
 */

/**
 * Compute a two-point trendline segment through the (top, 2nd) extrema of
 * the window ending at `clickIdx`, drawn from the EARLIER anchor to the
 * clicked date (the line passes through both anchors — two points
 * determining a line — and stops at the clicked date).
 *
 * Returns null when the line cannot be determined (missing extrema, anchor
 * date not found, coincident anchors, or anchor after the clicked date).
 */
export function trendlineSegment(
  clickIdx: number,
  i1: number,
  v1: number,
  i2: number,
  v2: number,
): Array<[number, number]> | null {
  if (i1 < 0 || i2 < 0 || i1 === i2) return null;
  // Anchors live inside the window ending at clickIdx, so both are <= it.
  if (clickIdx < Math.max(i1, i2)) return null;
  // Linear extrapolation of the anchor line to the clicked date.
  const vc = v1 + ((v2 - v1) * (clickIdx - i1)) / (i2 - i1);
  const s = Math.min(i1, i2);
  const vs = s === i1 ? v1 : v2;
  return [[s, vs], [clickIdx, vc]];
}

/**
 * Rolling window extreme of `values` over the window [max(0, i-w+1), i]
 * (partial windows at the series start, same region rule as the DB
 * anchors). side=1 → rolling max, side=-1 → rolling min. Null/NaN inputs
 * are skipped; a window with no valid value yields null. Monotonic-deque,
 * O(n) — every index enters and leaves the deque at most once.
 */
export function rollingExtremes(
  values: Array<number | null>,
  w: number,
  side: 1 | -1,
): Array<number | null> {
  const n = values.length;
  const out: Array<number | null> = new Array(n).fill(null);
  // Deque holds indices whose values are monotonic toward the head
  // (head = current extreme candidate).
  const dq: number[] = [];
  for (let i = 0; i < n; i++) {
    const v = values[i];
    if (v != null && Number.isFinite(v)) {
      // Pop dominated tail entries (ties keep the earliest index).
      while (
        dq.length > 0
        && (values[dq[dq.length - 1]] as number) * side <= v * side
      ) {
        dq.pop();
      }
      dq.push(i);
    }
    // Drop candidates that fell out of the window.
    const lo = Math.max(0, i - w + 1);
    while (dq.length > 0 && dq[0] < lo) dq.shift();
    out[i] = dq.length > 0 ? values[dq[0]] : null;
  }
  return out;
}
