// ---------------------------------------------------------------------------
//  Live Data — sec-alloc live attribution pipeline trigger + aggregates.
//
//  POST /api/live-data/sec-alloc-live/run spawns
//  `python -m live.sec_alloc_live_attribution` (via the shared py-runner)
//  and waits for it to finish. The module itself is incremental:
//    • heavy prev-date ref (live.sec_alloc_live_prev_ref) is built ONCE per
//      date and skipped when already present;
//    • light 5-min ticks (live.sec_alloc_live_attribution) are appended
//      for new bars only;
//    • when the prev-date ref is NOT ready, fallback rows
//      (is_without_trading_amt = true, equal-weight only) keep live data
//      flowing — a PG advisory lock degrades a second concurrent instance
//      to the fallback-only pass.
//  The Market Movements page fires this before each 5-min auto-refresh so
//  the shades never lag the raw 5-min bars.
//
//  GET /api/live-data/sec-alloc-live/attribution returns per-industry
//  weighted/equal aggregates at one tick + weighted_available, which
//  drives the UI "By Trading Amt" disable state (disabled while only
//  fallback rows exist for the benchmark+date).
// ---------------------------------------------------------------------------
import { fetchJson } from "./_cache";
import type { SecAllocLiveAttributionResponse } from "@shared/types";

/** Response of POST /api/live-data/sec-alloc-live/run. */
export interface SecAllocLiveRunResponse {
  /** True iff a run was started AND exited with code 0. */
  success: boolean;
  /** True when a previous run was still in flight and this one was skipped. */
  skipped_in_flight?: boolean;
  /** Tail of the Python stdout (diagnostics). */
  stdout_tail?: string;
  /** Tail of the Python stderr (diagnostics). */
  stderr_tail?: string;
}

/** Trigger one incremental run of `python -m live.sec_alloc_live_attribution`.
 *  Resolves when the run finishes (or is skipped because one is in flight).
 *  Never throws — failures surface as `{ success: false }` so refresh
 *  flows can proceed regardless. */
export async function runSecAllocLivePipeline(): Promise<SecAllocLiveRunResponse> {
  try {
    const res = await fetch("/api/live-data/sec-alloc-live/run", {
      method: "POST",
    });
    if (!res.ok) return { success: false, stderr_tail: `HTTP ${res.status}` };
    return (await res.json()) as SecAllocLiveRunResponse;
  } catch (e) {
    return { success: false, stderr_tail: String(e) };
  }
}

/** Per-industry weighted/equal aggregates at ONE 5-min tick for a
 *  benchmark. `weighted_available === false` disables the "By Trading
 *  Amt" toggle in the UI (only fallback rows exist for the date). */
export function fetchSecAllocLiveAttribution(
  benchmarkCode: string,
  date: string | null,
  time: string,
): Promise<SecAllocLiveAttributionResponse> {
  const params = new URLSearchParams();
  if (benchmarkCode) params.set("benchmark_code", benchmarkCode);
  if (date) params.set("date", date);
  if (time) params.set("time", time);
  return fetchJson<SecAllocLiveAttributionResponse>(
    `/api/live-data/sec-alloc-live/attribution?${params.toString()}`,
  );
}
