/**
 * Plan-template routes — GET/POST /plan-templates and DELETE
 * /plan-templates/:templateId (the saved deep-agent plan templates).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import { HttpError, wrapAsync } from "../../lib/http.js";
import {
  deletePlanTemplate,
  listPlanTemplates,
  savePlanTemplate,
} from "../../services/ai/ai-plan-template.service.js";

const router = Router();

// ---- Saved plan templates (llm.deep_agent_plan_templates — a finished
// run's plan JSON, placeholder-templated so a later generate can start
// from it and skip the LLM planning node; see
// services/ai-plan-template.service.ts) ----

// GET /api/ai/plan-templates — the generate form's picker rows (name
// order, each carrying the placeholders its plan templated).
router.get("/plan-templates", wrapAsync(async (_req: Request, res: Response) => {
  res.json(await listPlanTemplates());
}));

// POST /api/ai/plan-templates {threadId, name} — the Thread Graph Save
// button: template the settled run's kind 'plan' context payload and
// upsert under the name (UNIQUE — reusing a name overwrites). 400 when
// the thread has no plan or the name is unusable.
router.post("/plan-templates", wrapAsync(async (req: Request, res: Response) => {
  const threadId =
    typeof req.body?.threadId === "string" ? req.body.threadId.trim() : "";
  const name = typeof req.body?.name === "string" ? req.body.name : "";
  if (threadId === "") throw HttpError.missingParam("threadId");
  const out = await savePlanTemplate(threadId, name);
  if (out.ok === false) {
    throw new HttpError(400, "INVALID_PARAM", out.error);
  }
  res.json(out.template);
}));

// DELETE /api/ai/plan-templates/:templateId — remove one saved template;
// 404 when no row had the id (a run in flight that named it keeps its
// already-materialized plan — the template is only read at plan time).
router.delete(
  "/plan-templates/:templateId",
  wrapAsync(async (req: Request, res: Response) => {
    const templateId = Number(req.params.templateId);
    if (!Number.isInteger(templateId) || templateId < 1) {
      throw new HttpError(400, "INVALID_PARAM", "templateId must be a positive integer");
    }
    const out = await deletePlanTemplate(templateId);
    if (out === "missing") {
      throw HttpError.notFound(`plan template ${templateId} not found`);
    }
    res.json({ deleted: true });
  }),
);

export default router;
