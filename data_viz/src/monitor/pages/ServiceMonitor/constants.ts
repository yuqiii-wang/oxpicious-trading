/**
 * Shared constants + status-chip vocabularies of the Service Monitor page
 * (split out of the page file — index.tsx carries the page docstring).
 */
import type {
  ProcessStatus,
  ServiceAuditEntry,
  StreamServiceStatus,
} from "@/lib/api-client/monitor";

/** Table auto-refresh cadence (beat ages tick client-side every 1s). */
export const REFRESH_MS = 30_000;
/** Dropdown sentinel value for "no filter". */
export const ALL = "all";

/** Process-id-tag of the forced health check (must match the server's
 * POST /api/monitor/health-check spawn tag). */
export const HEALTH_CHECK_TAG = "monitor:health-check";
/** Poll interval (ms) while a health check started elsewhere is running. */
export const HC_REMOTE_POLL_MS = 3000;

export const STATE_CHIP: Record<
  StreamServiceStatus["status"],
  { label: string; color: "success" | "warning" | "error" | "default" }
> = {
  live: { label: "live", color: "success" },
  stale: { label: "stale", color: "warning" },
  stopped: { label: "stopped", color: "default" },
  down: { label: "down", color: "error" },
};

export const PROCESS_CHIP: Record<
  ProcessStatus["status"],
  { label: string; color: "success" | "warning" | "default" | "secondary" }
> = {
  running: { label: "running", color: "success" },
  stale: { label: "stale", color: "warning" },
  stopped: { label: "stopped", color: "default" },
  never: { label: "never", color: "secondary" },
};

/** Fetch state of one expanded process's run history. */
export interface ProcessRunsState {
  loading: boolean;
  error: string | null;
  runs: ServiceAuditEntry[];
}

/** Idle state used before a row's history was fetched / while closed. */
export const NO_RUNS: ProcessRunsState = { loading: false, error: null, runs: [] };
