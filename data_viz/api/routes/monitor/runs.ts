/**
 * GET /stream/runs + GET /stream/runs/log — one pipeline process's run
 * audit history and one run's component log tail.
 * Full route docs: see index.ts.
 */

import { existsSync } from "fs";
import { readFile, readdir } from "fs/promises";
import path from "path";
import { Router, type Request, type Response } from "express";
import { HttpError, intParam, strParam, wrapAsync } from "../../lib/http.js";
import {
  getReadPool,
} from "../../services/infra/db.service.js";
import { renderSqlView } from "@api/lib/sql-views";
import { isRunnableModule, REPO_ROOT } from "./_shared.js";

const router = Router();

interface AuditLifeRow {
  id: string;
  service: string;
  scope: string;
  sub_scope: string | null;
  pid: number;
  started_at: Date;
  ended_at: Date | null;
  exit_reason: string | null;
  is_normal_exit: boolean | null;
}

/** GET /api/monitor/stream/runs?service=...&limit=10 — run audit history
 *  of ONE pipeline process (the pipeline table's expandable rows). Full
 *  per-service history with its own limit — the UI dropped the global
 *  recent-runs table because the shared window crowded each service out
 *  (~50 rows across ~30 registered services ≈ 2 runs each). */
router.get("/stream/runs", wrapAsync(async (req: Request, res: Response) => {
  const service = strParam(req, "service", { required: true });
  const limit = intParam(req, "limit", { default: 10, clamp: [1, 100] });
  const pool = getReadPool();
  const { sql, params } = renderSqlView(
    "data_viz/api/routes/monitor/stream_runs",
    { service, limit },
  );
  const rows = await pool.query<AuditLifeRow>(sql, params);
  res.json({
    success: true,
    service,
    runs: rows.rows.map((r) => ({
      id: r.id,
      service: r.service,
      scope: r.scope,
      subScope: r.sub_scope,
      pid: r.pid,
      startedAt: r.started_at.toISOString(),
      endedAt: r.ended_at ? r.ended_at.toISOString() : null,
      exitReason: r.exit_reason,
      isNormalExit: r.is_normal_exit,
    })),
  });
}));

// ---------------------------------------------------------------------------
//  Run logs — GET /stream/runs/log?service=…&date=YYYY-MM-DD
//
//  One pipeline run's log: _common/log_setup writes ONE file per
//  (date, service) — logs/<local start date>/<service>.log, the dotted
//  service name — so the direct lookup is just the service path. Dates
//  before that convention kept per-component file names, so a miss falls
//  back to parsing candidate component names from the service's module
//  source (the audit row does not record them).
// ---------------------------------------------------------------------------

/** LEGACY candidate log component names for ONE service (dates before the
 *  per-service file naming), parsed from its module source in priority
 *  order: import-time setup_logger(...) names (these keyed the legacy
 *  file), then the entry declarations (DataPipeline `component` attrs,
 *  setup_logging(...) names) — __main__.py before the rest of the
 *  package (capped); the dotted name's last segment is the final
 *  fallback. */
async function logComponentCandidates(service: string): Promise<string[]> {
  const rel = service.split(".").join(path.sep);
  const modFile = `${path.join(REPO_ROOT, rel)}.py`;
  const modDir = path.join(REPO_ROOT, rel);
  const mainFiles: string[] = [];
  const otherFiles: string[] = [];
  const collectPy = async (dir: string, isModuleRoot: boolean): Promise<void> => {
    let entries;
    try {
      entries = await readdir(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      const p = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name !== "__pycache__") await collectPy(p, false);
      } else if (entry.name.endsWith(".py")) {
        if (isModuleRoot && entry.name === "__main__.py") mainFiles.push(p);
        else otherFiles.push(p);
      }
    }
  };
  if (existsSync(modFile)) mainFiles.push(modFile);
  if (existsSync(modDir)) await collectPy(modDir, true);

  // First declaration per pattern wins — the process component is declared
  // once at the entry point; deeper files carry internal-step loggers.
  // setup_logger names outrank the entry declarations: pre-refactor the
  // FILE was keyed by the first setup call in process import order, and
  // for the downloader packages that is a package-level setup_logger(...)
  // in a _config.py imported at the top of __main__ — the entry's own
  // setup_logging(...) name only became the logger prefix. Without this
  // priority a generic entry name (composition) steals another service's
  // file (builds.index.composition's composition.log) on shared dates.
  const rxComponent = /\bcomponent(?:\s*:\s*\w+)?\s*=\s*"([^"]+)"/;
  const rxSetup = /setup_logging\(\s*"([^"]+)"/;
  const rxSetupLogger = /setup_logger\(\s*"([^"]+)"/;
  const importTimeNames: string[] = [];
  const entryNames: string[] = [];
  const collect = async (files: string[], cap?: number): Promise<void> => {
    for (const f of files.slice(0, cap ?? files.length)) {
      try {
        const src = await readFile(f, "utf8");
        const first = (rx: RegExp, into: string[]): void => {
          const m = rx.exec(src);
          if (m && m[1] !== "" && !into.includes(m[1])) into.push(m[1]);
        };
        first(rxSetupLogger, importTimeNames);
        first(rxComponent, entryNames);
        first(rxSetup, entryNames);
      } catch {
        // unreadable source — skip the file
      }
    }
  };
  await collect(mainFiles);
  await collect(otherFiles, 60);
  const names = [...importTimeNames, ...entryNames];
  const last = service.split(".").at(-1);
  if (last && last !== "" && !names.includes(last)) names.push(last);
  return names;
}

/** GET /api/monitor/stream/runs/log?service=…&date=YYYY-MM-DD[&tail=] —
 *  the tail of ONE service's log for ONE local date: the service file
 *  _common/log_setup wrote (logs/<date>/<service>.log), falling back to
 *  the legacy per-component names for pre-refactor dates. `file` is
 *  relative to the repo root; a huge log arrives truncated to the last
 *  `tail` lines with `totalLines` / `sizeBytes` for the notice. */
router.get("/stream/runs/log", wrapAsync(async (req: Request, res: Response) => {
  const service = strParam(req, "service") ?? "";
  const date = strParam(req, "date") ?? "";
  const tail = intParam(req, "tail", { default: 2000, clamp: [50, 20000] });
  if (!service || !/^[A-Za-z0-9_.-]+$/.test(service) || !isRunnableModule(service)) {
    throw new HttpError(
      400,
      "INVALID_PARAM",
      `service must be a runnable dotted module path, got '${service}'`,
    );
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    throw new HttpError(
      400,
      "MISSING_PARAM",
      "query param 'date' (YYYY-MM-DD) is required",
    );
  }
  const dayDir = path.join(REPO_ROOT, "logs", date);
  // The direct per-service file first (one date one service one log
  // file); the legacy per-component candidate scan only on a miss.
  let file: string | null = null;
  let candidates: string[] = [];
  const direct = path.join(dayDir, `${service}.log`);
  if (existsSync(direct)) {
    file = direct;
    candidates = [service];
  } else {
    candidates = await logComponentCandidates(service);
    for (const c of candidates) {
      const f = path.join(dayDir, `${c}.log`);
      if (existsSync(f)) {
        file = f;
        break;
      }
    }
  }
  if (file == null) {
    res.json({
      success: true,
      service,
      date,
      component: null,
      file: null,
      candidates,
      found: false,
      content: "",
      truncated: false,
      totalLines: 0,
      sizeBytes: 0,
    });
    return;
  }
  const component = path.basename(file, ".log");
  const raw = await readFile(file, "utf8");
  const sizeBytes = Buffer.byteLength(raw, "utf8");
  const lines = raw.split("\n");
  if (lines.at(-1) === "") lines.pop();
  const totalLines = lines.length;
  const truncated = totalLines > tail;
  const content = (truncated ? lines.slice(-tail) : lines).join("\n");
  res.json({
    success: true,
    service,
    date,
    component,
    file: path.relative(REPO_ROOT, file).split(path.sep).join("/"),
    candidates,
    found: true,
    content,
    truncated,
    totalLines,
    sizeBytes,
  });
}));

export default router;
