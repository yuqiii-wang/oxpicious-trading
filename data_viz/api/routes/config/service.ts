/**
 * GET/PUT/DELETE /api/config/service — the Service config tab (the
 * service_config/ static tree).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  deleteServiceConfigEntry,
  readDefaultServiceConfig,
  readServiceConfig,
  validParallelServiceEntries,
  writeServiceConfigEntry,
  type ServiceConfigEntry,
} from "../../services/static-config.service.js";

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
  maxParallelRuns: number | null;
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
    maxParallelRuns: e.max_parallel_runs,
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
  // The in-service RUN cap (the deep-agents summary generation today):
  // null = unset (the code default of one at a time) or an integer >= 1.
  // Independent of maxParallel — that one caps the SERVICE PROCESSES,
  // this one caps the parallel RUNS inside the one daemon.
  let maxParallelRuns: number | null = null;
  if (b.maxParallelRuns != null) {
    maxParallelRuns = Number(b.maxParallelRuns);
    if (!Number.isInteger(maxParallelRuns) || maxParallelRuns < 1) {
      throw new Error("maxParallelRuns must be an integer >= 1 or null");
    }
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
    max_parallel_runs: maxParallelRuns,
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

export default router;
