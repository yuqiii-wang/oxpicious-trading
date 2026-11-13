/**
 * Security baseline bundle route — the shared data path of the shared
 * baseline-plot component.
 *
 * GET /api/sec-baseline?sec_type=index&code=000300
 *   Returns SecBaselineResponse: { sec_type, code, bundle } where bundle is
 *   the IndexBundle | EtfBundle | StockBundle matching sec_type (null when
 *   the code has no rows). Consumers: useSecBaselineBundle (PE page,
 *   Dividends page, …) — one URL per (sec_type, code) instead of the three
 *   page-scoped combined browse endpoints.
 */
import { Router, type Request, type Response } from "express";
import { wrapAsync, strParam, HttpError } from "../lib/http.js";
import { getSecBaselineBundle } from "../services/sec/sec-baseline.service.js";

const router = Router();

const VALID_SEC_TYPES = new Set(["index", "etf", "stock", "industry"]);

router.get("/", wrapAsync(async (req: Request, res: Response) => {
  const secType = (strParam(req, "sec_type") ?? "").toLowerCase();
  if (!VALID_SEC_TYPES.has(secType)) {
    throw new HttpError(
      400,
      "INVALID_PARAM",
      `Invalid or missing 'sec_type': ${secType}. Expected 'index', 'etf', 'stock', or 'industry'.`,
    );
  }
  const code = strParam(req, "code", { required: true });
  const bundle = await getSecBaselineBundle(
    secType as "index" | "etf" | "stock" | "industry", code);
  res.json({ sec_type: secType, code, bundle });
}));

export default router;
