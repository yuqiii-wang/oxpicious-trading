/**
 * Knowledge-base read routes — GET /calendar, GET /items, GET /qa
 * (the llm_qa feed's date-strip dots, item page and expand-card fetch).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import { boolParam, HttpError, intParam, wrapAsync } from "../../lib/http.js";
import { getAiQa, listAiCalendar, listAiItems } from "../../services/ai/ai.service.js";
import { industryIds, str } from "./_shared.js";

const router = Router();

router.get("/calendar", wrapAsync(async (req: Request, res: Response) => {
  res.json(
    await listAiCalendar({
      sectorId: str(req.query.sector_id) ?? null,
      industryId: str(req.query.industry_id) ?? null,
      industryIds: industryIds(req.query.industry_ids),
      search: str(req.query.search) ?? null,
      bigFluctuationOnly: boolParam(req, "is_big_fluctuation") ?? false,
    }),
  );
}));

router.get("/items", wrapAsync(async (req: Request, res: Response) => {
  res.json(
    await listAiItems({
      sectorId: str(req.query.sector_id) ?? null,
      industryId: str(req.query.industry_id) ?? null,
      industryIds: industryIds(req.query.industry_ids),
      search: str(req.query.search) ?? null,
      bigFluctuationOnly: boolParam(req, "is_big_fluctuation") ?? false,
      date: str(req.query.date) ?? null,
      dateFrom: str(req.query.date_from) ?? null,
      dateTo: str(req.query.date_to) ?? null,
      limit: intParam(req, "limit") ?? null,
      offset: intParam(req, "offset") ?? null,
    }),
  );
}));

// GET /api/ai/qa?qa_id= — full Q&A row + refs (the expand-card fetch).
router.get("/qa", wrapAsync(async (req: Request, res: Response) => {
  const qaId = Number(req.query.qa_id);
  if (!Number.isFinite(qaId) || qaId <= 0) throw HttpError.missingParam("qa_id");
  const row = await getAiQa(qaId);
  if (row === null) throw HttpError.notFound(`qa_id ${qaId} not found`);
  res.json(row);
}));

export default router;
