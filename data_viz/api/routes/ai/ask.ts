/**
 * Ask routes — POST /ask (the interactive chart question) and the
 * persisted ask-history reads (GET /ask-calendar, /ask-items, /ask-qa,
 * /ask-image). Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import { askLlm } from "../../services/ai-ask.service.js";
import {
  getAskHistoryDetail,
  getAskHistoryImage,
  listAskHistoryCalendar,
  listAskHistoryItems,
} from "../../services/ai-ask-history.service.js";
import { industryIds, str } from "./_shared.js";

const router = Router();

// ---------------------------------------------------------------------------
// Ask history — the persisted interactive asks (AiPage "QA by Ask" toggle).
// Same filter params as the knowledge-base routes above, plus `code` (a
// picked security filters the ask's primary code directly).
// ---------------------------------------------------------------------------

router.get("/ask-calendar", async (req: Request, res: Response) => {
  try {
    res.json(
      await listAskHistoryCalendar({
        sectorId: str(req.query.sector_id) ?? null,
        industryId: str(req.query.industry_id) ?? null,
        industryIds: industryIds(req.query.industry_ids),
        code: str(req.query.code) ?? null,
        search: str(req.query.search) ?? null,
      }),
    );
  } catch (err) {
    console.error("[ai/ask-calendar] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/ask-items", async (req: Request, res: Response) => {
  try {
    const limit = Number(req.query.limit);
    const offset = Number(req.query.offset);
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
        limit: Number.isFinite(limit) ? limit : null,
        offset: Number.isFinite(offset) ? offset : null,
      }),
    );
  } catch (err) {
    console.error("[ai/ask-items] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// GET /api/ai/ask-qa?ask_id= — full ask row + keywords + image list.
router.get("/ask-qa", async (req: Request, res: Response) => {
  try {
    const askId = Number(req.query.ask_id);
    if (!Number.isFinite(askId) || askId <= 0) {
      res.status(400).json({ error: "Missing 'ask_id'" });
      return;
    }
    const row = await getAskHistoryDetail(askId);
    if (row === null) {
      res.status(404).json({ error: `ask_id ${askId} not found` });
      return;
    }
    res.json(row);
  } catch (err) {
    console.error("[ai/ask-qa] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// GET /api/ai/ask-image?image_id= — one screenshot's bytes (the feed card's
// thumbnail / zoom source). Cached client-side by the browser via the
// immutable-by-id URL.
router.get("/ask-image", async (req: Request, res: Response) => {
  try {
    const imageId = Number(req.query.image_id);
    if (!Number.isFinite(imageId) || imageId <= 0) {
      res.status(400).json({ error: "Missing 'image_id'" });
      return;
    }
    const image = await getAskHistoryImage(imageId);
    if (image === null) {
      res.status(404).json({ error: `image_id ${imageId} not found` });
      return;
    }
    res.set("Content-Type", image.mime_type);
    res.set("Cache-Control", "private, max-age=86400");
    res.send(image.data);
  } catch (err) {
    console.error("[ai/ask-image] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// POST /api/ai/ask — chart question → llm_agents.llm_ask (via WSL).
router.post("/ask", async (req: Request, res: Response) => {
  try {
    const question =
      typeof req.body?.question === "string" ? req.body.question.trim() : "";
    const plotInfo =
      typeof req.body?.plotInfo === "object" && req.body.plotInfo !== null
        ? (req.body.plotInfo as Record<string, unknown>)
        : null;
    if (!question) {
      res.status(400).json({ success: false, error: "Missing 'question'" });
      return;
    }
    if (!plotInfo) {
      res.status(400).json({ success: false, error: "Missing 'plotInfo'" });
      return;
    }
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
      res.status(500).json({ success: false, error: out.stderrTail ?? "llm_ask failed" });
      return;
    }
    res.json({ success: true, answer: out.answer, model: out.model });
  } catch (err) {
    console.error("[ai/ask] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

export default router;
