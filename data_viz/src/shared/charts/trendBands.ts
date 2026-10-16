/**
 * TREND shading helpers — the signal rules tier's per-(code, date)
 * trend classification drawn as span shading on the shared CodeTrendChart
 * (the regimeBands precedent: translucent full-height markArea bands per
 * contiguous same-trend span).
 *
 * Source: GET /api/live-data/trading-signals/trend-spans — contiguous
 * same-trend runs computed over analysis.trend_days (the store
 * analysis.trends.trend_state.attach_trend fills on every
 * analysis_signals run: top/bottom the overextended extremes of the
 * price-vs-MA255 gap vs its own trailing q90/q10, rise/fall the
 * Wilder-RSI14 RE-ENTRY episodes vs the code's own trailing q75/q25
 * band — the 2026-10-15 replacement of the delayed price-vs-MA60
 * states, unknown the unclassified bucket).
 *
 * The shade depth is GRADED BY TREND CLASS: top/bottom (the
 * overextended extremes — the trend-split cells' strongest populations)
 * shade deepest; rise/fall (the momentum-episode directional states)
 * shade one step lighter; unknown is the unshaded background (the calm
 * precedent).
 */
import type { TrendSpan } from "@/lib/api-client";

/** Translucent full-height band color per trend — graded by class: the
 *  extremes (top red / bottom green) deepest, the directionals (rise
 *  blue / fall amber) lighter. unknown never shades (the background,
 *  the regime calm precedent). */
export const TREND_SHADE_COLORS: Record<string, string> = {
  top: "rgba(229, 57, 53, 0.14)",
  bottom: "rgba(67, 160, 71, 0.14)",
  rise: "rgba(33, 150, 243, 0.09)",
  fall: "rgba(255, 167, 38, 0.09)",
};

/** Solid accent per trend — legend markers / chips (the palette hues
 *  the SignalRulesPanel trend chips already speak: error red /
 *  success green / info blue / warning amber). "all" is the rules
 *  tier's POOLED stamp (never a day state); unknown the grey
 *  unclassified bucket. */
export const TREND_ACCENT_COLORS: Record<string, string> = {
  top: "#E53935",
  bottom: "#43A047",
  rise: "#2196F3",
  fall: "#FFA726",
  all: "#9E9E9E",
  unknown: "#9E9E9E",
};

/** Display label per trend. */
export const TREND_LABELS: Record<string, string> = {
  top: "Top",
  bottom: "Bottom",
  rise: "Rise",
  fall: "Fall",
  all: "All",
  unknown: "Unknown",
};

/** The trends that get SHADED when trend display is on (unknown is the
 *  background — shading it would paint the whole plot). */
export const SHADED_TRENDS: readonly string[] = [
  "top", "bottom", "rise", "fall",
] as const;

/** One markArea rectangle: [{xAxis: startDate, itemStyle}, {xAxis: endDate}]. */
export type TrendMarkAreaDatum = [
  { xAxis: string; itemStyle: { color: string } },
  { xAxis: string },
];

/** Convert one trend's spans into ECharts markArea data (translucent
 *  rectangles spanning the full plot height). Spans are date ranges, so
 *  no index-alignment with the chart rows is required. Empty input
 *  yields no shading. */
export function trendSpansToMarkArea(
  spans: TrendSpan[] | null | undefined,
  color: string,
): TrendMarkAreaDatum[] {
  if (!spans || spans.length === 0) return [];
  return spans.map((sp) => [
    { xAxis: sp.start_date, itemStyle: { color } },
    { xAxis: sp.end_date },
  ]);
}

/** Accent color for an arbitrary trend string (unknown → undefined) —
 *  for the table cells / tick menus that look up raw string keys. */
export function trendAccentColor(v: string): string | undefined {
  return TREND_ACCENT_COLORS[v];
}
