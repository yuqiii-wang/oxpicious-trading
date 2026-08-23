// ---------------------------------------------------------------------------
//  Analysis per-security run trigger + status.
//
//  POST /api/analysis/run-analysis spawns
//  `python -m analyze.<module> --sec-type <st> --code <code>` (via the
//  shared py-runner) and waits for it to finish. Used by the per-panel
//  "build this security" refresh button when a security has no analysis
//  rows (e.g. it was outside the active universe when the analysis last
//  ran, or the analysis table has a per-code hole date-level incremental
//  detection can never see).
//
//  GET /api/analysis/run-analysis/status?process_id_tag=a,b returns the
//  running-state of the tags so the button can restore its spinning state
//  after a page refresh while the remote process is still running.
// ---------------------------------------------------------------------------
import { fetchJson } from "./_cache";

/** Analysis mains that support single-security recomputation (--code). */
export type RunnableAnalysisModule =
  | "mov_ave_spread"
  | "fourier_freqs"
  | "pe_and_dividends";

/** Response of POST /api/analysis/run-analysis. */
export interface AnalysisRunResponse {
  /** True iff the run was started AND exited with code 0. */
  success: boolean;
  /** True when NO run was started because one with the SAME
   *  process-id-tag is already running (dedupe path). */
  already_running?: boolean;
  /** The process-id-tag the run was registered under. */
  process_id_tag?: string;
  /** Tail of the Python stdout (diagnostics). */
  stdout_tail?: string;
  /** Tail of the Python stderr (diagnostics). */
  stderr_tail?: string;
}

/** Process-id-tag for one (module, sec_type, code) run — must match the
 *  server-side tag format so status polling hits the same registry key. */
export function analysisRunTag(
  module: RunnableAnalysisModule,
  secType: string,
  code: string,
): string {
  return `analysis-run:${module}:${secType}:${code}`;
}

/** Trigger one single-security recompute run of `python -m analyze.<module>`.
 *
 *  Resolves when the run finishes (or is deduped — `already_running`).
 *  Never throws; failures surface as `{ success: false, stderr_tail }`
 *  so the refresh flow can proceed regardless. */
export async function runAnalysisForSecurity(
  module: RunnableAnalysisModule,
  secType: string,
  code: string,
): Promise<AnalysisRunResponse> {
  try {
    const res = await fetch("/api/analysis/run-analysis", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ module, sec_type: secType, code }),
    });
    if (!res.ok) {
      return { success: false, stderr_tail: `HTTP ${res.status}` };
    }
    return (await res.json()) as AnalysisRunResponse;
  } catch (e) {
    return { success: false, stderr_tail: String(e) };
  }
}

/** Running-state of analysis-run process tags — polled on mount (so a
 *  page refresh restores the spinner while a remote run continues) and
 *  while a run started elsewhere is in flight. */
export async function fetchAnalysisRunStatus(
  tags: ReadonlyArray<string>,
): Promise<Record<string, boolean>> {
  if (tags.length === 0) return {};
  const qs = `process_id_tag=${encodeURIComponent(tags.join(","))}`;
  const resp = await fetchJson<{ status: Record<string, boolean> }>(
    `/api/analysis/run-analysis/status?${qs}`,
  );
  return resp.status ?? {};
}
