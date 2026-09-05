/**
 * API client for Industry Correlations by Benchmark Offset (composite
 * analysis) — reads analysis_composites.industry_corr_benchmark_offsets.
 */
import { fetchJson } from "./_cache";
import type { AnalysisRunResponse } from "./analysis-run";
import type {
  IndustryCorrOffsetBenchmarksResponse,
  IndustryCorrOffsetIndustriesResponse,
  IndustryCorrOffsetsResponse,
} from "@shared/types";

// ---------------------------------------------------------------------------
//  GET rows — audit rows for the user-selected industries.
// ---------------------------------------------------------------------------
export function fetchIndustryCorrOffsets(
  industryIds: string[],
  poolSize: "all" | "small" | "mid" | "large" = "all",
  benchmark = "000300",
): Promise<IndustryCorrOffsetsResponse> {
  const params = new URLSearchParams();
  if (industryIds.length > 0) params.set("industry_ids", industryIds.join(","));
  params.set("pool_size", poolSize);
  params.set("benchmark", benchmark);
  const qs = params.toString();
  return fetchJson<IndustryCorrOffsetsResponse>(
    `/api/analysis/industry-corr-offsets?${qs}`,
  );
}

// ---------------------------------------------------------------------------
//  GET benchmarks — distinct benchmark_code values materialized (dropdown).
// ---------------------------------------------------------------------------
export function fetchIndustryCorrOffsetBenchmarks(): Promise<IndustryCorrOffsetBenchmarksResponse> {
  return fetchJson<IndustryCorrOffsetBenchmarksResponse>(
    "/api/analysis/industry-corr-offsets/benchmarks",
  );
}

// ---------------------------------------------------------------------------
//  GET industries — selectable industry list for the page's multi-select.
// ---------------------------------------------------------------------------
export function fetchIndustryCorrOffsetIndustries(): Promise<IndustryCorrOffsetIndustriesResponse> {
  return fetchJson<IndustryCorrOffsetIndustriesResponse>(
    "/api/analysis/industry-corr-offsets/industries",
  );
}

// ---------------------------------------------------------------------------
//  Refresh trigger.
//
//  POST /api/analysis/industry-corr-offsets/run spawns
//  `python -m analyze.analysis_composites --industry ... --code ...
//   --benchmark ...` (filtered recompute + upsert for the chosen
//  industries) via the shared py-runner and waits for it to finish. Drives
//  the refresh button on the Composites → Opposite Industry Correlations
//  page. Deduped by a fixed process-id-tag — poll
//  fetchAnalysisRunStatus([INDUSTRY_CORR_OFFSET_RUN_TAG]) for the spinner.
// ---------------------------------------------------------------------------
/** Fixed process-id-tag for UI-triggered offset-corr refresh runs — must
 *  match the server-side INDUSTRY_CORR_OFFSET_RUN_TAG. */
export const INDUSTRY_CORR_OFFSET_RUN_TAG = "analysis-run:industry_corr_offsets";

/** Trigger a filtered offset-corr recompute + upsert for the chosen data.
 *  Resolves when the run finishes (or is deduped — already_running).
 *  Never throws; failures surface as { success: false, stderr_tail }. */
export async function runIndustryCorrOffsetsRefresh(
  industryIds: ReadonlyArray<string>,
  benchmark: string,
): Promise<AnalysisRunResponse> {
  try {
    const res = await fetch("/api/analysis/industry-corr-offsets/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        industry_ids: [...industryIds],
        benchmark,
      }),
    });
    if (!res.ok) {
      return { success: false, stderr_tail: `HTTP ${res.status}` };
    }
    return (await res.json()) as AnalysisRunResponse;
  } catch (e) {
    return { success: false, stderr_tail: String(e) };
  }
}
