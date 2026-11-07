/**
 * Knowledge-base read routes — GET /calendar, GET /items, GET /qa
 * (the llm_qa feed's date-strip dots, item page and expand-card fetch).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import { getAiQa, listAiCalendar, listAiItems } from "../../services/ai.service.js";
import { industryIds, str } from "./_shared.js";

const router = Router();

/** Boolean flag query param — "1" (URLSearchParams-friendly) or "true"
 *  (String(boolean) from the api-client's qs). Anything else = off. */
function bool(v: unknown): boolean {
  return v === "1" || v === "true";
}

router.get("/calendar", async (req: Request, res: Response) => {
  try {
    res.json(
      await listAiCalendar({
        sectorId: str(req.query.sector_id) ?? null,
        industryId: str(req.query.industry_id) ?? null,
        industryIds: industryIds(req.query.industry_ids),
        search: str(req.query.search) ?? null,
        bigFluctuationOnly: bool(req.query.is_big_fluctuation),
      }),
    );
  } catch (err) {
    console.error("[ai/calendar] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/items", async (req: Request, res: Response) => {
  try {
    const limit = Number(req.query.limit);
    const offset = Number(req.query.offset);
    res.json(
      await listAiItems({
        sectorId: str(req.query.sector_id) ?? null,
        industryId: str(req.query.industry_id) ?? null,
        industryIds: industryIds(req.query.industry_ids),
        search: str(req.query.search) ?? null,
        bigFluctuationOnly: bool(req.query.is_big_fluctuation),
        date: str(req.query.date) ?? null,
        dateFrom: str(req.query.date_from) ?? null,
        dateTo: str(req.query.date_to) ?? null,
        limit: Number.isFinite(limit) ? limit : null,
        offset: Number.isFinite(offset) ? offset : null,
      }),
    );
  } catch (err) {
    console.error("[ai/items] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// GET /api/ai/qa?qa_id= — full Q&A row + refs (the expand-card fetch).
router.get("/qa", async (req: Request, res: Response) => {
  try {
    const qaId = Number(req.query.qa_id);
    if (!Number.isFinite(qaId) || qaId <= 0) {
      res.status(400).json({ error: "Missing 'qa_id'" });
      return;
    }
    const row = await getAiQa(qaId);
    if (row === null) {
      res.status(404).json({ error: `qa_id ${qaId} not found` });
      return;
    }
    res.json(row);
  } catch (err) {
    console.error("[ai/qa] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export default router;
