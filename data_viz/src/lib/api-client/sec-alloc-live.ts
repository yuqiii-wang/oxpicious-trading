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
  /** True when NO run was started because one with the SAME
   *  process-id-tag is already running (dedupe path). */
  already_running?: boolean;
  /** Which process ran ("live" | "ref"). */
  mode?: "live" | "ref";
  /** The process-id-tag the run was registered under. */
  process_id_tag?: string;
  /** Tail of the Python stdout (diagnostics). */
  stdout_tail?: string;
  /** Tail of the Python stderr (diagnostics). */
  stderr_tail?: string;
}

/** Default process-id-tags (must match the server-side defaults). */
export const SEC_ALLOC_LIVE_REF_TAG = "sec-alloc-live:ref";
/** Downloads pre-step of the ref chain (targeted CSV refresh) — polled
 *  together with SEC_ALLOC_LIVE_REF_TAG so the button spins across ALL
 *  phases. */
export const SEC_ALLOC_LIVE_REF_DL_TAG = "sec-alloc-live:ref:dl";
/** Baseline pre-step of the ref chain (estimated daily rows rebuild). */
export const SEC_ALLOC_LIVE_REF_BASE_TAG = "sec-alloc-live:ref:base";
export const SEC_ALLOC_LIVE_LIVE_TAG = "sec-alloc-live:live";

/** Trigger one run of `python -m live.sec_alloc_live_attribution`.
 *
 *  Two independent processes share this endpoint via `mode`:
 *    • "live" (default) — fast equal-weight 5-min tick pass (no yday-ref
 *      dependency); fired automatically every 5 min during trading hours.
 *    • "ref" — the FULL yday-ref chain, run sequentially server-side and
 *      deduped by process-id-tag (a second POST while any phase runs
 *      resolves immediately with already_running: true):
 *        1. downloads.index.csindex.quote --ensure-prev-trading-day
 *           (tag …:ref:dl) — TARGETED CSV refresh: codes whose local
 *           CSVs already contain the prev trading day are skipped
 *           entirely; only laggards fetch the 1m window.
 *        2. builds.index.baseline --refresh-estimated-days 10 (tag
 *           …:ref:base) — rebuild recent ESTIMATED daily rows from the
 *           fresh CSVs so prev-day OHLC is real.
 *        3. live.sec_alloc_live_attribution --mode ref
 *           --rebuild-latest-date (tag …:ref) — invalidate this date's
 *           ref + tick rows, then heavy prev-day closes + trading
 *           amounts + weights + weighted tick upgrades.
 *      Fired by the "Build Yday Ref" button; may take minutes.
 *
 *  `processIdTag` dedupes: if a process with the same tag is already
 *  running, the server does NOT spawn a duplicate and resolves with
 *  `already_running: true` (the UI shows "process already running" and
 *  keeps the button spinning via the status poll).
 *
 *  Resolves when the run finishes (or is deduped). Never throws —
 *  failures surface as `{ success: false }` so refresh flows can proceed
 *  regardless. */
export async function runSecAllocLivePipeline(
  mode: "live" | "ref" = "live",
  processIdTag?: string,
): Promise<SecAllocLiveRunResponse> {
  try {
    const res = await fetch("/api/live-data/sec-alloc-live/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, process_id_tag: processIdTag }),
    });
    if (!res.ok) return { success: false, mode, stderr_tail: `HTTP ${res.status}` };
    return (await res.json()) as SecAllocLiveRunResponse;
  } catch (e) {
    return { success: false, mode, stderr_tail: String(e) };
  }
}

/** Running-state of sec-alloc-live process tags — polled on mount and
 *  while a REMOTE process runs, so a page refresh restores the Build
 *  Yday Ref button's spinning state until the process exits. */
export async function fetchSecAllocLiveRunStatus(
  tags: ReadonlyArray<string>,
): Promise<Record<string, boolean>> {
  const params = new URLSearchParams();
  if (tags.length) params.set("process_id_tag", tags.join(","));
  const res = await fetch(
    `/api/live-data/sec-alloc-live/run/status?${params.toString()}`,
  );
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  }
  const json = (await res.json()) as { status: Record<string, boolean> };
  return json.status ?? {};
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
