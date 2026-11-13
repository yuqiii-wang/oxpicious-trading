/**
 * Debt API routes.
 */
import { Router, type Request, type Response } from "express";
import { wrapAsync, strParam } from "../lib/http.js";
import { getDebtDaily, getPbocOmaAnnouncements } from "../services/market/debt.service.js";

const router = Router();

router.get("/", wrapAsync(async (req: Request, res: Response) => {
  const query = {
    start_date: strParam(req, "start_date"),
    end_date: strParam(req, "end_date"),
  };
  const data = await getDebtDaily(query);
  res.json(data);
}));

/** PBoC Open Market Announcements (公开市场业务公告) — policy notices timeline. */
router.get("/oma", wrapAsync(async (_req: Request, res: Response) => {
  const data = await getPbocOmaAnnouncements();
  res.json(data);
}));

export default router;
