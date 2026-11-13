/**
 * Research Q&A routes — GET /research-calendar, GET /research-items,
 * GET /research-qa (the research-logic knowledge base reads), with the
 * researchLogicCode label-name decoder. Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import { HttpError, intParam, wrapAsync } from "../../lib/http.js";
import {
  getAiResearchQa,
  listAiResearchCalendar,
  listAiResearchItems,
} from "../../services/ai/ai-research.service.js";
import { encodeLabelParam } from "../../services/infra/label-codes.js";
import { industryIds, str } from "./_shared.js";

const router = Router();

// ---------------------------------------------------------------------------
// Research Q&A — the research-logic knowledge base (AiPage "Research"
// toggle). Same filter params as the knowledge-base routes; logic_label is
// a label NAME (growth/…) encoded to its research_logic code here — an
// unknown name is a 400 (the monitor.ts encode precedent).
// ---------------------------------------------------------------------------

/** logic_label query param -> research_logic code, or null (absent);
 *  an unknown name throws the standard 400. */
function researchLogicCode(raw: unknown): number | null {
  const name = str(raw);
  if (name === undefined) return null; // absent — all labels
  const code = encodeLabelParam("research_logic", name);
  if (code === null) {
    throw new HttpError(400, "INVALID_PARAM", `Unknown 'logic_label': ${name}`);
  }
  return code;
}

router.get("/research-calendar", wrapAsync(async (req: Request, res: Response) => {
  const logicLabel = researchLogicCode(req.query.logic_label);
  res.json(
    await listAiResearchCalendar({
      sectorId: str(req.query.sector_id) ?? null,
      industryId: str(req.query.industry_id) ?? null,
      industryIds: industryIds(req.query.industry_ids),
      search: str(req.query.search) ?? null,
      logicLabel,
    }),
  );
}));

router.get("/research-items", wrapAsync(async (req: Request, res: Response) => {
  const logicLabel = researchLogicCode(req.query.logic_label);
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
      limit: intParam(req, "limit") ?? null,
      offset: intParam(req, "offset") ?? null,
    }),
  );
}));

// GET /api/ai/research-qa?qa_id= — summary + four dimensions + sources.
router.get("/research-qa", wrapAsync(async (req: Request, res: Response) => {
  const qaId = Number(req.query.qa_id);
  if (!Number.isFinite(qaId) || qaId <= 0) throw HttpError.missingParam("qa_id");
  const row = await getAiResearchQa(qaId);
  if (row === null) throw HttpError.notFound(`qa_id ${qaId} not found`);
  res.json(row);
}));

export default router;
