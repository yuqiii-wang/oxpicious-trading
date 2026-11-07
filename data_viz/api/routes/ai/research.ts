/**
 * Research Q&A routes — GET /research-calendar, GET /research-items,
 * GET /research-qa (the research-logic knowledge base reads), with the
 * researchLogicCode label-name decoder. Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  getAiResearchQa,
  listAiResearchCalendar,
  listAiResearchItems,
} from "../../services/ai-research.service.js";
import { encodeLabelParam } from "../../services/label-codes.js";
import { industryIds, str } from "./_shared.js";

const router = Router();

// ---------------------------------------------------------------------------
// Research Q&A — the research-logic knowledge base (AiPage "Research"
// toggle). Same filter params as the knowledge-base routes; logic_label is
// a label NAME (growth/…) encoded to its research_logic code here — an
// unknown name is a 400 (the monitor.ts encode precedent).
// ---------------------------------------------------------------------------

/** logic_label query param -> research_logic code, or null (absent). */
function researchLogicCode(
  res: Response,
  raw: unknown,
): number | null | undefined {
  const name = str(raw);
  if (name === undefined) return null; // absent — all labels
  const code = encodeLabelParam("research_logic", name);
  if (code === null) {
    res.status(400).json({ error: `Unknown 'logic_label': ${name}` });
    return undefined; // signals the handler to stop
  }
  return code;
}

router.get("/research-calendar", async (req: Request, res: Response) => {
  try {
    const logicLabel = researchLogicCode(res, req.query.logic_label);
    if (logicLabel === undefined) return;
    res.json(
      await listAiResearchCalendar({
        sectorId: str(req.query.sector_id) ?? null,
        industryId: str(req.query.industry_id) ?? null,
        industryIds: industryIds(req.query.industry_ids),
        search: str(req.query.search) ?? null,
        logicLabel,
      }),
    );
  } catch (err) {
    console.error("[ai/research-calendar] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/research-items", async (req: Request, res: Response) => {
  try {
    const limit = Number(req.query.limit);
    const offset = Number(req.query.offset);
    const logicLabel = researchLogicCode(res, req.query.logic_label);
    if (logicLabel === undefined) return;
    res.json(
      await listAiResearchItems({
        sectorId: str(req.query.sector_id) ?? null,
        industryId: str(req.query.industry_id) ?? null,
        industryIds: industryIds(req.query.industry_ids),
        search: str(req.query.search) ?? null,
        logicLabel,
        date: str(req.query.date) ?? null,
        dateFrom: str(req.query.date_from) ?? null,
        dateTo: str(req.query.date_to) ?? null,
        limit: Number.isFinite(limit) ? limit : null,
        offset: Number.isFinite(offset) ? offset : null,
      }),
    );
  } catch (err) {
    console.error("[ai/research-items] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// GET /api/ai/research-qa?qa_id= — summary + four dimensions + sources.
router.get("/research-qa", async (req: Request, res: Response) => {
  try {
    const qaId = Number(req.query.qa_id);
    if (!Number.isFinite(qaId) || qaId <= 0) {
      res.status(400).json({ error: "Missing 'qa_id'" });
      return;
    }
    const row = await getAiResearchQa(qaId);
    if (row === null) {
      res.status(404).json({ error: `qa_id ${qaId} not found` });
      return;
    }
    res.json(row);
  } catch (err) {
    console.error("[ai/research-qa] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export default router;
