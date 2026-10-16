/**
 * Config API routes — READ + WRITE over the repo's static JSON config
 * trees (the Config page's four tabs; every underlying datum is a JSON
 * file under database/ or scripts/, no DB tables involved).
 *
 *   Service tab — database/sql/monitoring/static/service_config/ (one
 *   file per service; default.json is the tree-root fallback). Writes
 *   one entry per call; deleting a service's file lets the fallback
 *   apply again.
 *
 *   External Resources tab — database/sql/monitoring/static/
 *   external_request_config/ (one file per service holding its
 *   request-type rows; default.json fallback = the repo-wide 60s
 *   anti-bot cooldown). Writes one service's WHOLE row list per call
 *   (the dialog edits rows client-side, then saves the service's file);
 *   resource_type crosses the boundary DECODED ("http" | "llm" — the
 *   static.label_dim SMALLINT contract applies inside the files only).
 *
 *   Security Classification tab — database/sec_statics/ (hand-edited
 *   classification_rules/*.json + importance_rules/*.json curated top
 *   ranks + sec_owners.json, plus the GENERATED sec_classification/
 *   shard tree). Raw-text per-file GET/PUT; only files already in the
 *   tree are writable (JSON-validated, verbatim write) — shard creation
 *   stays with builds.classification.
 *
 *   Orchestration tab — READ-ONLY over scripts/orchestration/ (one
 *   step JSON per tier script; see orchestration.service). The files
 *   are git-reviewed edits like every other static config, so the tab
 *   only displays: no write route.
 *
 * All writes go through the static-config / sec-statics services
 * (updated_at stamping where the file shape has it, canonical shape,
 * atomic temp+rename). There is no auth on this localhost API — the
 * same trust level as POST /api/monitor/health-check spawning Python.
 */
import { Router, type Request, type Response } from "express";
import { decodeLabel, encodeLabelParam } from "../services/label-codes.js";
import {
  deleteServiceConfigEntry,
  readDefaultExtRequestConfig,
  readDefaultServiceConfig,
  readExternalRequestConfig,
  readServiceConfig,
  validParallelServiceEntries,
  writeExtRequestRowsFor,
  writeServiceConfigEntry,
  type ExtRequestConfigRow,
  type ServiceConfigEntry,
} from "../services/static-config.service.js";
import {
  listSecStaticsFiles,
  readSecStaticsFile,
  writeSecStaticsFile,
  type SecStaticsFile,
} from "../services/sec-statics.service.js";
import { readOrchestrationTiers } from "../services/orchestration.service.js";

const router = Router();

// ---------------------------------------------------------------------------
//  Service config — service_config/ tree
// ---------------------------------------------------------------------------

/** Closed freq vocabulary (scripts/*.sh cadences + the fallback), shared
 *  by validation here and the editor's dropdown in the UI. */
const FREQ_VALUES = [
  "always",
  "daily",
  "monthly",
  "semiannually",
  "once",
  "occasionally",
  "on_demand",
] as const;

export interface ServiceConfigPayload {
  service: string;
  maxParallel: number;
  includedParallelServices: string[];
  excludedParallelServices: string[];
  freq: string;
  schedule: string | null;
  notes: string | null;
  sourceRef: string;
  updatedAt: string;
}

function toServicePayload(e: ServiceConfigEntry): ServiceConfigPayload {
  return {
    service: e.service,
    maxParallel: e.max_parallel,
    includedParallelServices: e.included_parallel_services,
    excludedParallelServices: e.excluded_parallel_services,
    freq: e.freq,
    schedule: e.schedule,
    notes: e.notes,
    sourceRef: e.source_ref,
    updatedAt: e.updated_at,
  };
}

/** GET /api/config/service — the whole service-config inventory plus the
 *  default.json fallback entry. */
router.get("/service", (_req: Request, res: Response) => {
  try {
    res.json({
      success: true,
      generatedAt: new Date().toISOString(),
      rows: readServiceConfig().map(toServicePayload),
      default: toServicePayload(readDefaultServiceConfig()),
    });
  } catch (err) {
    console.error("[config/service GET] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

/** Validate + normalize a service-config entry from the wire (throws on
 *  bad input with a user-presentable message). */
function parseServiceEntry(body: unknown): ServiceConfigEntry {
  const b = (typeof body === "object" && body != null ? body : {}) as Record<string, unknown>;
  const service = typeof b.service === "string" ? b.service.trim() : "";
  if (
    service !== "default" &&
    !/^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*$/.test(service)
  ) {
    throw new Error("service must be a dotted name like builds.etf (or 'default')");
  }
  const maxParallel = Number(b.maxParallel);
  if (!Number.isInteger(maxParallel) || maxParallel < 1) {
    throw new Error("maxParallel must be an integer >= 1");
  }
  const freq = typeof b.freq === "string" ? b.freq : "";
  if (!(FREQ_VALUES as readonly string[]).includes(freq)) {
    throw new Error(`freq must be one of: ${FREQ_VALUES.join(" | ")}`);
  }
  // Parallel-services lists: arrays of rooted dotted prefixes that must
  // NAME a real scope / sub-scope path / service — the valid-entry set is
  // derived from the existing service_config/ inventory (every dotted
  // prefix of every service name), so a typo'd entry is a 400 instead of
  // a list the runtime guards silently never match.
  const validEntries = validParallelServiceEntries();
  const parseParallelList = (v: unknown, field: string): string[] => {
    if (v == null) return [];
    if (!Array.isArray(v) || v.some((e) => typeof e !== "string")) {
      throw new Error(`${field} must be an array of dotted names`);
    }
    const out: string[] = [];
    for (const e of v as string[]) {
      const entry = e.trim();
      if (entry === "") continue;
      if (!/^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*$/.test(entry)) {
        throw new Error(
          `${field} entries must be dotted names (scope, sub-scope path, or full service name), got '${entry}'`,
        );
      }
      if (!validEntries.has(entry)) {
        throw new Error(
          `${field} entry '${entry}' matches no scope, sub-scope path, or service in the service_config tree`,
        );
      }
      if (!out.includes(entry)) out.push(entry);
    }
    return out;
  };
  const included = parseParallelList(
    b.includedParallelServices,
    "includedParallelServices",
  );
  const excluded = parseParallelList(
    b.excludedParallelServices,
    "excludedParallelServices",
  );
  // A service runs in parallel iff it is included AND not excluded — one
  // entry cannot be in both lists (the UI dropdowns already enforce this;
  // this is the non-UI backstop).
  const both = included.filter((e) => excluded.includes(e));
  if (both.length > 0) {
    throw new Error(
      `entries cannot be in both parallel lists: ${both.join(", ")}`,
    );
  }
  const strOrNull = (v: unknown, field: string): string | null => {
    if (v == null) return null;
    if (typeof v !== "string") throw new Error(`${field} must be a string or null`);
    return v;
  };
  const sourceRef = strOrNull(b.sourceRef, "sourceRef");
  if (sourceRef == null || sourceRef === "") {
    throw new Error("sourceRef is required (relative file paths, ';'-separated)");
  }
  return {
    service,
    max_parallel: maxParallel,
    included_parallel_services: included,
    excluded_parallel_services: excluded,
    freq,
    schedule: strOrNull(b.schedule, "schedule"),
    notes: strOrNull(b.notes, "notes"),
    source_ref: sourceRef,
    updated_at: "", // stamped by the writer
  };
}

/** PUT /api/config/service — create/update ONE service's entry (the
 *  service name picks the tree file; "default" edits the fallback).
 *  updated_at is stamped server-side. */
router.put("/service", (req: Request, res: Response) => {
  try {
    const saved = writeServiceConfigEntry(parseServiceEntry(req.body));
    res.json({ success: true, entry: toServicePayload(saved) });
  } catch (err) {
    console.error("[config/service PUT] error:", err);
    res.status(400).json({ success: false, error: String(err) });
  }
});

/** DELETE /api/config/service/:service — remove a service's tree file so
 *  the default.json fallback applies to it again. */
router.delete("/service/:service", (req: Request, res: Response) => {
  const service = req.params.service ?? "";
  try {
    const existed = deleteServiceConfigEntry(service);
    if (!existed) {
      res.status(404).json({
        success: false,
        error: `no service_config file for ${service} (fallback already applies)`,
      });
      return;
    }
    res.json({ success: true });
  } catch (err) {
    console.error("[config/service DELETE] error:", err);
    res.status(400).json({ success: false, error: String(err) });
  }
});

// ---------------------------------------------------------------------------
//  External request config — external_request_config/ tree
// ---------------------------------------------------------------------------

export interface ExtRequestConfigPayload {
  service: string;
  requestType: string;
  /** Decoded ext_resource label: http | llm. */
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

function toExtRequestPayload(r: ExtRequestConfigRow): ExtRequestConfigPayload {
  return {
    service: r.service,
    requestType: r.request_type,
    resourceType: decodeLabel("ext_resource", r.resource_type) ?? String(r.resource_type),
    sleepSec: r.sleep_sec,
    sleepJitter: r.sleep_jitter,
    pollIntervalSec: r.poll_interval_sec,
    maxRetries: r.max_retries,
    retryBackoffSec: r.retry_backoff_sec,
    timeoutConnectSec: r.timeout_connect_sec,
    timeoutReadSec: r.timeout_read_sec,
    rotateBrowserProfile: r.rotate_browser_profile,
    addRandomParam: r.add_random_param,
    enableHostTracking: r.enable_host_tracking,
    hostBlockCooldownSec: r.host_block_cooldown_sec,
    notes: r.notes,
    sourceRef: r.source_ref,
    updatedAt: r.updated_at,
  };
}

const NUMERIC_FIELDS: Array<
  [keyof ExtRequestConfigPayload, keyof ExtRequestConfigRow]
> = [
  ["sleepSec", "sleep_sec"],
  ["sleepJitter", "sleep_jitter"],
  ["pollIntervalSec", "poll_interval_sec"],
  ["maxRetries", "max_retries"],
  ["retryBackoffSec", "retry_backoff_sec"],
  ["timeoutConnectSec", "timeout_connect_sec"],
  ["timeoutReadSec", "timeout_read_sec"],
  ["hostBlockCooldownSec", "host_block_cooldown_sec"],
];

/** Validate + encode ONE row from the wire (decoded resourceType label
 * → file's SMALLINT code; numbers ≥ 0 or null; booleans required). */
function parseExtRequestRow(body: unknown, service: string): ExtRequestConfigRow {
  const b = (typeof body === "object" && body != null ? body : {}) as Record<string, unknown>;
  const requestType = typeof b.requestType === "string" ? b.requestType.trim() : "";
  if (requestType === "") throw new Error("requestType must be a non-empty string");
  const resourceLabel = typeof b.resourceType === "string" ? b.resourceType : "";
  const resourceType = encodeLabelParam("ext_resource", resourceLabel);
  if (resourceType == null) throw new Error("resourceType must be 'http' or 'llm'");
  const row: ExtRequestConfigRow = {
    service,
    request_type: requestType,
    resource_type: resourceType,
    sleep_sec: null,
    sleep_jitter: null,
    poll_interval_sec: null,
    max_retries: null,
    retry_backoff_sec: null,
    timeout_connect_sec: null,
    timeout_read_sec: null,
    rotate_browser_profile: b.rotateBrowserProfile === true,
    add_random_param: b.addRandomParam === true,
    enable_host_tracking: b.enableHostTracking === true,
    host_block_cooldown_sec: null,
    notes: null,
    source_ref: "",
    updated_at: "", // stamped below
  };
  for (const [wire, file] of NUMERIC_FIELDS) {
    const v = b[wire];
    if (v == null) continue;
    const n = Number(v);
    if (!Number.isFinite(n) || n < 0) {
      throw new Error(`${wire} must be a number >= 0 or null`);
    }
    (row as unknown as Record<string, number | null>)[file] = n;
  }
  if (typeof b.notes === "string") row.notes = b.notes;
  const sourceRef = typeof b.sourceRef === "string" ? b.sourceRef : "";
  if (sourceRef === "") {
    throw new Error("sourceRef is required (relative file paths, ';'-separated)");
  }
  row.source_ref = sourceRef;
  return row;
}

/** GET /api/config/ext-request — the whole per-(service, request_type)
 *  pacing inventory (decoded resourceType) plus the default.json
 *  fallback rows. */
router.get("/ext-request", (_req: Request, res: Response) => {
  try {
    res.json({
      success: true,
      generatedAt: new Date().toISOString(),
      rows: readExternalRequestConfig().map(toExtRequestPayload),
      defaultRows: readDefaultExtRequestConfig().map(toExtRequestPayload),
    });
  } catch (err) {
    console.error("[config/ext-request GET] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

/** PUT /api/config/ext-request — write ONE service's whole row list (the
 *  body's rows all carry the same service; "default" edits the root
 *  fallback). An empty row list for a real service DELETES its file so
 *  the fallback applies again. updated_at is stamped on every row. */
router.put("/ext-request", (req: Request, res: Response) => {
  try {
    const b = (typeof req.body === "object" && req.body != null ? req.body : {}) as Record<string, unknown>;
    const service = typeof b.service === "string" ? b.service.trim() : "";
    if (
      service !== "default" &&
      !/^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*$/.test(service)
    ) {
      throw new Error("service must be a dotted name like downloads.etf.huatai_pb.trend (or 'default')");
    }
    if (!Array.isArray(b.rows)) {
      throw new Error("body must be { service, rows: [...] }");
    }
    const now = new Date().toISOString();
    const rows = b.rows.map((r) => ({
      ...parseExtRequestRow(r, service),
      updated_at: now,
    }));
    writeExtRequestRowsFor(service, rows);
    const saved =
      service === "default" ? readDefaultExtRequestConfig() : rows;
    res.json({
      success: true,
      service,
      rows: saved.map(toExtRequestPayload),
    });
  } catch (err) {
    console.error("[config/ext-request PUT] error:", err);
    res.status(400).json({ success: false, error: String(err) });
  }
});

// ---------------------------------------------------------------------------
//  Security classification statics — database/sec_statics/ tree
// ---------------------------------------------------------------------------

/** GET /api/config/sec-statics — the file inventory (grouping areas,
 *  generated flags, sizes, mtimes). Content is fetched per file on
 *  selection via /sec-statics/file. */
router.get("/sec-statics", (_req: Request, res: Response) => {
  try {
    const files: SecStaticsFile[] = listSecStaticsFiles();
    res.json({
      success: true,
      generatedAt: new Date().toISOString(),
      files,
    });
  } catch (err) {
    console.error("[config/sec-statics GET] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

/** GET /api/config/sec-statics/file?path=… — one file's raw text. */
router.get("/sec-statics/file", (req: Request, res: Response) => {
  const p = typeof req.query.path === "string" ? req.query.path : "";
  try {
    const { file, text } = readSecStaticsFile(p);
    res.json({ success: true, file, text });
  } catch (err) {
    console.error("[config/sec-statics/file GET] error:", err);
    res.status(404).json({ success: false, error: String(err) });
  }
});

/** PUT /api/config/sec-statics/file — write one file's text verbatim
 *  (JSON-parse validated server-side; 400 with the parse error when
 *  invalid). */
router.put("/sec-statics/file", (req: Request, res: Response) => {
  const b = (typeof req.body === "object" && req.body != null ? req.body : {}) as Record<string, unknown>;
  const p = typeof b.path === "string" ? b.path : "";
  const text = typeof b.text === "string" ? b.text : null;
  if (text == null) {
    res.status(400).json({ success: false, error: "body must be { path, text }" });
    return;
  }
  try {
    const file = writeSecStaticsFile(p, text);
    res.json({ success: true, file });
  } catch (err) {
    console.error("[config/sec-statics/file PUT] error:", err);
    res.status(400).json({ success: false, error: String(err) });
  }
});

// ---------------------------------------------------------------------------
//  Orchestration steps — scripts/orchestration/ tree (read-only)
// ---------------------------------------------------------------------------

/** GET /api/config/orchestration — every tier's step list (tiers in
 *  main_run.sh run order, steps by step number). No write route: the
 *  files are git-reviewed edits. */
router.get("/orchestration", (_req: Request, res: Response) => {
  try {
    res.json({
      success: true,
      generatedAt: new Date().toISOString(),
      tiers: readOrchestrationTiers(),
    });
  } catch (err) {
    console.error("[config/orchestration GET] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

export default router;
