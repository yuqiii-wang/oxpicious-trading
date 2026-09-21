/**
 * AI API routes — read access to the LLM Q&A knowledge base in the text
 * schema (text.llm_qa / text.llm_qa_refs / text.news_digestions, written by
 * llm_agents) and to the persisted interactive ask history
 * (text.llm_qa_by_ask family + multi_media.src_images, written by
 * llm_agents.llm_ask.storage). Mirrors the news routes' design:
 *
 *   GET /api/ai/calendar?sector_id=&industry_id=&search=
 *     Per-day Q&A counts for the date-event strip's dots; co-filtered by
 *     the classification scope + keyword search so the bar reacts to the
 *     nav, exactly like /api/news/calendar. Inactive rows never surface.
 *
 *   GET /api/ai/items?sector_id=&industry_id=&search=&date=&date_from=&date_to=&limit=&offset=
 *     One page of Q&A rows for the same filters (+ optional single-day pick
 *     from the date strip, or an inclusive date_from/date_to range window
 *     around the pick). answer is truncated to a 240-char snippet; the
 *     full row comes from /qa below.
 *
 *   GET /api/ai/qa?qa_id=
 *     Full Q&A row (untruncated answer + context) with its per-ref
 *     resolutions joined back to the text.news articles and the average
 *     digestion sentiment across the refs — the feed card's click-to-expand
 *     fetch (mirrors /api/news/item).
 *
 *   POST /api/ai/ask
 *     Interactive chart question (the AI Ask "?" beside chart titles):
 *     { question, plotInfo, screenshots?, themeMode, onlineSearch?,
 *     searchQuery? } → spawns llm_agents.llm_ask (chart-adviser mode) via
 *     the WSL py-runner (payload by temp file — see
 *     services/ai-ask.service.ts) and returns { success, answer, model }.
 *     Screenshots ride along as images when the model has image input
 *     (vision config on the python side); onlineSearch=true routes through
 *     the online-search agent instead (cited [来源：ref_N] answer,
 *     text-only). Every ask is persisted server-side (python side,
 *     fail-soft) into the ask-history tables below.
 *
 * Ask history (the AiPage "QA by Ask" toggle — same filter params, plus a
 * picked code filters llm_qa_by_ask.code directly; search terms hit
 * question/answer OR the derived keyword rows):
 *
 *   GET /api/ai/ask-calendar?sector_id=&industry_id=&code=&search=
 *   GET /api/ai/ask-items?...&date=&date_from=&date_to=&limit=&offset=
 *   GET /api/ai/ask-qa?ask_id=           — full row + keywords + image list
 *   GET /api/ai/ask-image?image_id=      — the screenshot bytes themselves
 */
import { Router, type Request, type Response } from "express";
import { askLlm } from "../services/ai-ask.service.js";
import { getAiQa, listAiCalendar, listAiItems } from "../services/ai.service.js";
import {
  getAskHistoryDetail,
  getAskHistoryImage,
  listAskHistoryCalendar,
  listAskHistoryItems,
} from "../services/ai-ask-history.service.js";

const router = Router();

function str(v: unknown): string | undefined {
  return typeof v === "string" && v.trim() !== "" ? v.trim() : undefined;
}

/** industry_ids list — comma-joined. PRESENT (even empty) scopes to exactly
 *  that set (empty → no rows); ABSENT leaves the scope to the other params. */
function industryIds(v: unknown): string[] | null {
  if (v === undefined) return null;
  return String(v).split(",").map((s) => s.trim()).filter(Boolean);
}

router.get("/calendar", async (req: Request, res: Response) => {
  try {
    res.json(
      await listAiCalendar({
        sectorId: str(req.query.sector_id) ?? null,
        industryId: str(req.query.industry_id) ?? null,
        industryIds: industryIds(req.query.industry_ids),
        search: str(req.query.search) ?? null,
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
