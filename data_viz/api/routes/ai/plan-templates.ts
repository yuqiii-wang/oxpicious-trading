/**
 * Plan-template routes — GET/POST /plan-templates and DELETE
 * /plan-templates/:templateId (the saved deep-agent plan templates).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  deletePlanTemplate,
  listPlanTemplates,
  savePlanTemplate,
} from "../../services/ai-plan-template.service.js";

const router = Router();

// ---- Saved plan templates (llm.deep_agent_plan_templates — a finished
// run's plan JSON, placeholder-templated so a later generate can start
// from it and skip the LLM planning node; see
// services/ai-plan-template.service.ts) ----

// GET /api/ai/plan-templates — the generate form's picker rows (name
// order, each carrying the placeholders its plan templated).
router.get("/plan-templates", async (_req: Request, res: Response) => {
  try {
    res.json(await listPlanTemplates());
  } catch (err) {
    console.error("[ai/plan-templates] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// POST /api/ai/plan-templates {threadId, name} — the Thread Graph Save
// button: template the settled run's kind 'plan' context payload and
// upsert under the name (UNIQUE — reusing a name overwrites). 400 when
// the thread has no plan or the name is unusable.
router.post("/plan-templates", async (req: Request, res: Response) => {
  try {
    const threadId =
      typeof req.body?.threadId === "string" ? req.body.threadId.trim() : "";
    const name = typeof req.body?.name === "string" ? req.body.name : "";
    if (threadId === "") {
      res.status(400).json({ error: "Missing 'threadId'" });
      return;
    }
    const out = await savePlanTemplate(threadId, name);
    if (out.ok === false) {
      res.status(400).json({ error: out.error });
      return;
    }
    res.json(out.template);
  } catch (err) {
    console.error("[ai/plan-templates save] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// DELETE /api/ai/plan-templates/:templateId — remove one saved template;
// 404 when no row had the id (a run in flight that named it keeps its
// already-materialized plan — the template is only read at plan time).
router.delete(
  "/plan-templates/:templateId",
  async (req: Request, res: Response) => {
    try {
      const templateId = Number(req.params.templateId);
      if (!Number.isInteger(templateId) || templateId < 1) {
        res.status(400).json({ error: "templateId must be a positive integer" });
        return;
      }
      const out = await deletePlanTemplate(templateId);
      if (out === "missing") {
        res.status(404).json({ error: `plan template ${templateId} not found` });
        return;
      }
      res.json({ deleted: true });
    } catch (err) {
      console.error("[ai/plan-templates delete] error:", err);
      res.status(500).json({ error: String(err) });
    }
  },
);

export default router;
