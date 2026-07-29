/**
 * Debt Baseline API routes.
 */
import { Router, type Request, type Response } from "express";
import { getDebtBaseline, getPbocOmaAnnouncements } from "../services/debt-baseline.service.js";

const router = Router();

router.get("/", async (req: Request, res: Response) => {
  try {
    const query = {
      start_date: typeof req.query.start_date === "string" ? req.query.start_date : undefined,
      end_date: typeof req.query.end_date === "string" ? req.query.end_date : undefined,
    };
    const data = await getDebtBaseline(query);
    res.json(data);
  } catch (err) {
    console.error("[debt-baseline] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** PBoC Open Market Announcements (公开市场业务公告) — policy notices timeline. */
router.get("/oma", async (_req: Request, res: Response) => {
  try {
    const data = await getPbocOmaAnnouncements();
    res.json(data);
  } catch (err) {
    console.error("[debt-baseline/oma] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export default router;
