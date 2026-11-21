/**
 * GET /cautions + POST /cautions (register) + POST /cautions/resolve —
 * the cross-service caution trail (monitor.caution_log, the Monitor
 * UI's Cautions tab). Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import { HttpError, intParam, wrapAsync } from "../../lib/http.js";
import { getReadPool } from "../../services/infra/db.service.js";
import { renderSqlView, sqlFrag } from "@api/lib/sql-views";
import {
  encodeLabelParam,
  labelDecodeSql,
  LABEL_CODE,
} from "../../services/infra/label-codes.js";
import {
  recordCaution,
  resolveCaution,
} from "../../services/caution-log.service.js";

const router = Router();

interface CautionRow {
  id: string;
  service: string;
  source: number;
  source_label: string | null;
  severity_level: number;
  severity_label: string | null;
  title: string;
  detail: unknown;
  dedupe_key: string;
  occurrence_count: number;
  is_resolved: boolean;
  created_at: Date;
  last_seen_at: Date;
  resolved_timestamp: Date | null;
}

export interface CautionEntry {
  id: number;
  service: string;
  source: number;
  sourceLabel: string;
  severity: number;
  severityLabel: string;
  title: string;
  detail: Record<string, unknown>;
  dedupeKey: string;
  occurrenceCount: number;
  isResolved: boolean;
  createdAt: string;
  lastSeenAt: string;
  resolvedTimestamp: string | null;
}

export interface CautionsResponse {
  success: true;
  generatedAt: string;
  state: "open" | "resolved" | "all";
  rows: CautionEntry[];
}

/** GET /api/monitor/cautions?state=open|resolved|all&source=&severity=&limit=
 *  — the caution trail, OPEN-first ordering (open before resolved, worst
 *  severity first, most-recent-hit first). source/severity arrive
 *  DECODED (caution_source / caution_severity; decode at the API
 *  boundary, the label-code contract). Reads the replica. */
router.get("/cautions", wrapAsync(async (req: Request, res: Response) => {
  const stateRaw = String(req.query.state ?? "open");
  const state = stateRaw === "resolved" || stateRaw === "all"
    ? (stateRaw as "resolved" | "all")
    : "open";
  const sourceRaw = req.query.source == null || req.query.source === ""
    ? null
    : encodeLabelParam("caution_source", String(req.query.source));
  if (sourceRaw === null && req.query.source != null && req.query.source !== "") {
    throw new HttpError(
      400, "INVALID_PARAM", "query param 'source' must be service | ai | manual");
  }
  const severityRaw = req.query.severity == null || req.query.severity === ""
    ? null
    : encodeLabelParam("caution_severity", String(req.query.severity));
  if (severityRaw === null && req.query.severity != null && req.query.severity !== "") {
    throw new HttpError(
      400, "INVALID_PARAM",
      "query param 'severity' must be info | warning | error | critical");
  }
  const limit = intParam(req, "limit", { default: 200, clamp: [1, 500] });
  const pool = getReadPool();
  const { sql, params } = renderSqlView(
    "data_viz/api/routes/monitor/caution_rows",
    {
      state,
      source: sourceRaw,
      severity: severityRaw,
      limit,
      source_label: sqlFrag(labelDecodeSql("caution_source", "source")),
      severity_label: sqlFrag(labelDecodeSql("caution_severity", "severity_level")),
    },
  );
  const agg = await pool.query<CautionRow>(sql, params);
  const rows: CautionEntry[] = agg.rows.map((r) => ({
    id: Number(r.id),
    service: r.service,
    source: r.source,
    sourceLabel: r.source_label ?? String(r.source),
    severity: r.severity_level,
    severityLabel: r.severity_label ?? String(r.severity_level),
    title: r.title,
    detail: (typeof r.detail === "object" && r.detail !== null
      ? r.detail
      : {}) as Record<string, unknown>,
    dedupeKey: r.dedupe_key,
    occurrenceCount: r.occurrence_count,
    isResolved: r.is_resolved,
    createdAt: r.created_at.toISOString(),
    lastSeenAt: r.last_seen_at.toISOString(),
    resolvedTimestamp: r.resolved_timestamp?.toISOString() ?? null,
  }));
  const body: CautionsResponse = {
    success: true,
    generatedAt: new Date().toISOString(),
    state,
    rows,
  };
  res.json(body);
}));

/** POST /cautions request body — source/severity as label NAMES
 *  (caution_source / caution_severity), the same vocabulary the Python
 *  CLI (`python -m _common.caution_log`) validates against. */
export interface RegisterCautionBody {
  service: string;
  title: string;
  severity: string;
  source?: string;
  dedupeKey?: string;
  detail?: Record<string, unknown>;
  /** How to locate the issue in the logs — { file, keyword, timeFrom,
   *  timeTo }; stored under detail.log_context (missing time bounds are
   *  stamped with the record time) and rendered by the Cautions tab as
   *  the locate-in-logs line. */
  logContext?: {
    file?: string;
    keyword?: string;
    timeFrom?: string;
    timeTo?: string;
  };
}

/** POST /api/monitor/cautions — register ONE caution from any writer
 *  that cannot go DB-direct (ops scripts, non-Python services, sandboxed
 *  code; Python services keep using `_common.caution_log` DB-direct).
 *  Optional body 'logContext' { file, keyword, timeFrom, timeTo } rides
 *  into detail.log_context so the Cautions tab can point at the exact
 *  log file + keyword + time range to locate the error.
 *  Same upsert + dedupe contract as every other writer: the row dedupes
 *  on (service, dedupeKey) over OPEN rows — a recurrence bumps
 *  occurrence_count / last_seen_at instead of inserting. Writes the
 *  primary through the caution-log service (the sql write action
 *  data_viz/api/caution_log/upsert). */
router.post("/cautions", wrapAsync(async (req: Request, res: Response) => {
  const body = (req.body ?? {}) as RegisterCautionBody;
  const service = typeof body.service === "string" ? body.service.trim() : "";
  if (!service) {
    throw new HttpError(400, "INVALID_PARAM", "body 'service' must be a non-empty string");
  }
  const title = typeof body.title === "string" ? body.title.trim() : "";
  if (!title) {
    throw new HttpError(400, "INVALID_PARAM", "body 'title' must be a non-empty string");
  }
  const severity = body.severity;
  if (
    typeof severity !== "string" ||
    !(severity in LABEL_CODE.caution_severity)
  ) {
    throw new HttpError(
      400, "INVALID_PARAM",
      "body 'severity' must be info | warning | error | critical");
  }
  const source = body.source == null || body.source === ""
    ? "service"
    : body.source;
  if (typeof source !== "string" || !(source in LABEL_CODE.caution_source)) {
    throw new HttpError(
      400, "INVALID_PARAM", "body 'source' must be service | ai | manual");
  }
  if (
    body.detail != null &&
    (typeof body.detail !== "object" || Array.isArray(body.detail))
  ) {
    throw new HttpError(400, "INVALID_PARAM", "body 'detail' must be a JSON object");
  }
  const logContext = body.logContext;
  if (logContext != null) {
    if (typeof logContext !== "object" || Array.isArray(logContext)) {
      throw new HttpError(
        400, "INVALID_PARAM", "body 'logContext' must be a JSON object");
    }
    for (const key of ["file", "keyword", "timeFrom", "timeTo"] as const) {
      const v = logContext[key];
      if (v != null && typeof v !== "string") {
        throw new HttpError(
          400, "INVALID_PARAM",
          `body 'logContext.${key}' must be a string (file path / grep keyword / ISO time bound)`);
      }
    }
  }
  const dedupeKey = body.dedupeKey == null || body.dedupeKey === ""
    ? title
    : String(body.dedupeKey);
  const recorded = await recordCaution({
    service,
    source: source as keyof (typeof LABEL_CODE)["caution_source"],
    severity: severity as keyof (typeof LABEL_CODE)["caution_severity"],
    title,
    dedupeKey,
    detail: body.detail,
    logContext,
  });
  if (!recorded) {
    throw new HttpError(
      500, "INTERNAL", "the caution write failed (see API logs)");
  }
  res.json({ success: true, service, dedupeKey });
}));

/** POST /api/monitor/cautions/resolve { id } — resolve ONE open row
 *  (is_resolved = true, resolved_timestamp = now(); a resolved row is
 *  history — a recurrence re-raises as a NEW row). Writes the primary
 *  through the caution-log service (the sql write action
 *  data_viz/api/caution_log/resolve). */
router.post("/cautions/resolve", wrapAsync(async (req: Request, res: Response) => {
  const id = Number(req.body?.id);
  if (!Number.isInteger(id) || id <= 0) {
    throw new HttpError(400, "INVALID_PARAM", "body 'id' must be a positive integer");
  }
  const resolved = await resolveCaution(id);
  if (!resolved) {
    throw new HttpError(404, "NOT_FOUND", `no open caution row with id ${id}`);
  }
  res.json({ success: true, id, resolved: true });
}));

export default router;
