/**
 * Strategy API routes.
 *
 *  GET /api/strategy/singleton/backtest?sec_type=index&code=000970
 *    Reads pre-computed backtest results from the DB (strategy_identity +
 *    strategy_results + trade_decision) and pairs them with OHLC/MA series.
 *
 *  GET /api/strategy/singleton/risks?sec_type=index&code=000970
 *    Reads pre-computed risk metrics (concentration, drawdown, per-period
 *    gain/loss) from strategy.strategy_risks + strategy_risk_period.
 *    Computed by `python -m strategy._risks`.
 *
 *  POST /api/strategy/singleton/run?sec_type=index&code=000970
 *    Spawns the Python backtest (`strategy.singleton_trading`) + risk
 *    computation (`strategy._risks`) via WSL, waits for both to exit, then
 *    returns success/failure. The frontend reloads data from DB on success.
 */
import { Router, type Request, type Response } from "express";
import {
  runSingletonBacktest,
  fetchStrategyRisks,
  runStrategyScript,
  fetchStrategyForecast1m,
  fetchForecastScenarioDecisions,
  DEFAULT_STRATEGY_NAME,
} from "../services/strategy.service.js";
import type { MaSpreadSecType } from "../../shared/types.js";

const router = Router();

const VALID_SEC_TYPES = new Set(["etf", "index", "stock"]);

function parseParams(req: Request) {
  const code = typeof req.query.code === "string" ? req.query.code.trim() : "";
  const rawSecType = typeof req.query.sec_type === "string"
    ? req.query.sec_type.trim().toLowerCase() : "index";
  // Optional scenario query param: when provided, fetch the child seq for
  // that forecast scenario (e.g. mir_255d_std_scale, flip_255d_std_scale, ...). When absent/empty,
  // fetch the parent seq (actual backtest only, no forecast).
  const scenario = typeof req.query.scenario === "string"
    ? req.query.scenario.trim() || null : null;
  // strategy_name query param: the DB strategy_name to filter on. This is
  // EITHER an algo name (bollinger_bands / macd / ma_spread — binary mode)
  // OR a portfolio name (portfolio:bb*0.5+macd*0.5 — mixed mode). Falls back
  // to the legacy `algo` query param for backward compat, then to the default
  // (macd). No validation — any string the frontend resolved is passed
  // through to SQL as the strategy_name filter.
  const rawStrategyName = typeof req.query.strategy_name === "string"
    ? req.query.strategy_name.trim()
    : (typeof req.query.algo === "string" ? req.query.algo.trim().toLowerCase() : "");
  const strategyName = rawStrategyName || DEFAULT_STRATEGY_NAME;
  return { code, rawSecType, scenario, strategyName };
}

router.get("/singleton/backtest", async (req: Request, res: Response) => {
  try {
    const { code, rawSecType, scenario, strategyName } = parseParams(req);
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    if (!VALID_SEC_TYPES.has(rawSecType)) {
      res.status(400).json({ error: `Invalid sec_type: ${rawSecType}. Expected etf/index/stock.` });
      return;
    }
    const result = await runSingletonBacktest(code, rawSecType as MaSpreadSecType, scenario, strategyName);
    res.json(result);
  } catch (err) {
    console.error("[strategy/singleton/backtest] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/singleton/risks", async (req: Request, res: Response) => {
  try {
    const { code, rawSecType, scenario, strategyName } = parseParams(req);
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    if (!VALID_SEC_TYPES.has(rawSecType)) {
      res.status(400).json({ error: `Invalid sec_type: ${rawSecType}. Expected etf/index/stock.` });
      return;
    }
    const result = await fetchStrategyRisks(code, rawSecType as MaSpreadSecType, scenario, strategyName);
    res.json(result);
  } catch (err) {
    console.error("[strategy/singleton/risks] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.post("/singleton/run", async (req: Request, res: Response) => {
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
    // forecast toggle: default true. When false, passes --no-forecast to
    // skip the 1-month forecast (10 scenarios + child seqs) for faster runs.
    const forecast = req.query.forecast !== "false";
    // algo query param: serialized selection like "bollinger_bands:0.5,macd:0.5"
    // (understood by Python's _parse_algo_arg). Falls back to the default
    // binary algo (macd) when absent.
    const algo = typeof req.query.algo === "string" && req.query.algo.trim()
      ? req.query.algo.trim() : DEFAULT_STRATEGY_NAME;
    const result = await runStrategyScript(code, rawSecType as MaSpreadSecType, forecast, algo);
    res.json(result);
  } catch (err) {
    console.error("[strategy/singleton/run] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// 1-month forward sell-confidence forecast (7 sigma scenarios + mean).
// GET /api/strategy/singleton/forecast?sec_type=index&code=000036&strategy_name=macd
router.get("/singleton/forecast", async (req: Request, res: Response) => {
  try {
    const { code, rawSecType, strategyName } = parseParams(req);
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    if (!VALID_SEC_TYPES.has(rawSecType)) {
      res.status(400).json({ error: `Invalid sec_type: ${rawSecType}. Expected etf/index/stock.` });
      return;
    }
    const result = await fetchStrategyForecast1m(code, rawSecType as MaSpreadSecType, strategyName);
    res.json(result);
  } catch (err) {
    console.error("[strategy/singleton/forecast] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// Lightweight forecast-only decisions endpoint for scenario switching.
// GET /api/strategy/singleton/forecast-decisions?sec_type=index&code=000922&scenario=flip_255d_std_scale&strategy_name=macd
// Returns ONLY the 20 forecast SELL decisions from the child seq + the child's
// summary. The UI merges these with the cached parent backtest to avoid a
// full OHLC/actual-decisions reload when switching forecast scenarios.
router.get("/singleton/forecast-decisions", async (req: Request, res: Response) => {
  try {
    const { code, rawSecType, scenario, strategyName } = parseParams(req);
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    if (!VALID_SEC_TYPES.has(rawSecType)) {
      res.status(400).json({ error: `Invalid sec_type: ${rawSecType}. Expected etf/index/stock.` });
      return;
    }
    if (!scenario) {
      res.status(400).json({ error: "Missing 'scenario' parameter" });
      return;
    }
    const result = await fetchForecastScenarioDecisions(code, rawSecType as MaSpreadSecType, scenario, strategyName);
    if (result === null) {
      res.status(404).json({ error: `No child seq found for scenario '${scenario}'` });
      return;
    }
    res.json(result);
  } catch (err) {
    console.error("[strategy/singleton/forecast-decisions] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export default router;
