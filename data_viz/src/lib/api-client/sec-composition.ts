import { fetchJson } from "./_cache";
import type {
  SecCompositionResponse,
  LinkedEtfsResponse,
  SimilarIndicesResponse,
  QuarterlyCompositionResponse,
  IndustryWeightSeriesResponse,
} from "@shared/types";

/** Fetch the composition holdings for a security.
 *  Without `date`: latest snapshot. With `date` ("YYYY-MM-DD"): the latest
 *  snapshot within the calendar QUARTER containing the date (by-season
 *  lookup — used by the ETF Holdings page's quarterly bar → pie drill-down). */
export function fetchSecComposition(
  code: string,
  date?: string,
): Promise<SecCompositionResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (date) params.set("date", date);
  const qs = params.toString();
  return fetchJson<SecCompositionResponse>(`/api/sec-composition${qs ? `?${qs}` : ""}`);
}

/** Fetch per-quarter industry-aggregated composition for a security (one
 *  entry per calendar quarter that has a snapshot; tracking-index fallback
 *  when the ETF has no snapshots). Used by the ETF Holdings page's stacked
 *  bar chart. */
export function fetchQuarterlyComposition(
  code: string,
): Promise<QuarterlyCompositionResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  const qs = params.toString();
  return fetchJson<QuarterlyCompositionResponse>(`/api/sec-composition/quarterly${qs ? `?${qs}` : ""}`);
}

/** Fetch ONE industry's weight in a security's composition across ALL
 *  snapshot dates (roughly monthly; denser than the quarterly view). Used by
 *  the ETF Holdings page's Industry-changes row drill-down. */
export function fetchIndustryWeightSeries(
  code: string,
  industryId: string,
): Promise<IndustryWeightSeriesResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (industryId) params.set("industry_id", industryId);
  const qs = params.toString();
  return fetchJson<IndustryWeightSeriesResponse>(
    `/api/sec-composition/industry-weight-series${qs ? `?${qs}` : ""}`,
  );
}

/** Fetch ETFs tracking the given index (parent_index_code = code).
 *  Used by the Index Baseline page's "Linked ETFs" expansion. */
export function fetchLinkedEtfs(code: string): Promise<LinkedEtfsResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  const qs = params.toString();
  return fetchJson<LinkedEtfsResponse>(`/api/sec-composition/linked-etfs${qs ? `?${qs}` : ""}`);
}

/** Fetch the top-3 similar indices by mutual shared composition weight for
 *  the given index (from stats.sec_similars, sec_type='index', latest snapshot <= today).
 *  TTL-only cache (composition snapshot dates move infrequently). */
export function fetchSimilarIndices(code: string): Promise<SimilarIndicesResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  const qs = params.toString();
  return fetchJson<SimilarIndicesResponse>(`/api/sec-composition/similar-indices${qs ? `?${qs}` : ""}`);
}
