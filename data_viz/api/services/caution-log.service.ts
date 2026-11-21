/**
 * caution-log.service.ts — the data_viz API's face of the cross-service
 * caution trail (monitor.caution_log, database/sql/monitoring/
 * caution_log.sql): any API-side error / suspicion lands as one row the
 * Monitor UI's Cautions tab shows and resolves.
 *
 * Writers here (the Python twin is _common/caution_log.py — every
 * pipeline exit hook, orchestration step failure, maintainer job crash
 * and AI-suspicion seam writes through that one):
 *   - the central error middleware in api/app.ts (severity error,
 *     deduped on method+path+error class);
 *   - services/infra/py-runner.service.ts (cluster rejection, area
 *     unreachable — the child-process failures themselves are covered
 *     Python-side by the service_heartbeat stop hook);
 *   - services/ai/ai-summary.service.ts (the deep-agents daemon
 *     unreachable / non-OK).
 *
 * NEVER-THROW contract: a caution write is observability — a DB outage
 * or a schema drift must not turn the audited error into a second one.
 * recordCaution swallows every failure behind a console.error; the
 * label codes come from the caution_source / caution_severity domains
 * (label-codes.ts, the label-code contract's mirrors).
 */
import { LABEL_CODE } from "./infra/label-codes.js";
import { queryRows } from "./infra/db.service.js";
import { loadSqlWrite } from "./infra/sql-writes.service.js";

/** One caution entry crossing the service boundary — source/severity as
 *  label NAMES (validated against the domains; a bad name drops the
 *  caution rather than writing an undecodable row). */
export interface CautionInput {
  service: string;
  source: keyof (typeof LABEL_CODE)["caution_source"];
  severity: keyof (typeof LABEL_CODE)["caution_severity"];
  title: string;
  dedupeKey: string;
  detail?: Record<string, unknown>;
  /** How to locate this issue in the logs (the Cautions tab renders it
   *  as the locate-in-logs line): the log FILE (repo-relative, e.g.
   *  "logs/2026-11-20/builds.index.log"), a grep KEYWORD and the TIME
   *  RANGE to narrow to. Missing timeFrom/timeTo are stamped with the
   *  record time — the moment to search around. */
  logContext?: CautionLogContext;
}

/** Log-locating context for one caution — stored under detail.log_context
 *  (snake_case, the Python twin's payload shape: file / keyword /
 *  time_from / time_to). Every field optional; an all-empty context is
 *  dropped rather than stored as noise. */
export interface CautionLogContext {
  file?: string;
  keyword?: string;
  timeFrom?: string;
  timeTo?: string;
}

/** Normalize one log context into the stored snake_case shape, stamping
 *  missing time bounds with the record time (ISO). Returns null when
 *  nothing locatable survives (no file, no keyword, no times). */
function stampLogContext(ctx: CautionLogContext): Record<string, string> | null {
  const out: Record<string, string> = {};
  if (typeof ctx.file === "string" && ctx.file.trim() !== "") {
    out.file = ctx.file.trim();
  }
  if (typeof ctx.keyword === "string" && ctx.keyword.trim() !== "") {
    out.keyword = ctx.keyword.trim();
  }
  const now = new Date().toISOString();
  if (typeof ctx.timeFrom === "string" && ctx.timeFrom.trim() !== "") {
    out.time_from = ctx.timeFrom.trim();
  }
  if (typeof ctx.timeTo === "string" && ctx.timeTo.trim() !== "") {
    out.time_to = ctx.timeTo.trim();
  }
  // The record time is the anchor when a bound was left open — the
  // Cautions tab narrows its log view around it.
  if (out.file != null || out.keyword != null) {
    out.time_from ??= now;
    out.time_to ??= now;
  }
  return Object.keys(out).length > 0 ? out : null;
}

/** Enqueue-quality truncation caps (the Python side enforces the same
 *  shapes — titles/keys stay table-row sized, detail stays a tail). */
const TITLE_MAX = 300;
const DEDUPE_MAX = 300;
const DETAIL_MAX_CHARS = 16000;

/** Upsert one caution row (deduped on (service, dedupeKey) over open
 *  rows). Resolves even on total failure (false) — never throws. */
export async function recordCaution(entry: CautionInput): Promise<boolean> {
  try {
    const source = LABEL_CODE.caution_source[entry.source];
    const severity = LABEL_CODE.caution_severity[entry.severity];
    if (source == null || severity == null) {
      throw new Error(
        `unknown caution label (source=${entry.source}, severity=${entry.severity})`,
      );
    }
    let detail = entry.detail ?? {};
    // The dedicated logContext field wins over a writer-stuffed
    // detail.log_context (one sanctioned way to set it from the API).
    const stamped = entry.logContext == null ? null : stampLogContext(entry.logContext);
    if (stamped != null) {
      detail = { ...detail, log_context: stamped };
    }
    if (JSON.stringify(detail).length > DETAIL_MAX_CHARS) {
      detail = { truncated: true, title: entry.title.slice(0, TITLE_MAX), keys: Object.keys(detail).slice(0, 20) };
    }
    await queryRows(
      loadSqlWrite("data_viz", "api", "caution_log", "upsert"),
      [
        entry.service.slice(0, TITLE_MAX),
        source,
        severity,
        entry.title.slice(0, TITLE_MAX),
        JSON.stringify(detail),
        (entry.dedupeKey || entry.title).slice(0, DEDUPE_MAX),
      ],
    );
    return true;
  } catch (err) {
    console.error("[caution-log] record failed (dropped):", err);
    return false;
  }
}

/** Resolve one open row (stamps resolved_timestamp). Returns whether a
 *  row flipped — false for an unknown id or an already-resolved row
 *  (resolve never un-resolves; a recurrence opens a fresh row). */
export async function resolveCaution(id: number): Promise<boolean> {
  const rows = await queryRows<{ id: string | number }>(
    loadSqlWrite("data_viz", "api", "caution_log", "resolve"),
    [id],
  );
  return rows.length > 0;
}
