import { fetchJson } from "./_cache";
import type {
  SectorNode,
  StrategyNode,
  PerfAttrCodesResponse,
  PerfAttrChartResponse,
  PerfAttrAttributionResponse,
  PerfAttrSecType,
} from "../../../shared/types";

// ---------------------------------------------------------------------------
//  Analysis Commons — Perf Attribution (ETF/Index × Index)
//  TTL-only cache (analysis schema is recomputed offline).
// ---------------------------------------------------------------------------
export function fetchPerfAttrCodes(
  secType: PerfAttrSecType = "etf",
): Promise<PerfAttrCodesResponse> {
  return fetchJson<PerfAttrCodesResponse>(
    `/api/analysis/perf-attr/codes?sec_type=${secType}`,
  );
}

/** Themes tree (L1 sector → L2 industry → items) for the ThemeSelector.
 *  Only includes codes that have rows in analysis.sec_alloc_perf_attribution. */
export function fetchPerfAttrThemes(
  secType: PerfAttrSecType = "etf",
): Promise<SectorNode[]> {
  return fetchJson<SectorNode[]>(
    `/api/analysis/perf-attr/themes?sec_type=${secType}`,
  );
}

export function fetchPerfAttrStrategyThemes(
  secType: PerfAttrSecType = "etf",
): Promise<StrategyNode[]> {
  return fetchJson<StrategyNode[]>(
    `/api/analysis/perf-attr/strategy-themes?sec_type=${secType}`,
  );
}

export function fetchPerfAttrAttribution(
  code: string,
  secType: PerfAttrSecType = "etf",
  date?: string | null,
): Promise<PerfAttrAttributionResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  params.set("sec_type", secType);
  if (date) params.set("date", date);
  const qs = params.toString();
  return fetchJson<PerfAttrAttributionResponse>(
    `/api/analysis/perf-attr/attribution?${qs}`,
  );
}

export function fetchPerfAttrChart(
  code: string,
  benchmarkCode: string,
  secType: PerfAttrSecType = "etf",
): Promise<PerfAttrChartResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (benchmarkCode) params.set("benchmark_code", benchmarkCode);
  params.set("sec_type", secType);
  const qs = params.toString();
  return fetchJson<PerfAttrChartResponse>(
    `/api/analysis/perf-attr/chart?${qs}`,
  );
}
