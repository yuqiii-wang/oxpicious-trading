/**
 * Strategy API routes.
 *
 *  GET /api/strategy/ma-spread/backtest?sec_type=index&code=000970
 *    Reads pre-computed backtest results from the DB (strategy_identity +
 *    strategy_results + trade_decision) and pairs them with OHLC/MA series.
 *
 *  GET /api/strategy/ma-spread/risks?sec_type=index&code=000970
 *    Reads pre-computed risk metrics (concentration, drawdown, per-period
 *    gain/loss) from strategy.strategy_risks + strategy_risk_period.
 *    Computed by `python -m strategy._risks`.
 *
 *  POST /api/strategy/ma-spread/run?sec_type=index&code=000970
 *    Spawns the Python backtest (`strategy.ma_spread_trading`) + risk
 *    computation (`strategy._risks`) via WSL, waits for both to exit, then
 *    returns success/failure. The frontend reloads data from DB on success.
 */
import { Router, type Request, type Response } from "express";
import {
  runMaSpreadBacktest,
  fetchStrategyRisks,
  runStrategyScript,
} from "../services/strategy.service.js";
import type { MaSpreadSecType } from "../../shared/types.js";

const router = Router();

const VALID_SEC_TYPES = new Set(["etf", "index", "stock"]);

function parseParams(req: Request) {
  const code = typeof req.query.code === "string" ? req.query.code.trim() : "";
  const rawSecType = typeof req.query.sec_type === "string"
    ? req.query.sec_type.trim().toLowerCase() : "index";
  return { code, rawSecType };
}

router.get("/ma-spread/backtest", async (req: Request, res: Response) => {
  try {
    const { code, rawSecType } = parseParams(req);
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
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
    const { code, rawSecType } = parseParams(req);
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
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

router.post("/ma-spread/run", async (req: Request, res: Response) => {
  try {
    const { code, rawSecType } = parseParams(req);
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    if (!VALID_SEC_TYPES.has(rawSecType)) {
      res.status(400).json({ error: `Invalid sec_type: ${rawSecType}. Expected etf/index/stock.` });
      return;
    }
    const result = await runStrategyScript(code, rawSecType as MaSpreadSecType);
    res.json(result);
  } catch (err) {
    console.error("[strategy/ma-spread/run] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export default router;
