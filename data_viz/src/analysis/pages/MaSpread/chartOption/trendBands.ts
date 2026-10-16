/**
 * Trend vocabulary adapter for the MA-Spread chart — the SIGNALS
 * TIER's per-(code, date) trend classification (analysis.trend_days,
 * fetched via GET /api/live-data/trading-signals/trend-spans).
 *
 * REPLACES the retired client-side close-vs-MA60 phase classifier
 * (2026-10-15): the MA60 leg identified turns months late (after the
 * 2020-03 crash, close stayed below its crash-inflated MA60 all April
 * — a rising month shaded "down"). The signals tier's RSI re-entry
 * classifier (Wilder RSI(14) zones + the deep-anchor turn) is
 * server-side, shared with the rule cells, and already rebuilt daily
 * — this module only adapts its vocabulary and span format for the
 * pair chart's tooltip readout and the Trend section's shades.
 *
 * The spans are DATE ranges, so no index alignment with the pair rows
 * is needed (the CodeTrendChart precedent); unknown is the unshaded
 * background.
 */
import type { TrendSpan } from "@/lib/api-client";
import {
  SHADED_TRENDS,
  TREND_ACCENT_COLORS,
  TREND_LABELS,
  TREND_SHADE_COLORS,
} from "@/shared/charts/trendBands";

/** The pickable trend phases (unknown = the unshaded background). */
export type TrendType = "top" | "bottom" | "rise" | "fall";

/** Canonical button order of the Trend section's phase chips. */
export const TREND_TYPES: readonly TrendType[] =
  SHADED_TRENDS as readonly TrendType[];

/** Chip / legend label per trend type. */
export const TREND_TYPE_LABELS: Record<TrendType, string> = {
  top: TREND_LABELS.top,
  bottom: TREND_LABELS.bottom,
  rise: TREND_LABELS.rise,
  fall: TREND_LABELS.fall,
};

/** The picked chips' / legend accents (the shared trend palette). */
export const TREND_ACCENTS: Record<TrendType, string> = {
  top: TREND_ACCENT_COLORS.top,
  bottom: TREND_ACCENT_COLORS.bottom,
  rise: TREND_ACCENT_COLORS.rise,
  fall: TREND_ACCENT_COLORS.fall,
};

/** The trend-spans record as the API returns it (label -> spans). */
export type TrendSpansRecord = Record<string, TrendSpan[]>;

/** Translucent shade fill per trend (the shared graded palette). */
export const TREND_SHADES: Record<TrendType, string> = {
  top: TREND_SHADE_COLORS.top,
  bottom: TREND_SHADE_COLORS.bottom,
  rise: TREND_SHADE_COLORS.rise,
  fall: TREND_SHADE_COLORS.fall,
};

/** The trend span containing ``date`` (ISO), or null. */
export function findTrendSpanAt(
  spans: TrendSpansRecord | null,
  date: string,
): { trend: TrendType; span: TrendSpan } | null {
  if (spans == null) return null;
  for (const t of TREND_TYPES) {
    const list = spans[t];
    if (!list) continue;
    const s = list.find((x) => date >= x.start_date && date <= x.end_date);
    if (s) return { trend: t, span: s };
  }
  return null;
}

/** Short-series label, e.g. "Price", "MA5", or "EMA6". */
export function shortLabel(maShort: number, kind?: string): string {
  if (maShort === 0) return "Price";
  return kind === "ema" ? `EMA${maShort}` : `MA${maShort}`;
}
