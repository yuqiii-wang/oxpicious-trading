/**
 * High/low band-BREAK streak shading — SHARED across analysis pages.
 *
 * This module is the single source of truth for the purple/yellow streak
 * style: the window/detection math, the markArea geometry and the four
 * zone/shade/accent colors all live here, and every page that draws
 * streak shading renders its markArea data through `streakShadeMarkAreas`
 * so the pages cannot drift. Consumers:
 *   - MaSpread (price + amt-envelope charts) — value = adjusted close,
 *     bands of daily high/low prices (analysis.mov_ave_high_low_pct).
 *   - PE & Dividend (valuation streak chart) — value = pe_ma20 /
 *     dividend_yield, both band legs folded onto the same series.
 *
 * Two layers, both price-bounded horizontal bands over date ranges:
 *  - WINDOW ZONES (light): when a (period, pct) combo is selected, the
 *    trailing `period`-row window ending at the anchor date (latest date
 *    by default; clicking a chart date re-anchors it to "before that
 *    date") is filled with its top pct% zone — from the window's high_val
 *    (the (100-pct)-th percentile of daily highs) up to the window's max
 *    high, light purple — and its bottom pct% zone — from the window's min
 *    low down to low_val (the pct-th percentile of daily lows), light
 *    yellow. Computed client-side from the chart rows, mirroring the
 *    backend's percentile_cont over the same trailing window.
 *  - BREAK STREAKS (darker): ONE horizontal band PER STREAK, computed
 *    CLIENT-SIDE against the SAME static band edges the window zones
 *    draw — a day breaks out when its CLOSE (short_value) is above the
 *    anchor window's high_val / below low_val. The DB streak rows (the
 *    *_pct_streaks analysis tables, tested against each month's OWN
 *    moving band) are the monthly analysis record and are deliberately
 *    NOT used for shading: against the one static edge the chart draws,
 *    an old month's breakout often sits BELOW today's edge, which shaded
 *    stretches the visible value never entered. Each streak is a maximal
 *    run of same-side break days with up to STREAK_GAP_TOLERANCE
 *    consecutive in-band days bridged (the DB step's gap semantics),
 *    drawn over its own span (startDate → endDate) across the window's
 *    whole vertical excursion (band edge → the window's max high / min
 *    low), one step darker than the window zones. A streak never spans a
 *    non-break stretch beyond the bridge, so no band appears where the
 *    value line is not inside the zone.
 *
 * A single-series metric (the PE & Dividend fold) participates through
 * the same code path by passing the value as BOTH the high and the low
 * leg — the percentile windows and the close-based breakout then reduce
 * exactly to the single-series semantics.
 */
/** Max consecutive in-band days bridged inside ONE break streak — mirrors
 *  the DB step's HIGH_LOW_PCT_GAP_TOLERANCE (5 trading rows). */
export const STREAK_GAP_TOLERANCE = 5;

/** ONE break-streak span: a maximal run of same-side break days (close
 *  above the window's high_val / below low_val), bridging at most
 *  STREAK_GAP_TOLERANCE consecutive in-band days. */
export interface LongBandStreak {
  side: "high" | "low";
  /** The streak's first break day (the span's left edge). */
  startDate: string;
  /** The streak's last break day (the span's right edge). */
  endDate: string;
  /** Chart rows in [startDate, endDate] (bridged in-band rows included). */
  days: number;
  /** Max high over the span (high side) / min low (low side) — shown in
   *  the panel caption (peak/trough); NOT the band edge — bands span to
   *  the window's winHigh/winLow. */
  extreme: number;
}

/** The break-streak detection input row: the chart's own pair rows carry
 *  short_value (= adjusted close on the price pairs — the price line the
 *  chart draws) plus the intraday high/low. */
export interface BreakStreakRow {
  date: string;
  short_value?: number | null;
  high: number | null;
  low: number | null;
}

/** Classify one row against the window's static edges: +1 close above
 *  high_val, -1 close below low_val, 0 in-band. Falls back to the
 *  intraday extreme when close is missing. */
function breakSide(r: BreakStreakRow, win: StreakBandWindow): -1 | 0 | 1 {
  const c = r.short_value;
  if (c != null && Number.isFinite(c)) {
    if (c > win.highVal) return 1;
    if (c < win.lowVal) return -1;
    return 0;
  }
  if (r.high != null && Number.isFinite(r.high) && r.high > win.highVal) return 1;
  if (r.low != null && Number.isFinite(r.low) && r.low < win.lowVal) return -1;
  return 0;
}

/** Detect the break streaks of the anchor window against its OWN static
 *  band edges (the same edges the light zones draw): one pass over the
 *  window's rows, bridging ≤ STREAK_GAP_TOLERANCE consecutive in-band
 *  days inside a same-side streak; a side switch or a longer gap closes
 *  it. Because every streak day is tested against the very edge the
 *  chart shades, a band can only ever appear where the price line is
 *  inside the drawn zone. */
export function computeBreakStreaks(
  rows: BreakStreakRow[],
  win: StreakBandWindow,
): { high: LongBandStreak[]; low: LongBandStreak[] } {
  const inWin = rows.filter(
    (r) => r.date >= win.startDate && r.date <= win.endDate,
  );
  const out: LongBandStreak[] = [];
  let cur: { side: -1 | 1; start: number; lastBreak: number; gap: number } | null =
    null;
  const closeStreak = (upto: number) => {
    if (!cur) return;
    const seg = inWin.slice(cur.start, upto + 1);
    let extreme: number | null = null;
    for (const r of seg) {
      const v = cur.side === 1 ? r.high : r.low;
      if (v == null || !Number.isFinite(v)) continue;
      extreme =
        extreme == null
          ? v
          : cur.side === 1
            ? Math.max(extreme, v)
            : Math.min(extreme, v);
    }
    if (extreme != null) {
      out.push({
        side: cur.side === 1 ? "high" : "low",
        startDate: inWin[cur.start].date,
        endDate: inWin[cur.lastBreak].date,
        days: cur.lastBreak - cur.start + 1,
        extreme,
      });
    }
    cur = null;
  };
  for (let i = 0; i < inWin.length; i++) {
    const s = breakSide(inWin[i], win);
    if (s === 0) {
      if (cur) {
        cur.gap++;
        if (cur.gap > STREAK_GAP_TOLERANCE) closeStreak(cur.lastBreak);
      }
    } else if (cur && cur.side === s) {
      cur.lastBreak = i;
      cur.gap = 0;
    } else {
      closeStreak(cur ? cur.lastBreak : -1);
      cur = { side: s, start: i, lastBreak: i, gap: 0 };
    }
  }
  if (cur) closeStreak(cur.lastBreak);
  return {
    high: out.filter((s) => s.side === "high"),
    low: out.filter((s) => s.side === "low"),
  };
}

/** Light purple window zone (top pct% of the anchor window, above
 *  high_val). */
export const STREAK_HIGH_WIN_COLOR = "rgba(171, 71, 188, 0.12)";
/** Darker purple for the long high-side break-streak band. */
export const STREAK_HIGH_SHADE_COLOR = "rgba(142, 36, 170, 0.32)";
/** Deep purple for the High Streak legend marker + button accents. */
export const STREAK_HIGH_ACCENT_COLOR = "#AB47BC";
/** Light yellow window zone (bottom pct% of the anchor window, below
 *  low_val). */
export const STREAK_LOW_WIN_COLOR = "rgba(255, 241, 118, 0.20)";
/** Darker amber for the long low-side break-streak band. */
export const STREAK_LOW_SHADE_COLOR = "rgba(251, 192, 45, 0.42)";
/** Deep yellow for the Low Streak legend marker + button accents. */
export const STREAK_LOW_ACCENT_COLOR = "#F9A825";

/** One markArea rectangle (price-bounded): the first corner carries the
 *  band edge (yAxis), the second the streak/window extreme. */
export type StreakMarkAreaDatum = [
  { xAxis: string; yAxis: number; itemStyle: { color: string } },
  { xAxis: string; yAxis: number },
];

/** The anchor-date band window: the trailing `period` price rows with
 *  their percentile band edges and actual extremes (the window zones'
 *  vertical bounds). */
export interface StreakBandWindow {
  startDate: string;
  endDate: string;
  /** (100-pct)-th percentile of the window's daily highs (band top). */
  highVal: number;
  /** pct-th percentile of the window's daily lows (band bottom). */
  lowVal: number;
  /** Max high in the window (top zone's upper bound). */
  winHigh: number;
  /** Min low in the window (bottom zone's lower bound). */
  winLow: number;
}

/** percentile_cont-style linear-interpolated percentile of a sorted
 *  ascending array. */
function percentileCont(sorted: number[], p: number): number {
  if (sorted.length === 0) return NaN;
  const idx = (p / 100) * (sorted.length - 1);
  const lo = Math.floor(idx);
  const hi = Math.ceil(idx);
  return lo === hi
    ? sorted[lo]
    : sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
}

/** Compute the anchor-date band window from the chart's own rows (date +
 *  high/low per row): the trailing `period` rows ending at anchorIdx
 *  (latest row when null/invalid, clamped at the series start), the band
 *  edges as percentiles of the window's highs/lows. Null when the window
 *  has no usable price rows. */
export function computeStreakBandWindow(
  rows: Array<{ date: string; high: number | null; low: number | null }>,
  period: number,
  pct: number,
  anchorIdx: number | null,
): StreakBandWindow | null {
  const n = rows.length;
  if (n === 0) return null;
  const end =
    anchorIdx != null && anchorIdx >= 0 && anchorIdx < n ? anchorIdx : n - 1;
  const start = Math.max(0, end - period + 1);
  const highs: number[] = [];
  const lows: number[] = [];
  for (let i = start; i <= end; i++) {
    const h = rows[i].high;
    const l = rows[i].low;
    if (h != null && Number.isFinite(h)) highs.push(h);
    if (l != null && Number.isFinite(l)) lows.push(l);
  }
  if (highs.length === 0 || lows.length === 0) return null;
  highs.sort((a, b) => a - b);
  lows.sort((a, b) => a - b);
  return {
    startDate: rows[start].date,
    endDate: rows[end].date,
    highVal: percentileCont(highs, 100 - pct),
    lowVal: percentileCont(lows, pct),
    winHigh: highs[highs.length - 1],
    winLow: lows[0],
  };
}

/** The anchor window's two zone rectangles (window-wide, light): the top
 *  zone from high_val up to the window's max high, the bottom zone from
 *  the window's min low down to low_val. */
export function streakWindowToMarkArea(win: StreakBandWindow): {
  high: StreakMarkAreaDatum;
  low: StreakMarkAreaDatum;
} {
  return {
    high: [
      {
        xAxis: win.startDate,
        yAxis: win.highVal,
        itemStyle: { color: STREAK_HIGH_WIN_COLOR },
      },
      { xAxis: win.endDate, yAxis: win.winHigh },
    ],
    low: [
      {
        xAxis: win.startDate,
        yAxis: win.winLow,
        itemStyle: { color: STREAK_LOW_WIN_COLOR },
      },
      { xAxis: win.endDate, yAxis: win.lowVal },
    ],
  };
}

/** Convert ONE break streak into its ECharts markArea datum — a darker
 *  horizontal band from the window band edge to the WHOLE WINDOW's top
 *  price (winHigh, high side) / bottom price (winLow, low side) — the
 *  same vertical extent as the light window zones, NOT the streak's own
 *  local extreme — over the streak's own price-date span
 *  [startDate, endDate]. */
export function longStreakToMarkArea(
  streak: LongBandStreak,
  win: StreakBandWindow,
): StreakMarkAreaDatum {
  return streak.side === "high"
    ? [
        {
          xAxis: streak.startDate,
          yAxis: win.highVal,
          itemStyle: { color: STREAK_HIGH_SHADE_COLOR },
        },
        { xAxis: streak.endDate, yAxis: win.winHigh },
      ]
    : [
        {
          xAxis: streak.startDate,
          yAxis: win.winLow,
          itemStyle: { color: STREAK_LOW_SHADE_COLOR },
        },
        { xAxis: streak.endDate, yAxis: win.lowVal },
      ];
}

/** The COMPLETE markArea data for ONE page's streak shading: per side,
 *  the light anchor-window zone rectangle followed by one darker band per
 *  detected streak. This is the canonical purple/yellow assembly — every
 *  consumer renders its markArea through this helper (MaSpread pair +
 *  amt-envelope charts and the PE & Dividend streak chart pass the same
 *  window + client-detected streaks they computed via
 *  computeStreakBandWindow / computeBreakStreaks above), so the two
 *  pages' streak styling cannot drift. */
export function streakShadeMarkAreas(
  win: StreakBandWindow,
  streaks: { high?: LongBandStreak[]; low?: LongBandStreak[] } | null,
): { high: StreakMarkAreaDatum[]; low: StreakMarkAreaDatum[] } {
  const zones = streakWindowToMarkArea(win);
  return {
    high: [
      zones.high,
      ...(streaks?.high ?? []).map((s) => longStreakToMarkArea(s, win)),
    ],
    low: [
      zones.low,
      ...(streaks?.low ?? []).map((s) => longStreakToMarkArea(s, win)),
    ],
  };
}
