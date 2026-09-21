/**
 * Market-regime shading helpers — shared across every chart that can
 * draw REGIME span shading (the shared StockOhlcChart used by
 * CodeTrendChart, the MA-Spread charts, …).
 *
 * Source: GET /api/analysis/market-regimes — contiguous same-regime
 * spans from the stats.market_regime_spans table (materialized by
 * builds.market_regimes over the daily stats.market_regimes registry).
 * This module turns per-regime span lists into
 * ECharts markArea data — a translucent band drawn over each span,
 * spanning the full plot height, colored per regime (hot keeps the
 * retired hype-episode purple).
 *
 * Replaces the retired hypeBands.ts (mov_ave_market_hypes episode
 * shading).
 */
import type {
  MarketRegime,
  MarketRegimeSpan,
  MarketRegimeSpans,
} from "@shared/types";

/** Translucent full-height band color per regime — each derived from
 *  that regime's solid accent (the palette the forecast tables color
 *  their regime cells with). calm's shade was near-invisible while it
 *  was only the background; now that the MA-Spread regime row offers a
 *  calm pick it shades at the same depth as the others. hot keeps the
 *  retired HYPE_SHADE_COLOR purple. */
export const REGIME_SHADE_COLORS: Record<MarketRegime, string> = {
  calm: "rgba(158, 158, 158, 0.12)",
  hot: "rgba(149, 117, 205, 0.16)",
  panic: "rgba(229, 57, 53, 0.12)",
  quiet: "rgba(33, 150, 243, 0.10)",
};

/** Solid accent per regime — legend markers and toggle styling. hot
 *  keeps the retired HYPE_ACCENT_COLOR purple. */
export const REGIME_ACCENT_COLORS: Record<MarketRegime, string> = {
  calm: "#9E9E9E",
  hot: "#7E57C2",
  panic: "#E53935",
  quiet: "#2196F3",
};

/** Display label per regime (series names / chips). */
export const REGIME_LABELS: Record<MarketRegime, string> = {
  calm: "Calm",
  hot: "Hot",
  panic: "Panic",
  quiet: "Quiet",
};

/** The regimes that get SHADED when regime display is on (calm is the
 *  background — shading it would paint the whole plot). The MA-Spread
 *  regime row uses ALL_REGIMES instead: there the calm pick explicitly
 *  opts into the grey background shade. */
export const SHADED_REGIMES: readonly MarketRegime[] = [
  "hot", "panic", "quiet",
] as const;

/** Every regime — the full chip/button set (calm included). */
export const ALL_REGIMES: readonly MarketRegime[] = [
  "calm", "hot", "panic", "quiet",
] as const;

/** Accent color for an arbitrary regime string (unknown → undefined) —
 *  for the table cells / tick menus that look up raw string keys. */
export function regimeAccentColor(v: string): string | undefined {
  return (REGIME_ACCENT_COLORS as Record<string, string>)[v];
}

/** One markArea rectangle: [{xAxis: startDate, itemStyle}, {xAxis: endDate}]. */
export type RegimeMarkAreaDatum = [
  { xAxis: string; itemStyle: { color: string } },
  { xAxis: string },
];

/** Convert one regime's spans into ECharts markArea data (translucent
 *  rectangles spanning the full plot height). Spans are date ranges, so
 *  no index-alignment with the chart rows is required. Empty input
 *  yields no shading. */
export function regimeSpansToMarkArea(
  spans: MarketRegimeSpan[] | null | undefined,
  color: string,
): RegimeMarkAreaDatum[] {
  if (!spans || spans.length === 0) return [];
  return spans.map((sp) => [
    { xAxis: sp.startDate, itemStyle: { color } },
    { xAxis: sp.endDate },
  ]);
}

/** Flatten the endpoint's per-regime span map into the SHADED regimes'
 *  span lists (calm dropped — the background), ascending by startDate
 *  within each regime. The map's lists are each already ascending. */
export function shadedRegimeSpans(
  spans: Partial<MarketRegimeSpans> | null | undefined,
): Partial<MarketRegimeSpans> {
  if (!spans) return {};
  const out: Partial<MarketRegimeSpans> = {};
  for (const r of SHADED_REGIMES) {
    if (spans[r] && spans[r].length > 0) out[r] = spans[r];
  }
  return out;
}
