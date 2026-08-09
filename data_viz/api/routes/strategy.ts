/**
 * Strategy API routes.
 *
 *  GET /api/strategy/ma-spread/backtest?sec_type=index&code=000970
 *    Runs the MA-spread crossover backtest for one security and returns
 *    OHLC + trading amount series + trade decisions + total return.
 *    Ephemeral (no DB write) — for UI exploration.
 *
 *  GET /api/strategy/ma-spread/risks?sec_type=index&code=000970
 *    Reads pre-computed risk metrics (concentration, drawdown, per-period
 *    gain/loss) from strategy.strategy_risk_seq + strategy_risk_period.
 *    Computed by `python -m strategy._risks`.
 */
import { Router, type Request, type Response } from "express";
import { runMaSpreadBacktest, fetchStrategyRisks } from "../services/strategy.service.js";
import type { MaSpreadSecType } from "../../shared/types.js";

const router = Router();

const VALID_SEC_TYPES = new Set(["etf", "index", "stock"]);

router.get("/ma-spread/backtest", async (req: Request, res: Response) => {
  try {
    const code = typeof req.query.code === "string" ? req.query.code.trim() : "";
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    const rawSecType = typeof req.query.sec_type === "string"
      ? req.query.sec_type.trim().toLowerCase() : "index";
    if (!VALID_SEC_TYPES.has(rawSecType)) {
      res.status(400).json({ error: `Invalid sec_type: ${rawSecType}. Expected etf/index/stock.` });
      return;
    }
    const result = await runMaSpreadBacktest(code, rawSecType as MaSpreadSecType);
    res.json(result);
  } catch (err) {
    console.error("[strategy/ma-spread/backtest] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/ma-spread/risks", async (req: Request, res: Response) => {
  try {
    const code = typeof req.query.code === "string" ? req.query.code.trim() : "";
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    const rawSecType = typeof req.query.sec_type === "string"
      ? req.query.sec_type.trim().toLowerCase() : "index";
    if (!VALID_SEC_TYPES.has(rawSecType)) {
      res.status(400).json({ error: `Invalid sec_type: ${rawSecType}. Expected etf/index/stock.` });
      return;
    }
    const result = await fetchStrategyRisks(code, rawSecType as MaSpreadSecType);
    res.json(result);
  } catch (err) {
    console.error("[strategy/ma-spread/risks] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export default router;
