/**
 * Shared constants + small helpers for the Market Sentiments by AI and News
 * composites page.
 */
import {
  fetchIndustrySentimentsStrategyThemes,
  fetchIndustrySentimentsThemes,
} from "@/lib/api-client";

/** Nav trees endpoints — the SAME sources as Industry Sentiments
 *  (index-only; the classification pick scopes chart shades AND the feed). */
export const THEMES_SOURCES = {
  index: {
    themes: (exchange: string | null) => fetchIndustrySentimentsThemes(exchange),
    strategyThemes: (exchange: string | null) => fetchIndustrySentimentsStrategyThemes(exchange),
  },
};

/** Market mode: the feed-scope hypes & drains fetch uses the SAME defaults
 *  the MarketTrendChart uses internally (broad-market CSI 300 · ~6-month
 *  rolling window · equal weighting), so the feed's "top industries" match
 *  what the plot's Hypes & Drains sub-view shows out of the box. */
export const MARKET_TOP_BENCHMARK = "000300";

/** Market mode: the broad-market tag is ALWAYS part of the feed scope —
 *  downloads.macro.ai_daily stores the daily broad-market summary QA under
 *  it (stats.sec_index_tags for 000001; same value the DataViz AI page
 *  resolves through the strategy tree), and it never appears in the
 *  hypes & drains rankings, so without it the market feed would never
 *  show the one QA that updates every trading day. */
export const MARKET_FEED_BROAD_INDUSTRY_ID = "BROAD_SSE";

/** Feed page size (server caps at 100). */
export const PAGE_SIZE = 20;

/** A picked date windows the feed ±this many CALENDAR days around itself
 *  (instead of narrowing it to the single day) — both AI and news. */
export const DATE_WINDOW_DAYS = 120;

/** Shift a YYYY-MM-DD string by whole days (UTC arithmetic — no DST). */
export function shiftDate(date: string, days: number): string {
  const d = new Date(`${date}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** Suffix-insensitive code match (000001.SZ ≙ 000001) — same as CodeSearchBar. */
export function normCode(code: string): string {
  return code.toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "");
}
