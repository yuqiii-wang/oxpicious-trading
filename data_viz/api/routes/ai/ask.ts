/**
 * Ask routes — POST /ask (the interactive chart question) and the
 * persisted ask-history reads (GET /ask-calendar, /ask-items, /ask-qa,
 * /ask-image). Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import { HttpError, intParam, sendError, wrapAsync } from "../../lib/http.js";
import { askLlm } from "../../services/ai/ai-ask.service.js";
import {
  getAskHistoryDetail,
  getAskHistoryImage,
  listAskHistoryCalendar,
  listAskHistoryItems,
} from "../../services/ai/ai-ask-history.service.js";
import { industryIds, str } from "./_shared.js";

const router = Router();

// ---------------------------------------------------------------------------
// Ask history — the persisted interactive asks (AiPage "QA by Ask" toggle).
// Same filter params as the knowledge-base routes above, plus `code` (a
// picked security filters the ask's primary code directly).
// ---------------------------------------------------------------------------

router.get("/ask-calendar", wrapAsync(async (req: Request, res: Response) => {
  res.json(
    await listAskHistoryCalendar({
      sectorId: str(req.query.sector_id) ?? null,
      industryId: str(req.query.industry_id) ?? null,
      industryIds: industryIds(req.query.industry_ids),
      code: str(req.query.code) ?? null,
      search: str(req.query.search) ?? null,
    }),
  );
}));

router.get("/ask-items", wrapAsync(async (req: Request, res: Response) => {
  res.json(
    await listAskHistoryItems({
      sectorId: str(req.query.sector_id) ?? null,
      industryId: str(req.query.industry_id) ?? null,
      industryIds: industryIds(req.query.industry_ids),
      code: str(req.query.code) ?? null,
      search: str(req.query.search) ?? null,
      date: str(req.query.date) ?? null,
      dateFrom: str(req.query.date_from) ?? null,
      dateTo: str(req.query.date_to) ?? null,
      limit: intParam(req, "limit") ?? null,
      offset: intParam(req, "offset") ?? null,
    }),
  );
}));

// GET /api/ai/ask-qa?ask_id= — full ask row + keywords + image list.
router.get("/ask-qa", wrapAsync(async (req: Request, res: Response) => {
  const askId = Number(req.query.ask_id);
  if (!Number.isFinite(askId) || askId <= 0) throw HttpError.missingParam("ask_id");
  const row = await getAskHistoryDetail(askId);
  if (row === null) throw HttpError.notFound(`ask_id ${askId} not found`);
  res.json(row);
}));

// GET /api/ai/ask-image?image_id= — one screenshot's bytes (the feed card's
// thumbnail / zoom source). Cached client-side by the browser via the
// immutable-by-id URL.
router.get("/ask-image", wrapAsync(async (req: Request, res: Response) => {
  const imageId = Number(req.query.image_id);
  if (!Number.isFinite(imageId) || imageId <= 0) throw HttpError.missingParam("image_id");
  const image = await getAskHistoryImage(imageId);
  if (image === null) throw HttpError.notFound(`image_id ${imageId} not found`);
  res.set("Content-Type", image.mime_type);
  res.set("Cache-Control", "private, max-age=86400");
  res.send(image.data);
}));

// POST /api/ai/ask — chart question → llm_agents.llm_ask (via WSL).
router.post("/ask", wrapAsync(async (req: Request, res: Response) => {
  const question =
    typeof req.body?.question === "string" ? req.body.question.trim() : "";
  const plotInfo =
    typeof req.body?.plotInfo === "object" && req.body.plotInfo !== null
      ? (req.body.plotInfo as Record<string, unknown>)
      : null;
  if (!question) throw HttpError.missingParam("question");
  if (!plotInfo) throw HttpError.missingParam("plotInfo");
  const screenshots =
    Array.isArray(req.body?.screenshots) && req.body.screenshots.every((s: unknown) => typeof s === "string")
      ? (req.body.screenshots as string[])
      : undefined;
  const themeMode =
    typeof req.body?.themeMode === "string" ? req.body.themeMode : undefined;
  const onlineSearch = req.body?.onlineSearch === true;
  // The modal's online-search line — meaningful only on the online-search
  // path (the plain adviser ignores it; the service passes it through to
  // python, which falls back to the question when absent/blank).
  const searchQuery =
    typeof req.body?.searchQuery === "string" && req.body.searchQuery.trim() !== ""
      ? req.body.searchQuery
      : undefined;

  const out = await askLlm({ question, plotInfo, screenshots, themeMode, onlineSearch, searchQuery });
  if (!out.success) {
    sendError(res, 500, out.stderrTail ?? "llm_ask failed", "UPSTREAM_FAILED");
    return;
  }
  res.json({ success: true, answer: out.answer, model: out.model });
}));

export default router;
