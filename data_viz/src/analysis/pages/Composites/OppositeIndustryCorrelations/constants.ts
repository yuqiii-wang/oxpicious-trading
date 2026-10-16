/**
 * Shared types, constants, and row helpers for the Opposite Industry
 * Correlations (by benchmark offset) composites page.
 */
import {
  fetchIndustrySentimentsStrategyThemes,
  fetchIndustrySentimentsThemes,
} from "@/lib/api-client";
import type { IndustryCorrOffsetRow } from "@shared/types";

export type PoolSize = "all" | "small" | "mid" | "large";
export type CorrWindow = "20d" | "60d" | "255d";
/** The charted metrics — the opposite score (1 − offset) / 2 is a pure
 *  rescale of the offset correlation, so it is not a separate metric. */
export type OffsetMetric = "overall" | "sub";

export const WINDOWS: CorrWindow[] = ["20d", "60d", "255d"];

/** Nav trees endpoints (index-only — the same classification trees Industry
 *  Sentiments browses; industries/themes map 1:1 onto the offset table's
 *  industry_ids from stats.sec_classification). */
export const THEMES_SOURCES = {
  index: {
    themes: (exchange: string | null) => fetchIndustrySentimentsThemes(exchange),
    strategyThemes: (exchange: string | null) => fetchIndustrySentimentsStrategyThemes(exchange),
  },
};

/** metric → per-window row column. */
export const METRIC_COLS: Record<OffsetMetric, Record<CorrWindow, string>> = {
  overall: {
    "20d": "overall_corr_ma20_20d",
    "60d": "overall_corr_ma60_60d",
    "255d": "overall_corr_ma255_255d",
  },
  sub: {
    "20d": "offset_sub_corr_ma20_20d",
    "60d": "offset_sub_corr_ma60_60d",
    "255d": "offset_sub_corr_ma255_255d",
  },
};

export const METRIC_LABELS: Record<OffsetMetric, string> = {
  overall: "Overall",
  sub: "Benchmark Offset",
};

/** window → the row's END-date column for that window (start + W − 1
 *  trading days). The chart and the latest-window audit key by it, so
 *  the trailing window that ends at the latest date plots at that
 *  date — the live frontier is visible on the x-axis. */
export const WIN_END_COLS: Record<CorrWindow, string> = {
  "20d": "end_ma20_20d",
  "60d": "end_ma60_60d",
  "255d": "end_ma255_255d",
};

/** X key of a row for the given window (its END date), or null when the
 *  row does not carry that window (incomplete window / off-grid
 *  trailing row of another window). */
export function rowEndDate(
  r: IndustryCorrOffsetRow,
  win: CorrWindow,
): string | null {
  return (
    (r as unknown as Record<string, string | null>)[WIN_END_COLS[win]] ?? null
  );
}

export function rowVal(r: IndustryCorrOffsetRow, col: string): number | null {
  return (r as unknown as Record<string, number | null>)[col] ?? null;
}

export function pairLabel(r: IndustryCorrOffsetRow): string {
  const short = (label: string, id: string) =>
    label.split("  ")[0] || id;
  return `${short(r.industry_label, r.industry_id)} ↔ ${short(
    r.benchmark_industry_label, r.benchmark_industry_id,
  )}`;
}
