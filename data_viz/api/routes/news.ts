/**
 * News API routes — read access to the text schema (populated by builds.text).
 *
 *   GET /api/news/themes
 *     Sector → industry tree (SectorNode[]) with news counts per industry.
 *     Industry level only — items[] is always empty (no L3 security level).
 *
 *   GET /api/news/calendar?sector_id=&industry_id=&search=&start=&end=
 *     Per-day news counts for the date bar's dots; co-filtered by the
 *     classification scope + keyword search so the bar reacts to the nav.
 *
 *   GET /api/news/items?sector_id=&industry_id=&search=&date=&limit=&offset=
 *     One page of articles for the same filters (+ optional single-day pick).
 */
import { Router, type Request, type Response } from "express";
import {
  listNewsAuthors,
  listNewsCalendar,
  listNewsItems,
  listNewsStrategyThemes,
  listNewsThemes,
} from "../services/news.service.js";

const router = Router();

function str(v: unknown): string | undefined {
  return typeof v === "string" && v.trim() !== "" ? v.trim() : undefined;
}

router.get("/themes", async (req: Request, res: Response) => {
  try {
    res.json(await listNewsThemes(str(req.query.source) ?? null, str(req.query.author) ?? null));
  } catch (err) {
    console.error("[news/themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/strategy-themes", async (req: Request, res: Response) => {
  try {
    res.json(await listNewsStrategyThemes(str(req.query.source) ?? null, str(req.query.author) ?? null));
  } catch (err) {
    console.error("[news/strategy-themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/authors", async (req: Request, res: Response) => {
  try {
    res.json(
      await listNewsAuthors({
        sectorId: str(req.query.sector_id) ?? null,
        industryId: str(req.query.industry_id) ?? null,
        search: str(req.query.search) ?? null,
        source: str(req.query.source) ?? null,
      }),
    );
  } catch (err) {
    console.error("[news/authors] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/calendar", async (req: Request, res: Response) => {
  try {
    res.json(
      await listNewsCalendar({
        sectorId: str(req.query.sector_id) ?? null,
        industryId: str(req.query.industry_id) ?? null,
        search: str(req.query.search) ?? null,
        source: str(req.query.source) ?? null,
        author: str(req.query.author) ?? null,
        start: str(req.query.start) ?? null,
        end: str(req.query.end) ?? null,
      }),
    );
  } catch (err) {
    console.error("[news/calendar] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/items", async (req: Request, res: Response) => {
  try {
    const limit = Number(req.query.limit);
    const offset = Number(req.query.offset);
    res.json(
      await listNewsItems({
        sectorId: str(req.query.sector_id) ?? null,
        industryId: str(req.query.industry_id) ?? null,
        search: str(req.query.search) ?? null,
        source: str(req.query.source) ?? null,
        author: str(req.query.author) ?? null,
        date: str(req.query.date) ?? null,
        limit: Number.isFinite(limit) ? limit : null,
        offset: Number.isFinite(offset) ? offset : null,
      }),
    );
  } catch (err) {
    console.error("[news/items] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export default router;
