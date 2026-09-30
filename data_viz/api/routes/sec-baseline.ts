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
import { getSecBaselineBundle } from "../services/sec-baseline.service.js";

const router = Router();

const VALID_SEC_TYPES = new Set(["index", "etf", "stock"]);

router.get("/", async (req: Request, res: Response) => {
  try {
    const secType = typeof req.query.sec_type === "string"
      ? req.query.sec_type.trim().toLowerCase()
      : "";
    const code = typeof req.query.code === "string" ? req.query.code.trim() : "";
    if (!VALID_SEC_TYPES.has(secType)) {
      res.status(400).json({
        error: `Invalid or missing 'sec_type': ${secType}. Expected 'index', 'etf', or 'stock'.`,
      });
      return;
    }
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    const bundle = await getSecBaselineBundle(secType as "index" | "etf" | "stock", code);
    res.json({ sec_type: secType, code, bundle });
  } catch (err) {
    console.error("[sec-baseline] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export default router;
