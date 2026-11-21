/**
 * Shared formatting helpers for the News Audit tables (kept out of the
 * component files so they stay component-only for react fast refresh).
 */

/** fake_pct (0..1) → display percent; null when nothing was digested. */
export function fmtPct(v: number | null): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

/** Sentiment-colored text — green bullish / red bearish / neutral body
 *  (same ±0.5 read as the news feed's sentiment chip). */
export function sentimentSx(v: number | null): { color?: string } {
  if (v == null || !Number.isFinite(v)) return {};
  if (v > 0.5) return { color: "success.main" };
  if (v < -0.5) return { color: "error.main" };
  return {};
}

/** Filter-vocabulary label for an origin's author — the '' sentinel (the
 *  provider's own rows) reads as "(provider)", matching the Author column's
 *  ticks filter so the toolbar select and the header filter agree. */
export function authorFilterLabel(author: string): string {
  return author.length > 0 ? author : "(provider)";
}
