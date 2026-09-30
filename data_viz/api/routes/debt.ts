/**
 * Debt API routes.
 */
import { Router, type Request, type Response } from "express";
import { getDebtDaily, getPbocOmaAnnouncements } from "../services/debt.service.js";

const router = Router();

router.get("/", async (req: Request, res: Response) => {
  try {
    const query = {
      start_date: typeof req.query.start_date === "string" ? req.query.start_date : undefined,
      end_date: typeof req.query.end_date === "string" ? req.query.end_date : undefined,
    };
    const data = await getDebtDaily(query);
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
