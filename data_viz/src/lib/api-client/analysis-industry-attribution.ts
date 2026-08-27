import { fetchJson } from "./_cache";
import type {
  IndustryBenchmarkAttributionResponse,
  IndustryAttributionBenchmarksResponse,
  BenchmarkPriceChartResponse,
  IndustryAttributionPriceSeriesResponse,
  AllIndustriesAttributionResponse,
  MemberIndexAttributionResponse,
  IndustryHypesAndDrainsResponse,
} from "@shared/types";

/**
 * Fetch the industry-level benchmark attribution for ONE industry at a
 * specific (or latest) date. Reads pre-materialized rows from
 * analysis.industry_attributions (industry_shared_weight +
 * benchmark_shared_weight per benchmark_code). benchmark_return is computed
 * on-the-fly. Drives the per-industry attribution bar charts (2nd plot
 * onward) in "Benchmark Attribution" mode on the IndustrySentiments page.
 *
 * `date` is optional (defaults to latest available). Returns one row per
 * benchmark with the shared weights and benchmark_return for that
 * (industry, benchmark, date).
 */
export function fetchIndustryBenchmarkAttribution(
  industryId: string,
  date?: string | null,
): Promise<IndustryBenchmarkAttributionResponse> {
  const params = new URLSearchParams();
  if (industryId) params.set("industry_id", industryId);
  if (date) params.set("date", date);
  const qs = params.toString();
  return fetchJson<IndustryBenchmarkAttributionResponse>(
    `/api/analysis/industry-benchmark-attribution?${qs}`,
  );
}

/**
 * Fetch the list of benchmark codes that appear in
 * analysis.industry_attributions, enriched with display name and
 * is_broad_market flag. Broad-market benchmarks are sorted first. Drives
 * the benchmark dropdown in "Benchmark Attribution" mode.
 */
export function fetchIndustryAttributionBenchmarks(): Promise<IndustryAttributionBenchmarksResponse> {
  return fetchJson<IndustryAttributionBenchmarksResponse>(
    `/api/analysis/industry-attribution/benchmarks`,
  );
}

/**
 * Fetch the daily close + fractional daily return series for ONE benchmark
 * index. Drives the 1st plot (benchmark price chart, clickable to pick a
 * date) in "Benchmark Attribution" mode.
 */
export function fetchBenchmarkPriceChart(
  code: string,
): Promise<BenchmarkPriceChartResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  const qs = params.toString();
  return fetchJson<BenchmarkPriceChartResponse>(
    `/api/analysis/industry-attribution/benchmark-price?${qs}`,
  );
}

/**
 * Fetch the non-this-industry price series for ONE (industry, benchmark) pair.
 * Returns benchmark close + benchmark_rolling + non_this_industry_price +
 * 5 rolling_Xdays_price columns (5/20/60/255/500) per date. Drives the
 * green/red shade overlay on the BenchmarkPriceChart — the frontend dropdown
 * picks which rolling window drives the shade.
 */
export function fetchIndustryAttributionPriceSeries(
  industryId: string,
  benchmarkCode: string,
): Promise<IndustryAttributionPriceSeriesResponse> {
  const params = new URLSearchParams();
  params.set("industry_id", industryId);
  params.set("benchmark_code", benchmarkCode);
  return fetchJson<IndustryAttributionPriceSeriesResponse>(
    `/api/analysis/industry-attribution/non-this-industry-price?${params.toString()}`,
  );
}

/** Fetch all industries' benchmark_shared_weight for a given benchmark+date.
 *  Drives the industry-level bar chart in "Benchmark Attribution" mode. */
export function fetchAllIndustriesAttribution(
  benchmarkCode: string,
  date?: string | null,
): Promise<AllIndustriesAttributionResponse> {
  const params = new URLSearchParams();
  params.set("benchmark_code", benchmarkCode);
  if (date) params.set("date", date);
  return fetchJson<AllIndustriesAttributionResponse>(
    `/api/analysis/industry-attribution/all-industries?${params.toString()}`,
  );
}

/** Fetch pre-computed top-5 (HYPE) + bottom-5 (DRAIN) industries ranked by
 *  attribution contribution to a COMPOSITE broad-market benchmark (MAIN or
 *  INNOV). Returns the 10 ranked industries + composite benchmark price series
 *  + each industry's mean_close series. Drives the "Hypes & Drains" sub-toggle
 *  in "Market Trend" mode.
 *  weighting: 'equal' (raw attribution contribution) or 'amt'
 *  (contribution × shared_trading_amt). Default: 'equal'. */
export function fetchIndustryHypesAndDrains(
  benchmarkCode: string,
  periodDays: number,
  weighting: "equal" | "amt" = "equal",
): Promise<IndustryHypesAndDrainsResponse> {
  const params = new URLSearchParams();
  params.set("benchmark_code", benchmarkCode);
  params.set("period_days", String(periodDays));
  params.set("weighting", weighting);
  return fetchJson<IndustryHypesAndDrainsResponse>(
    `/api/analysis/industry-hypes-and-drains?${params.toString()}`,
  );
}

/** Fetch all member indices' code_sec_shared_weight for a given
 *  industry+benchmark+date. Drives the per-industry bar charts. */
export function fetchMemberIndexAttribution(
  industryId: string,
  benchmarkCode: string,
  date?: string | null,
): Promise<MemberIndexAttributionResponse> {
  const params = new URLSearchParams();
  params.set("industry_id", industryId);
  params.set("benchmark_code", benchmarkCode);
  if (date) params.set("date", date);
  return fetchJson<MemberIndexAttributionResponse>(
    `/api/analysis/industry-attribution/member-indices?${params.toString()}`,
  );
}
