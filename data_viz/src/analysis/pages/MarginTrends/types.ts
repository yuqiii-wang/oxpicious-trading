/**
 * Shared types for the Margin Trends analysis page sub-modules.
 */
import type { MarginAttribution } from "./constants";

/** Props for the MarginTrendsCharts component. */
export interface MarginTrendsChartsProps {
  industryId: string;
  attribution: MarginAttribution;
  /** When set, show only this single security's margin + close curves
   *  (single-item mode). */
  selectedItemCode?: string | null;
}

export type { MarginIndustrySeriesResponse } from "@shared/types";
export type { MarginAttribution, MarginSeries } from "./constants";
