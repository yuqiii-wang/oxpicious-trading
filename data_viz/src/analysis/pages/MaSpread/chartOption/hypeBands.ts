/**
 * Market-hype shading for the MA-Spread charts (pair chart + amt envelope).
 *
 * Source: analysis.mov_ave_market_hypes — one EPISODE row per CONCATENATED
 * hype episode per check-in window: a span of trading dates around a
 * maximal run of consecutive hyped dates, extended through the surrounding
 * check-in evidence (startDate / endDate bracket the extended span) and
 * bucketed by span into [minCheckinPeriod, next window). The backend ships
 * the episodes grouped by window (MovAveSpreadHypeEpisodes); this module
 * turns the selected window's episode list into ECharts markArea data — a
 * light purple shade drawn over each hyped period, spanning the full plot
 * height.
 */
import type { MovAveSpreadHypeEpisode } from "@shared/types";

/** Light purple used to shade hyped date periods on the chart. */
export const HYPE_SHADE_COLOR = "rgba(149, 117, 205, 0.16)";
/** Deeper purple for the Hyped(Wd) legend marker and button accents. */
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
