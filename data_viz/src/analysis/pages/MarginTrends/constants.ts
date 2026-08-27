/**
 * Shared constants for the Margin Trends analysis page sub-modules.
 */

/** Attribution toggle: Index (weighted-avg constituent-stock margin via
 *  the margin_index_series TABLE) or ETF (the ETF's own margin). */
export type MarginAttribution = "index" | "etf";
export const ATTRIBUTION_OPTIONS: MarginAttribution[] = ["index", "etf"];

/** Series toggle: balance (融资余额) or buy (融资买入额). */
export type MarginSeries = "balance" | "buy";
export const SERIES_OPTIONS: MarginSeries[] = ["balance", "buy"];
