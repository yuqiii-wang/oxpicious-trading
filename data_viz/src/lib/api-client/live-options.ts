import { fetchJson } from "./_cache";
import type {
  LiveOptionsDatesResponse,
  LiveOptionsOiSkewResponse,
  LiveOptionsRunStatusResponse,
} from "@shared/types";
import type { OptionsTargetType } from "./options";

/** Spot-bar dates for an underlying (descending) — the live Options tab's
 *  DateSelector source. */
export function fetchLiveOptionsDates(
  underlying: string,
  targetType?: OptionsTargetType,
): Promise<LiveOptionsDatesResponse> {
  const params = new URLSearchParams();
  params.set("underlying", underlying);
  if (targetType) params.set("target_type", targetType);
  return fetchJson<LiveOptionsDatesResponse>(
    `/api/live-options/dates?${params.toString()}`,
  );
}

/** Underlying 5-min spot bars + the computed intraday OI-skew series.
 *  `date === null` = latest (server resolves). A missing/stale series
 *  triggers the route's on-demand compute (bounded wait + cooldown). */
export function fetchLiveOiSkewIntraday(
  underlying: string,
  date: string | null,
  targetType?: OptionsTargetType,
): Promise<LiveOptionsOiSkewResponse> {
  const params = new URLSearchParams();
  params.set("underlying", underlying);
  if (date) params.set("date", date);
  if (targetType) params.set("target_type", targetType);
  return fetchJson<LiveOptionsOiSkewResponse>(
    `/api/live-options/oi-skew-intraday?${params.toString()}`,
  );
}

/** Process-id tag of the on-demand compute for an (underlying, date) pair —
 *  mirror of the route-side tag construction (spinner restore polling). */
export function liveOptionsComputeTag(underlying: string, date: string): string {
  return `live-options:compute:${underlying}:${date}`;
}

export function fetchLiveOptionsRunStatus(
  tags: ReadonlyArray<string>,
): Promise<LiveOptionsRunStatusResponse> {
  const params = new URLSearchParams();
  params.set("process_id_tag", tags.join(","));
  return fetchJson<LiveOptionsRunStatusResponse>(
    `/api/live-options/run/status?${params.toString()}`,
  );
}

/** Process-id tags of the Build Yday Ref chain — the UI polls ALL of them
 *  (route-side whole-chain dedupe mirrors this list) so a page refresh
 *  during ANY phase restores the button's spinning state. */
export const liveOptionsRefChainTags = [
  "live-options:ref",
  "live-options:ref:dl",
  "live-options:ref:build",
  "live-options:ref:compute",
] as const;

/** Response of the "Build Yday Ref" chain (mirrors SecAllocLiveRunResponse). */
export interface LiveOptionsRefRunResponse {  success: boolean;
  mode: "ref";
  process_id_tag: string;
  already_running: boolean;
  stdout_tail: string;
  stderr_tail: string;
}

/** Build Yday Ref chain for the intraday OI-skew series: refresh the SSE
 *  contract listing → rebuild the prev-trading-day options snapshot the
 *  estimator bases its OI on (`snapshot_date`) → recompute the series for
 *  the on-screen underlying-day. The route dedupes by process-id-tag, so
 *  duplicate POSTs resolve immediately with already_running. */
export async function runLiveOptionsRefChain(opts: {
  snapshotDate: string;
  date?: string;
  underlying?: string;
  processIdTag?: string;
}): Promise<LiveOptionsRefRunResponse> {
  try {
    const res = await fetch("/api/live-options/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        snapshot_date: opts.snapshotDate,
        date: opts.date,
        underlying: opts.underlying,
        process_id_tag: opts.processIdTag,
      }),
    });
    if (!res.ok) {
      return {
        success: false,
        mode: "ref",
        process_id_tag: opts.processIdTag ?? "live-options:ref",
        already_running: false,
        stdout_tail: "",
        stderr_tail: `HTTP ${res.status}`,
      };
    }
    return (await res.json()) as LiveOptionsRefRunResponse;
  } catch (e) {
    return {
      success: false,
      mode: "ref",
      process_id_tag: opts.processIdTag ?? "live-options:ref",
      already_running: false,
      stdout_tail: "",
      stderr_tail: String(e),
    };
  }
}
