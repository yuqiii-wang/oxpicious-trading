/**
 * GET /api/config/cluster and GET /api/config/cluster/:area/openapi — the
 * Service config tab's SERVER CLUSTER view: the resident FastAPI area
 * servers (server_cluster/, one row per area) with their heartbeat state,
 * and each server's exposed API surface parsed from its FastAPI
 * /openapi.json (the machine-readable twin of the /docs page).
 * Full route docs: see index.ts.
 */

import { readFileSync } from "fs";
import path from "path";
import { Router, type Request, type Response } from "express";
import { wrapAsync, HttpError } from "../../lib/http.js";
import { renderSqlView } from "@api/lib/sql-views";
import { getReadPool } from "../../services/infra/db.service.js";

const router = Router();

// Mirror of _common/service_heartbeat.py STALE_AFTER_S (and the monitor
// routes' _shared.ts) — keep in sync. A beat older than this is stale.
const STALE_AFTER_S = 240;

/** The repo root (routes/config → routes → api → data_viz → root) — the
 *  cluster's area→port map lives at server_cluster/cluster_config.json. */
const REPO_ROOT = path.resolve(
  import.meta.dirname ?? __dirname,
  "..",
  "..",
  "..",
  "..",
);

const CLUSTER_CONFIG_PATH = path.join(
  REPO_ROOT,
  "server_cluster",
  "cluster_config.json",
);

/** One area server of the cluster: area name → host port. Read per
 *  request (tiny file, live edits apply without an API restart), sorted
 *  by port for a stable row order (llm 8767 → maintainer 8775). */
function readClusterAreas(): Array<{ area: string; port: number }> {
  let raw: unknown;
  try {
    raw = JSON.parse(readFileSync(CLUSTER_CONFIG_PATH, "utf-8"));
  } catch (e) {
    throw new HttpError(
      500,
      "INTERNAL",
      `cannot read server_cluster/cluster_config.json: ${e instanceof Error ? e.message : String(e)}`,
    );
  }
  const areas: Array<{ area: string; port: number }> = [];
  if (typeof raw === "object" && raw != null) {
    for (const [area, cfg] of Object.entries(raw as Record<string, unknown>)) {
      const port =
        typeof cfg === "object" && cfg != null
          ? (cfg as { port?: unknown }).port
          : undefined;
      if (typeof port === "number" && Number.isInteger(port)) {
        areas.push({ area, port });
      }
    }
  }
  return areas.sort((a, b) => a.port - b.port);
}

interface BeatRow {
  service: string;
  pid: number;
  started_at: Date;
  beat_at: Date;
  beat_age_s: number;
  scope: string;
  sub_scope: string | null;
  stop_reason: string | null;
  is_normal_exit: boolean | null;
  stopped_at: Date | null;
  live_parallel: number;
}

/** One cluster-server row: the area's address + its heartbeat state
 *  (monitor.service_heartbeat, service = server_cluster.<area>). Status
 *  vocabulary matches the Service Monitor's resident rows: live | stale |
 *  stopped | down (no beat row at all). */
export interface ClusterServerStatus {
  area: string;
  service: string;
  port: number;
  baseUrl: string;
  docsUrl: string;
  status: "live" | "stale" | "stopped" | "down";
  pid: number | null;
  startedAt: string | null;
  beatAt: string | null;
  beatAgeS: number | null;
  stopReason: string | null;
  isNormalExit: boolean | null;
  stoppedAt: string | null;
  liveParallel: number | null;
}

/** GET /api/config/cluster — every area server with its heartbeat state.
 *  The heartbeat is the DB row (the server's own ~60s beat — the SAME
 *  liveness signal the watchdog reads), NOT an HTTP probe, so a down
 *  server answers instantly instead of timing out per row. */
router.get("/cluster", wrapAsync(async (_req: Request, res: Response) => {
  const areas = readClusterAreas();
  const view = renderSqlView("data_viz/api/routes/config/cluster_heartbeats");
  const beats = await getReadPool().query<BeatRow>(view.sql, view.params);
  const byService = new Map(beats.rows.map((r) => [r.service, r]));

  const rows: ClusterServerStatus[] = areas.map(({ area, port }) => {
    const baseUrl = `http://127.0.0.1:${port}`;
    const service = `server_cluster.${area}`;
    const beat = byService.get(service);
    if (beat == null) {
      return {
        area,
        service,
        port,
        baseUrl,
        docsUrl: `${baseUrl}/docs`,
        status: "down",
        pid: null,
        startedAt: null,
        beatAt: null,
        beatAgeS: null,
        stopReason: null,
        isNormalExit: null,
        stoppedAt: null,
        liveParallel: 0,
      };
    }
    const status: ClusterServerStatus["status"] =
      beat.stop_reason != null
        ? "stopped"
        : beat.beat_age_s <= STALE_AFTER_S
          ? "live"
          : "stale";
    return {
      area,
      service,
      port,
      baseUrl,
      docsUrl: `${baseUrl}/docs`,
      status,
      pid: beat.pid,
      startedAt: beat.started_at.toISOString(),
      beatAt: beat.beat_at.toISOString(),
      beatAgeS: beat.beat_age_s,
      stopReason: beat.stop_reason,
      isNormalExit: beat.is_normal_exit,
      stoppedAt: beat.stopped_at ? beat.stopped_at.toISOString() : null,
      liveParallel: beat.live_parallel,
    };
  });

  res.json({
    success: true,
    generatedAt: new Date().toISOString(),
    staleAfterS: STALE_AFTER_S,
    rows,
  });
}));

// ---------------------------------------------------------------------------
//  Per-server API surface — the FastAPI openapi.json parse
// ---------------------------------------------------------------------------

/** The HTTP methods an OpenAPI path item can carry, in display order. */
const OPENAPI_METHODS = [
  "get",
  "post",
  "put",
  "patch",
  "delete",
  "options",
  "head",
  "trace",
] as const;

/** One exposed endpoint of an area server. */
export interface ClusterApiOperation {
  method: string;
  path: string;
  summary: string;
}

/** Endpoints grouped by their FastAPI router tag ("" = untagged). */
export interface ClusterApiGroup {
  tag: string;
  operations: ClusterApiOperation[];
}

export interface ClusterOpenApiSurface {
  success: true;
  area: string;
  title: string;
  version: string;
  docsUrl: string;
  /** Total endpoint count across the groups. */
  count: number;
  groups: ClusterApiGroup[];
}

/** A fetched /openapi.json — only the fields the parse walks; anything
 *  else in the document is ignored. */
interface OpenApiDoc {
  info?: { title?: unknown; version?: unknown };
  paths?: Record<string, Record<string, unknown> | undefined>;
}

/** Fetch ONE server's /openapi.json with a hard timeout — a down server
 *  must fail fast (the heartbeat column already says down; the surface
 *  fetch only happens when a row is expanded). */
async function fetchOpenApiDoc(
  baseUrl: string,
  area: string,
): Promise<OpenApiDoc> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 4000);
  try {
    const res = await fetch(`${baseUrl}/openapi.json`, {
      signal: controller.signal,
    });
    if (!res.ok) {
      throw new HttpError(
        502,
        "UPSTREAM_FAILED",
        `${area} server answered HTTP ${res.status} on /openapi.json`,
      );
    }
    return (await res.json()) as OpenApiDoc;
  } catch (e) {
    if (e instanceof HttpError) throw e;
    const why =
      e instanceof Error && e.name === "AbortError"
        ? "timed out"
        : e instanceof Error
          ? e.message
          : String(e);
    throw new HttpError(
      502,
      "UPSTREAM_FAILED",
      `cluster server ${area} at ${baseUrl} unreachable (${why}) — its heartbeat row says whether it should be up`,
    );
  } finally {
    clearTimeout(timer);
  }
}

/** Collapse the openapi paths object into tag-grouped operations (the
 *  same grouping the /docs page shows), sorted by path then method. */
function toSurface(
  area: string,
  docsUrl: string,
  doc: OpenApiDoc,
): ClusterOpenApiSurface {
  const groups = new Map<string, ClusterApiOperation[]>();
  const str = (v: unknown): string => (typeof v === "string" ? v : "");
  for (const [p, item] of Object.entries(doc.paths ?? {})) {
    if (item == null) continue;
    for (const method of OPENAPI_METHODS) {
      const op = item[method];
      if (typeof op !== "object" || op == null) continue;
      const tags = (op as { tags?: unknown }).tags;
      const tag =
        Array.isArray(tags) && typeof tags[0] === "string" ? tags[0] : "";
      const summary = str((op as { summary?: unknown }).summary);
      const list = groups.get(tag) ?? [];
      list.push({ method, path: p, summary });
      groups.set(tag, list);
    }
  }
  const sortedGroups = [...groups.entries()]
    .map(([tag, operations]) => ({
      tag: tag === "" ? "untagged" : tag,
      operations: operations.sort(
        (a, b) => a.path.localeCompare(b.path) || a.method.localeCompare(b.method),
      ),
    }))
    .sort((a, b) => a.tag.localeCompare(b.tag));
  return {
    success: true,
    area,
    title: str(doc.info?.title),
    version: str(doc.info?.version),
    docsUrl,
    count: sortedGroups.reduce((n, g) => n + g.operations.length, 0),
    groups: sortedGroups,
  };
}

/** GET /api/config/cluster/:area/openapi — ONE server's exposed API
 *  surface (its FastAPI openapi.json, tag-grouped). 502 when the server
 *  is unreachable — the UI renders that as the row's expand state. */
router.get(
  "/cluster/:area/openapi",
  wrapAsync(async (req: Request, res: Response) => {
    const area = req.params.area ?? "";
    const entry = readClusterAreas().find((a) => a.area === area);
    if (entry == null) {
      throw HttpError.notFound(`unknown cluster area '${area}'`);
    }
    const baseUrl = `http://127.0.0.1:${entry.port}`;
    const doc = await fetchOpenApiDoc(baseUrl, area);
    res.json(toSurface(area, `${baseUrl}/docs`, doc));
  }),
);

export default router;
