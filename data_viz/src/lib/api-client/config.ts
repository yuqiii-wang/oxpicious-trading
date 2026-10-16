/**
 * API client for the Config pages (/api/config) — read + WRITE over the
 * repo's static JSON config trees: the service_config/ and
 * external_request_config/ trees under
 * database/sql/monitoring/static/ and the database/sec_statics/ tree.
 *
 * Plain fetch throughout (NOT the shared fetchJson cache): these pages
 * MUTATE the underlying files, so every read after a save must hit the
 * server — a 10-min TTL cache would serve the pre-save state.
 */

// ---------------------------------------------------------------------------
//  Service config (service_config/ tree)
// ---------------------------------------------------------------------------

/** One service_config/ tree entry: a service's concurrency cap +
 *  supposed-run cadence (mirrored from scripts/*.sh) + its cross-service
 *  parallel-services lists (included = whitelist, empty = unrestricted;
 *  excluded = denylist; entries are rooted dotted prefixes — a scope, a
 *  sub-scope path, or the full service name; more specific wins on
 *  conflict, name > sub-scope > scope, ties to excluded). */
export interface ServiceConfigEntry {
  service: string;
  maxParallel: number;
  includedParallelServices: string[];
  excludedParallelServices: string[];
  /** always | daily | monthly | semiannually | once | occasionally |
   *  on_demand (the default.json fallback's value). */
  freq: string;
  schedule: string | null;
  notes: string | null;
  sourceRef: string;
  updatedAt: string;
}

export interface ServiceConfigResponse {
  success: true;
  generatedAt: string;
  rows: ServiceConfigEntry[];
  /** The default.json fallback entry (service "default"). */
  default: ServiceConfigEntry;
}

/** Body of a service-config save — `service` picks the tree file
 *  ("default" edits the fallback); updatedAt is stamped server-side. */
export type ServiceConfigSave = Omit<ServiceConfigEntry, "updatedAt">;

export function fetchServiceConfig(): Promise<ServiceConfigResponse> {
  return fetch("/api/config/service").then((r) => r.json());
}

export function saveServiceConfig(
  entry: ServiceConfigSave,
): Promise<{ success: true; entry: ServiceConfigEntry }> {
  return fetch("/api/config/service", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(entry),
  }).then(async (r) => {
    const body = await r.json();
    if (!r.ok) throw new Error(body.error ?? `HTTP ${r.status}`);
    return body;
  });
}

/** Remove a service's file so the default fallback applies again. */
export function deleteServiceConfig(service: string): Promise<void> {
  return fetch(`/api/config/service/${encodeURIComponent(service)}`, {
    method: "DELETE",
  }).then(async (r) => {
    if (!r.ok) {
      const body = await r.json();
      throw new Error(body.error ?? `HTTP ${r.status}`);
    }
  });
}

// ---------------------------------------------------------------------------
//  External request config (external_request_config/ tree)
// ---------------------------------------------------------------------------

/** One per-(service, request_type) pacing / anti-bot config row.
 *  resourceType arrives/goes DECODED: http | llm. */
export interface ExtRequestConfigEntry {
  service: string;
  requestType: string;
  resourceType: string;
  sleepSec: number | null;
  sleepJitter: number | null;
  pollIntervalSec: number | null;
  maxRetries: number | null;
  retryBackoffSec: number | null;
  timeoutConnectSec: number | null;
  timeoutReadSec: number | null;
  rotateBrowserProfile: boolean;
  addRandomParam: boolean;
  enableHostTracking: boolean;
  hostBlockCooldownSec: number | null;
  notes: string | null;
  sourceRef: string;
  updatedAt: string;
}

export interface ExtRequestConfigResponse {
  success: true;
  generatedAt: string;
  rows: ExtRequestConfigEntry[];
  /** The default.json fallback rows (repo-wide 60s anti-bot cooldown). */
  defaultRows: ExtRequestConfigEntry[];
}

/** Save ONE service's whole row list — an empty list DELETES the
 *  service's file so the fallback applies again. */
export function saveExtRequestConfig(
  service: string,
  rows: Array<Omit<ExtRequestConfigEntry, "service" | "updatedAt">>,
): Promise<{ success: true; service: string; rows: ExtRequestConfigEntry[] }> {
  return fetch("/api/config/ext-request", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ service, rows }),
  }).then(async (r) => {
    const body = await r.json();
    if (!r.ok) throw new Error(body.error ?? `HTTP ${r.status}`);
    return body;
  });
}

export function fetchExtRequestConfig(): Promise<ExtRequestConfigResponse> {
  return fetch("/api/config/ext-request").then((r) => r.json());
}

// ---------------------------------------------------------------------------
//  Security classification statics (database/sec_statics/ tree)
// ---------------------------------------------------------------------------

export type SecStaticsArea =
  | "rules"
  | "importance"
  | "owners"
  | "catalog"
  | "indices"
  | "meta";

export interface SecStaticsFile {
  /** Tree-relative POSIX path — the read/write key. */
  path: string;
  area: SecStaticsArea;
  /** Rebuilt wholesale by builds.classification — edits don't survive. */
  generated: boolean;
  sizeBytes: number;
  updatedAt: string;
  /** importance_rules rank files only — curated code-list summary parsed
   *  from the file (shown inline in the path table). */
  codesCount?: number;
  sampleCodes?: string[];
}

export function fetchSecStaticsFiles(): Promise<{
  success: true;
  generatedAt: string;
  files: SecStaticsFile[];
}> {
  return fetch("/api/config/sec-statics").then((r) => r.json());
}

export function fetchSecStaticsFile(
  path: string,
): Promise<{ success: true; file: SecStaticsFile; text: string }> {
  return fetch(
    `/api/config/sec-statics/file?path=${encodeURIComponent(path)}`,
  ).then(async (r) => {
    const body = await r.json();
    if (!r.ok) throw new Error(body.error ?? `HTTP ${r.status}`);
    return body;
  });
}

/** Write one file's text verbatim (server JSON-validates). */
export function saveSecStaticsFile(
  path: string,
  text: string,
): Promise<{ success: true; file: SecStaticsFile }> {
  return fetch("/api/config/sec-statics/file", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, text }),
  }).then(async (r) => {
    const body = await r.json();
    if (!r.ok) throw new Error(body.error ?? `HTTP ${r.status}`);
    return body;
  });
}

// ---------------------------------------------------------------------------
//  Orchestration steps (scripts/orchestration/ tree — read-only)
// ---------------------------------------------------------------------------

/** One orchestration step: the runner executes a tier's steps one by
 *  one in step-number order (strictly serial — canBeParallel is false
 *  for every step today). */
export interface OrchestrationStep {
  step: number;
  script: string;
  args: string[];
  canBeParallel: boolean;
  note: string | null;
}

/** One tier script's steps (<tier>.json ↔ scripts/<tier>.sh). */
export interface OrchestrationTier {
  /** File stem, e.g. "builds_daily". */
  tier: string;
  /** Leading segment of the tier name, e.g. "builds". */
  domain: string;
  /** Trailing cadence segment, e.g. "daily" ("" when the name carries
   *  no known freq suffix). */
  freq: string;
  sourceRef: string;
  steps: OrchestrationStep[];
}

export function fetchOrchestrationConfig(): Promise<{
  success: true;
  generatedAt: string;
  tiers: OrchestrationTier[];
}> {
  return fetch("/api/config/orchestration").then((r) => r.json());
}
