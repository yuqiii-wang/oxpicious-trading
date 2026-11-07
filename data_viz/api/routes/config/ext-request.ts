/**
 * GET/PUT /api/config/ext-request — the External Resources tab (the
 * external_request_config/ static tree).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import { decodeLabel, encodeLabelParam } from "../../services/label-codes.js";
import {
  readDefaultExtRequestConfig,
  readExternalRequestConfig,
  writeExtRequestRowsFor,
  type ExtRequestConfigRow,
} from "../../services/static-config.service.js";

const router = Router();

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
  /** Playwright flows' post-navigation settle wait (null = the flow keeps
   *  its own behavior). */
  settleSleepSec: number | null;
  /** The row's endpoint inventory {role: url} (null = none). */
  urls: Record<string, string> | null;
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
    settleSleepSec: r.settle_sleep_sec ?? null,
    urls: r.urls ?? null,
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
  ["settleSleepSec", "settle_sleep_sec"],
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
    settle_sleep_sec: null,
    urls: null,
    notes: null,
    source_ref: "",
    updated_at: "", // stamped below
  };
  if (b.urls != null) {
    if (typeof b.urls !== "object" || Array.isArray(b.urls)) {
      throw new Error("urls must be an object of {role: url} strings");
    }
    const urls: Record<string, string> = {};
    for (const [rawRole, rawUrl] of Object.entries(
      b.urls as Record<string, unknown>,
    )) {
      const role = rawRole.trim();
      const url = typeof rawUrl === "string" ? rawUrl.trim() : "";
      if (role === "" || url === "") {
        throw new Error(
          `urls entry "${rawRole}" must be a non-empty role = url pair`,
        );
      }
      urls[role] = url;
    }
    row.urls = Object.keys(urls).length > 0 ? urls : null;
  }
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

export default router;
