/**
 * dateTicks — shared x-axis date-tick helpers for the time-slider chart
 * families (macro / debt base options): the tick-position filter across a
 * month / quarter / year ladder plus the width- and zoom-aware granularity
 * picker that guards against label overlap.
 *
 * The tick filter mirrors the former format_date_axis_quarterly in
 * _plot_commons.py (the matplotlib plot commons the earlier chart helpers
 * were ported from).
 */

export type DateTickGranularity = "month" | "quarter" | "year";

/** Granularity ladder, densest first — the picker walks it and stops at the
 *  densest granularity whose labels fit the available width. */
const LADDER: DateTickGranularity[] = ["month", "quarter", "year"];

/** Left + right grid insets of the macro/debt base options
 *  (`commonGrid({ left: 64, right: 64, ... })`) — px inside the chart body
 *  that the plot area never occupies, so labels can't use them. */
const GRID_X_INSETS = 128;

/** Estimated minimum px per date label — "2026-01-02" at fontSize 10 plus
 *  the axis-label margin. Below this density the labels overlap. */
const MIN_TICK_PX = 70;

/** Visible dataZoom window in percentages (0–100) of the full date axis. */
export interface ZoomWindowPct {
  start: number;
  end: number;
}

/**
 * Compute the tick positions at the given granularity: the first business
 * date of each month / quarter / year. Dates are YYYY-MM-DD strings on a
 * category axis, so a tick is simply the first date whose bucket differs
 * from the previous entry's.
 */
export function dateTickFilter(
  dates: string[],
  granularity: DateTickGranularity,
): Set<string> {
  const out = new Set<string>();
  let lastBucket = -1;
  for (const d of dates) {
    const bucket = granularity === "month"
      ? Number(d.slice(0, 4)) * 12 + (Number(d.slice(5, 7)) - 1)
      : granularity === "quarter"
        ? Math.floor((Number(d.slice(5, 7)) - 1) / 3)
        : Number(d.slice(0, 4));
    if (bucket !== lastBucket) {
      out.add(d);
      lastBucket = bucket;
    }
  }
  return out;
}

/**
 * Pick the tick granularity for the visible window of a chart body
 * `chartWidthPx` wide: the densest granularity (month → quarter → year)
 * whose labels fit the available width, so a zoomed-in time-slider window
 * re-densifies (year → quarter → month) while a wide or full view stays
 * coarse enough that the labels never overlap. Without a measured width the
 * quarter default stands (the pre-width behaviour); without a zoom window
 * the full date span is judged.
 */
export function pickDateTickGranularity(
  dates: string[],
  chartWidthPx: number | undefined,
  zoom?: ZoomWindowPct,
): DateTickGranularity {
  if (!chartWidthPx || chartWidthPx <= 0) return "quarter";
  const usable = chartWidthPx - GRID_X_INSETS;
  if (usable <= 0) return "year";
  const n = dates.length;
  if (n === 0) return "quarter";
  let i0 = 0;
  let i1 = n - 1;
  if (zoom) {
    i0 = Math.max(0, Math.min(n - 1, Math.round((zoom.start / 100) * (n - 1))));
    i1 = Math.max(i0, Math.min(n - 1, Math.round((zoom.end / 100) * (n - 1))));
  }
  const windowDates = dates.slice(i0, i1 + 1);
  for (const g of LADDER) {
    if (dateTickFilter(windowDates, g).size * MIN_TICK_PX <= usable) return g;
  }
  return "year";
}
