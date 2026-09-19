/**
 * Sec board API routes — board tag data for the code-trend header.
 */
import { Router, type Request, type Response } from "express";
import {
  getSecBoardMap,
  type SecBoardSecType,
} from "../services/sec-board.service.js";

const router = Router();

const SEC_TYPES: ReadonlySet<string> = new Set(["etf", "index", "stock"]);

/** GET /api/sec-board?sec_type=etf&code=510050.SS
 *  Board tag(s) for one security: a stock returns its single listing
 *  board; an ETF/index returns the board mix of its latest composition
 *  snapshot (pct DESC). Empty `boards` when no data exists. */
router.get("/", async (req: Request, res: Response) => {
  try {
    const code = typeof req.query.code === "string" ? req.query.code : "";
    const secType =
      typeof req.query.sec_type === "string" ? req.query.sec_type : "";
    if (!code || !SEC_TYPES.has(secType)) {
      res.status(400).json({ error: "Missing or invalid 'code' / 'sec_type' parameter" });
      return;
    }
    res.json(await getSecBoardMap(secType as SecBoardSecType, code));
  } catch (err) {
    console.error("[sec-board] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export default router;
