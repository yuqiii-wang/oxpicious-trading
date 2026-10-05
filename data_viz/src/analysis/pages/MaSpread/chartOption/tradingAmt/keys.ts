/**
 * The shared-metrics keys per trading-amt MA window — one copy for both
 * the pair chart (Amt overlay mode) and the Amt Envelope view.
 */
import type { MovAveSpreadSharedMetricsRow } from "@shared/types";

/** The per-window trading-amt MA slope columns. */
export const SHARED_AMT_SLOPE_KEYS: Record<number, keyof MovAveSpreadSharedMetricsRow> = {
  5: "trading_amt_ma5_slope",
  20: "trading_amt_ma20_slope",
  60: "trading_amt_ma60_slope",
  120: "trading_amt_ma120_slope",
  255: "trading_amt_ma255_slope",
};

/** The per-window trading-amt market-share columns. */
export const SHARED_AMT_SHARE_KEYS: Record<number, keyof MovAveSpreadSharedMetricsRow> = {
  5: "trading_amt_market_share_ma5",
  20: "trading_amt_market_share_ma20",
  60: "trading_amt_market_share_ma60",
  120: "trading_amt_market_share_ma120",
  255: "trading_amt_market_share_ma255",
};
