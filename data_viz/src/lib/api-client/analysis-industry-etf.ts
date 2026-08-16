import { fetchJson } from "./_cache";
import type {
  IndustryEtfPriceSeriesResponse,
  IndustryEtfContributionBarsResponse,
} from "../../../shared/types";

// ---------------------------------------------------------------------------
//  Industry ETF Contribution — drives the "ETF Contribution" view on the
//  Industry Sentiments page. Mirrors "Benchmark Attribution" but with ETFs
//  as the unit of analysis.
// ---------------------------------------------------------------------------

/**
 * Fetch the daily close series for ALL ETFs tracking member indices of the
 * selected industries. Drives the 1st plot in "ETF Contribution" mode:
 * a multi-line chart where each line is one ETF, rebased to 100 at its own
 * first available date (cascading rebasing handled client-side). The chart
 * is clickable to pick the as-of date for the per-industry bar charts below.
 */
export function fetchIndustryEtfPriceSeries(
  industryIds: string[],
): Promise<IndustryEtfPriceSeriesResponse> {
  const params = new URLSearchParams();
  if (industryIds.length > 0) params.set("industry_ids", industryIds.join(","));
  return fetchJson<IndustryEtfPriceSeriesResponse>(
    `/api/analysis/industry-etf-contribution/etf-price?${params.toString()}`,
  );
}

/**
 * Fetch per-ETF contribution bars for ONE industry at a specific (or latest)
 * date. Returns one row per ETF with trading_amount + etf_return, plus the
 * industry aggregate from analysis.industry_etf_contribution. Drives the
 * 2nd+ plots in "ETF Contribution" mode.
 */
export function fetchIndustryEtfContributionBars(
  industryId: string,
  date?: string | null,
): Promise<IndustryEtfContributionBarsResponse> {
  const params = new URLSearchParams();
  params.set("industry_id", industryId);
  if (date) params.set("date", date);
  return fetchJson<IndustryEtfContributionBarsResponse>(
    `/api/analysis/industry-etf-contribution/etf-bars?${params.toString()}`,
  );
}
