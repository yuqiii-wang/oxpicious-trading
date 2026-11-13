/**
 * Sec board API routes — board tag data for the code-trend header.
 */
import { Router, type Request, type Response } from "express";
import { wrapAsync, strParam, HttpError } from "../lib/http.js";
import {
  getSecBoardMap,
  type SecBoardSecType,
} from "../services/sec/sec-board.service.js";

const router = Router();

const SEC_TYPES: ReadonlySet<string> = new Set(["etf", "index", "stock"]);

/** GET /api/sec-board?sec_type=etf&code=510050.SS
 *  Board tag(s) for one security: a stock returns its single listing
 *  board; an ETF/index returns the board mix of its latest composition
 *  snapshot (pct DESC). Empty `boards` when no data exists. */
router.get("/", wrapAsync(async (req: Request, res: Response) => {
  const code = strParam(req, "code");
  const secType = strParam(req, "sec_type");
  if (!code || !secType || !SEC_TYPES.has(secType)) {
    throw new HttpError(
      400,
      "MISSING_PARAM",
      "Missing or invalid 'code' / 'sec_type' parameter",
    );
  }
  res.json(await getSecBoardMap(secType as SecBoardSecType, code));
}));

export default router;
