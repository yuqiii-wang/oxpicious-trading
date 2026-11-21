/**
 * News Audit routes (/news-audit*) — the 5-day provider/content audits of
 * the news corpus, including the fixed process-id-tag for the run
 * endpoint. Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import { getNewsProviderAudit, getNewsContentAudit } from "../../services/analysis/index.js";
import { runPythonModule } from "../../services/infra/py-runner.service.js";
import { wrapAsync, strParam } from "../../lib/http.js";

const router = Router();

// ---- News Audit (composite analysis — drives the Composites page).
//   GET /api/analysis/news-audit/providers?limit=8&source=
//     Returns NewsProviderAuditResponse: one row per (5-day window,
//     origin) over the most recent `limit` distinct windows — items /
//     digested / fake share / avg sentiment / avg word count / industry
//     spread, joined with the registry's is_blocked + seen-date span.
//     `source` (optional) filters to one provider; empty = all.
//   GET /api/analysis/news-audit/content?limit=8&industry_id=
//     Returns NewsContentAuditResponse: the content twin per industry
//     ("" = untagged), plus distinct keywords hit / total mentions /
//     articles with hits. `industry_id` (optional) filters; empty = all.
//   POST /api/analysis/news-audit/run
//     Spawns `python -m analysis.news_audit` (incremental: registry sync
//     + missing/trailing/grace windows) via the shared WSL py-runner and
//     WAITS for it to exit. Deduped by a fixed process-id-tag; running
//     state polled via GET /api/analysis/run-analysis/status with
//     NEWS_AUDIT_RUN_TAG.
router.get("/news-audit/providers", wrapAsync(async (req: Request, res: Response) => {
  res.json(await getNewsProviderAudit(strParam(req, "limit"), strParam(req, "source")));
}));

router.get("/news-audit/content", wrapAsync(async (req: Request, res: Response) => {
  res.json(await getNewsContentAudit(strParam(req, "limit"), strParam(req, "industry_id")));
}));

/** Fixed process-id-tag for UI-triggered news-audit runs — one refresh at
 *  a time globally (must match the tag built client-side). */
export const NEWS_AUDIT_RUN_TAG = "analysis-run:news_audit";

router.post("/news-audit/run", wrapAsync(async (_req: Request, res: Response) => {
  console.log("[analysis/news-audit/run] python -m analysis.news_audit");
  const result = await runPythonModule("analysis.news_audit", [], {
    processIdTag: NEWS_AUDIT_RUN_TAG,
  });
  res.json({
    success: result.success,
    already_running: result.already_running === true,
    process_id_tag: NEWS_AUDIT_RUN_TAG,
    stdout_tail: result.stdout.slice(-2000),
    stderr_tail: result.stderr.slice(-2000),
  });
}));

export default router;
