/**
 * Shared constants + small helpers for the two Market Sentiments
 * composites pages.
 */
import {
  fetchMarketBetaStrategyThemes,
  fetchMarketBetaThemes,
  fetchMovAveSpreadStrategyThemes,
  fetchMovAveSpreadThemes,
} from "@/lib/api-client";

/** Nav trees endpoints per sec_type — the classification pick scopes chart
 *  shades AND the feed:
 *   • index  — the SAME trees as Market Beta (classified indices grouped by
 *     their industry / strategy-theme classification).
 *   • industry — the industry-ENTITY trees (code = industry_id) under their
 *     stats.sec_classification classification — the same two-column shape the
 *     Analysis Summary page's industry scope and the MA-Spread industry mode
 *     use. Entity ids ARE the feed's industry tags (text.llm_qa /
 *     text.news industry_id), so a pick scopes the feed directly. */
export const THEMES_SOURCES = {
  index: {
    themes: (exchange: string | null) => fetchMarketBetaThemes(exchange),
    strategyThemes: (exchange: string | null) => fetchMarketBetaStrategyThemes(exchange),
  },
  industry: {
    themes: (exchange: string | null) => fetchMovAveSpreadThemes("industry", exchange),
    strategyThemes: (exchange: string | null) => fetchMovAveSpreadStrategyThemes("industry", exchange),
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
