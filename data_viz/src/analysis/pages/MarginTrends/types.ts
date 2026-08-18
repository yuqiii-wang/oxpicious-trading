/**
 * Shared types for the Margin Trends analysis page sub-modules.
 */
import type { ThemeMode } from "@/store/filters";
import type {
  MarginIndustrySeriesResponse,
  MarginIndustryCorrelationResponse,
} from "@shared/types";
import type { MarginAttribution, MarginSeries, CorrWindow } from "./constants";

/** Props for the MarginTrendsCharts component (both plots). */
export interface MarginTrendsChartsProps {
  industryId: string;
  themeMode: ThemeMode;
  attribution: MarginAttribution;
}

export type {
  MarginIndustrySeriesResponse,
  MarginIndustryCorrelationResponse,
} from "@shared/types";
export type { MarginAttribution, MarginSeries, CorrWindow } from "./constants";
