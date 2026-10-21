/**
 * Shared constants for the Margin Trends analysis page sub-modules.
 */

/** Attribution toggle: Index (weighted-avg constituent-stock margin via
 *  the margin_index_series TABLE), ETF (the ETF's own margin), Industry
 *  (the industry's OWN SUM(stock+etf) margin curve from
 *  analysis.margin_industry_stats — ONE series, code = industry_id), or
 *  Exchange (the exchange's OFFICIAL market-wide totals from
 *  analysis.exchange_margin_summary — ONE series, code = 'SS'/'SZ'). */
export type MarginAttribution = "index" | "etf" | "industry" | "exchange";
export const ATTRIBUTION_OPTIONS: MarginAttribution[] = [
  "index",
  "etf",
  "industry",
  "exchange",
];

/** Series toggle: balance (融资余额) or buy (融资买入额). */
export type MarginSeries = "balance" | "buy";
export const SERIES_OPTIONS: MarginSeries[] = ["balance", "buy"];

/** Value-mode toggle (industry attribution only — the only one backed by
 *  analysis.margin_industry_stats): raw = the SUM(stock+etf) member-total
 *  curve; normalized = the SUM of the per-side member AVERAGES over
 *  non-null non-zero members (*_norm cols) — the per-member-normalized
 *  curve, comparable across industries with different member counts.
 *  The amplification analytics (returns / vol / buy-share terciles) stay
 *  raw-based in both modes. */
export type MarginValueMode = "raw" | "normalized";
export const VALUE_MODE_OPTIONS: MarginValueMode[] = ["raw", "normalized"];
