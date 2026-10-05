/**
 * High/Low Streaks group (the nested period-then-pct buttons) — the
 * anchor-window zone + break-streak mark areas and the per-side carrier
 * series, shared by the pair chart and the Amt Envelope view. Active
 * when BOTH nested buttons are picked (period then pct). DEFAULT view
 * (no chart date clicked): the LATEST date's trailing streakPeriod-row
 * window is filled with its top/bottom pct% price zones — light purple
 * from the window's high_val up to its max high, light yellow from its
 * min low down to low_val. Clicking a chart date ANCHORS the window to
 * that date (the trailing rows before it). The break streak is the
 * WHOLE-WINDOW LONG HORIZONTAL band per side — the in-window DB streaks
 * merged into one span — drawn ONE step darker from the window's
 * constant band edge to the merged extreme, painted after the zone rect
 * so it sits on top where they coincide. All values are raw prices
 * (both display modes keep raw data on the axis). Legend entries toggle
 * each side.
 */
import type { SeriesList } from "../_common";
import { shadeOverlaySeries } from "../_common/shadeOverlaySeries";
import {
  computeStreakBandWindow,
  streakShadeMarkAreas,
  STREAK_HIGH_ACCENT_COLOR,
  STREAK_LOW_ACCENT_COLOR,
  STREAK_HIGH_SHADE_COLOR,
  STREAK_LOW_SHADE_COLOR,
  type LongBandStreak,
  type StreakMarkAreaDatum,
} from "@/shared/charts/streakBands";

/** The per-side streak mark areas (light window zone + the darker
 *  whole-window long streak bands) — null when the combo is off. */
export type StreakShading = { high: StreakMarkAreaDatum[]; low: StreakMarkAreaDatum[] };

export function computeStreakShading(
  rows: Array<{ date: string; high: number | null; low: number | null }>,
  streakPeriod: number | null,
  streakPct: number | null,
  streakAnchorIdx: number | null,
  longStreaks: { high: LongBandStreak[]; low: LongBandStreak[] } | null,
): StreakShading | null {
  if (streakPeriod == null || streakPct == null) return null;
  const win = computeStreakBandWindow(rows, streakPeriod, streakPct, streakAnchorIdx);
  if (win == null) return null;
  return streakShadeMarkAreas(win, longStreaks);
}

/** The legend label — "" when the combo is off. */
export function streakLegendLabel(streakPeriod: number | null, streakPct: number | null): string {
  return streakPeriod != null && streakPct != null
    ? `Streak(${streakPeriod}d·${streakPct}%)`
    : "";
}

/** The two per-side carrier series (rect legend marker + that side's
 *  markArea) — same toggle-per-legend-entry pattern as the other shade
 *  overlays. A side draws only when it has data; the pair chart layers
 *  them at z=1 (over the gap fill), the Amt Envelope at z=0. */
export function appendStreakSeries(
  series: SeriesList,
  streakData: StreakShading | null,
  label: string,
  z: number,
): void {
  if (streakData == null) return;
  const streakSides: Array<{
    hasData: boolean;
    name: string;
    accent: string;
    shade: string;
    data: StreakMarkAreaDatum[];
  }> = [
    {
      hasData: streakData.high.length > 0,
      name: `High ${label}`,
      accent: STREAK_HIGH_ACCENT_COLOR,
      shade: STREAK_HIGH_SHADE_COLOR,
      data: streakData.high,
    },
    {
      hasData: streakData.low.length > 0,
      name: `Low ${label}`,
      accent: STREAK_LOW_ACCENT_COLOR,
      shade: STREAK_LOW_SHADE_COLOR,
      data: streakData.low,
    },
  ];
  for (const s of streakSides) {
    if (!s.hasData) continue;
    series.push(shadeOverlaySeries({
      name: s.name,
      data: s.data,
      accent: s.accent,
      borderColor: s.shade,
      z,
    }));
  }
}
