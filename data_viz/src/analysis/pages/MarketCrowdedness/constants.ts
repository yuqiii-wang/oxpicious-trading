/**
 * Shared constants for the Market Crowdedness page sub-modules.
 */
import type { MarketCrowdednessHotTechMetric } from "@shared/types";

/**
 * Page size — number of CrowdednessPanel cards shown per page. Kept to 1
 * because each panel renders a full-height crowdedness curve chart.
 */
export const PAGE_SIZE = 1;

/**
 * The hot-tech stat buttons (HOT_TECH_METRIC_ITEMS order = the button
 * group's order, short-term stretch → trend → momentum → volume →
 * return), with the ma-spread-style chart labels. The default pick
 * (px_vs_ma20) lives on HotTechPanel's initial state.
 */
export const HOT_TECH_METRIC_ITEMS: ReadonlyArray<{
  key: MarketCrowdednessHotTechMetric;
  label: string;
}> = [
  { key: "ma5_ratio", label: "Px/MA5" },
  { key: "px_vs_ma20", label: "Px/MA20" },
  { key: "px_vs_ma60", label: "Px/MA60" },
  { key: "macd_hist", label: "MACD" },
  { key: "rsi14", label: "RSI14" },
  { key: "roc20", label: "ROC20" },
  { key: "amt_vs_ma20", label: "Amt/MA20" },
  { key: "ret", label: "Ret" },
];
