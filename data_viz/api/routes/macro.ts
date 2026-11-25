/**
 * Macro API routes (precious metals — gold/silver daily bars).
 */
import { Router, type Request, type Response } from "express";
import { wrapAsync, strParam } from "../lib/http.js";
import { getMacroDaily } from "../services/market/macro.service.js";

const router = Router();

router.get("/", wrapAsync(async (req: Request, res: Response) => {
  const query = {
    start_date: strParam(req, "start_date"),
    end_date: strParam(req, "end_date"),
  };
  const data = await getMacroDaily(query);
  res.json(data);
}));

export default router;
