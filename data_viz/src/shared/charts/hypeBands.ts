/**
 * Market-hype shading helpers — shared across every chart that can draw
 * hype EPISODE shading (the shared StockOhlcChart used by CodeTrendChart,
 * the MA-Spread charts, …).
 *
 * Source: stats.mov_ave_market_hypes — one EPISODE row per CONCATENATED
 * hype episode per check-in window: a span of trading dates around a
 * maximal run of consecutive hyped dates, extended through the
 * surrounding check-in evidence (startDate / endDate bracket the extended
 * span) and bucketed by span into [minCheckinPeriod, next window). This
 * module turns episode lists into ECharts markArea data — a light purple
 * shade drawn over each hyped period, spanning the full plot height.
 */
import type { MovAveSpreadHypeEpisode, MovAveSpreadHypeEpisodes } from "@shared/types";

/** Light purple used to shade hyped date periods on the chart. */
export const HYPE_SHADE_COLOR = "rgba(149, 117, 205, 0.16)";
/** Deeper purple for the Hyped legend marker and toggle accents. */
export const HYPE_ACCENT_COLOR = "#7E57C2";

/** One markArea rectangle: [{xAxis: startDate, itemStyle}, {xAxis: endDate}]. */
export type HypeMarkAreaDatum = [
  { xAxis: string; itemStyle: { color: string } },
  { xAxis: string },
];

/** Convert one check-in window's hype episodes into ECharts markArea data
 *  (light purple rectangles spanning the full plot height). Episodes are
 *  date spans, so no index-alignment with the chart rows is required.
 *  Empty input (window absent / never hyped) yields no shading. */
export function hypeEpisodesToMarkArea(
  episodes: MovAveSpreadHypeEpisode[] | null | undefined,
): HypeMarkAreaDatum[] {
  if (!episodes || episodes.length === 0) return [];
  return episodes.map((ep) => [
    { xAxis: ep.startDate, itemStyle: { color: HYPE_SHADE_COLOR } },
    { xAxis: ep.endDate },
  ]);
}

/** Union-merge the episodes of ALL check-in windows into one sorted list of
 *  DISJOINT date spans — the single Hypes toggle on the shared code trend
 *  shades every hyped date once (overlapping windows' spans would otherwise
 *  stack the translucent shade darker where they coincide). The input map's
 *  episode lists are each ascending by startDate; spans that touch (next
 *  start == current end) are fused into one. */
export function mergeHypeEpisodesAllWindows(
  episodes: MovAveSpreadHypeEpisodes | null | undefined,
): MovAveSpreadHypeEpisode[] {
  if (!episodes) return [];
  const all = Object.values(episodes).flat();
  if (all.length === 0) return [];
  const sorted = [...all].sort(
    (a, b) =>
      (a.startDate < b.startDate ? -1 : a.startDate > b.startDate ? 1 : 0),
  );
  const merged: MovAveSpreadHypeEpisode[] = [];
  for (const ep of sorted) {
    const last = merged[merged.length - 1];
    if (last && ep.startDate <= last.endDate) {
      // Overlapping or touching — extend the current span (fuse the
      // bucket diagnostics: keep the max hypeDays seen in the union).
      if (ep.endDate > last.endDate) last.endDate = ep.endDate;
      last.hypeDays = Math.max(last.hypeDays, ep.hypeDays);
    } else {
      merged.push({ ...ep });
    }
  }
  return merged;
}
