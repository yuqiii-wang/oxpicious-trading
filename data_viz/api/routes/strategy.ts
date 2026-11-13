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
import { wrapAsync, strParam, HttpError } from "../lib/http.js";
import {
  runSingletonBacktest,
  fetchStrategyRisks,
  runStrategyScript,
  runTrainingScript,
  checkExistingStrategy,
  fetchTrainInfo,
  fetchSignalFamilies,
  DEFAULT_STRATEGY_NAME,
} from "../services/strategy/index.js";
import { getPythonProcessStatus } from "../services/infra/py-runner.service.js";
import type { MaSpreadSecType } from "../../shared/types.js";

const router = Router();

const VALID_SEC_TYPES = new Set(["etf", "index", "stock"]);

/** 400 — sec_type outside the etf/index/stock vocabulary (legacy text kept). */
function requireValidSecType(raw: string): void {
  if (!VALID_SEC_TYPES.has(raw)) {
    throw new HttpError(400, "INVALID_PARAM", `Invalid sec_type: ${raw}. Expected etf/index/stock.`);
  }
}

function parseParams(req: Request) {
  const code = typeof req.query.code === "string" ? req.query.code.trim() : "";
  const rawSecType = typeof req.query.sec_type === "string"
    ? req.query.sec_type.trim().toLowerCase() : "index";
  // strategy_name query param: the DB strategy_name to filter on. This is
  // EITHER an algo name (macd — binary mode)
  // OR a portfolio name (portfolio:macd*0.5 — mixed mode). Falls back
  // to the legacy `algo` query param for backward compat, then to the default
  // (macd). No validation — any string the frontend resolved is passed
  // through to SQL as the strategy_name filter.
  const rawStrategyName = typeof req.query.strategy_name === "string"
    ? req.query.strategy_name.trim()
    : (typeof req.query.algo === "string" ? req.query.algo.trim().toLowerCase() : "");
  const strategyName = rawStrategyName || DEFAULT_STRATEGY_NAME;
  return { code, rawSecType, strategyName };
}

// The SIGNAL TAGS menu — the analysis signal families with recorded
// standalone-tier events for the sec_type (data-driven; the strategy
// tier picks up analysis_signals.history_signals events by family).
// GET /api/strategy/singleton/signal-families?sec_type=index
// Returns { sec_type, families: [{name, n_events, n_codes, latest_date}] }.
router.get("/singleton/signal-families", wrapAsync(async (req: Request, res: Response) => {
  const rawSecType = typeof req.query.sec_type === "string"
    ? req.query.sec_type.trim().toLowerCase() : "index";
  requireValidSecType(rawSecType);
  const families = await fetchSignalFamilies(rawSecType as MaSpreadSecType);
  res.json({ sec_type: rawSecType, families });
}));

router.get("/singleton/backtest", wrapAsync(async (req: Request, res: Response) => {
  const { code, rawSecType, strategyName } = parseParams(req);
  if (!code) throw HttpError.missingParam("code");
  requireValidSecType(rawSecType);
  const result = await runSingletonBacktest(code, rawSecType as MaSpreadSecType, strategyName);
  res.json(result);
}));

router.get("/singleton/risks", wrapAsync(async (req: Request, res: Response) => {
  const { code, rawSecType, strategyName } = parseParams(req);
  if (!code) throw HttpError.missingParam("code");
  requireValidSecType(rawSecType);
  const result = await fetchStrategyRisks(code, rawSecType as MaSpreadSecType, strategyName);
  res.json(result);
}));

// Check if a strategy run already exists for the natural key.
// GET /api/strategy/singleton/check?sec_type=index&code=000970&strategy_name=macd
// Returns { exists: true, seq_id, seq_no, start_date, end_date, status }
// or { exists: false } if no run exists yet.
router.get("/singleton/check", wrapAsync(async (req: Request, res: Response) => {
  const { code, rawSecType, strategyName } = parseParams(req);
  if (!code) throw HttpError.missingParam("code");
  requireValidSecType(rawSecType);
  const existing = await checkExistingStrategy(code, rawSecType as MaSpreadSecType, strategyName);
  if (existing) {
    res.json({ exists: true, ...existing });
  } else {
    res.json({ exists: false });
  }
}));

router.post("/singleton/run", wrapAsync(async (req: Request, res: Response) => {
  const { code, rawSecType } = parseParams(req);
  if (!code) throw HttpError.missingParam("code");
  requireValidSecType(rawSecType);
  // algo query param: serialized selection like "macd:0.5,bb:0.5"
  // (understood by Python's _parse_algo_arg). Falls back to the default
  // binary algo (macd) when absent.
  const algo = typeof req.query.algo === "string" && req.query.algo.trim()
    ? req.query.algo.trim() : DEFAULT_STRATEGY_NAME;
  // fault_tolerance query param: 0-20 (default 0 = disabled). When >0,
  // passes --fault-tolerance to Python, which runs a two-pass stress test
  // (baseline → stress OHLC on decision days → recompute → re-run) and
  // appends _ft{N} suffix to the strategy_name so the FT variant is a
  // distinct strategy in the DB.
  const ftRaw = typeof req.query.fault_tolerance === "string"
    ? req.query.fault_tolerance.trim() : "";
  const faultTolerance = ftRaw ? Math.max(0, Math.min(20, Number(ftRaw) || 0)) : 0;
  // force query param: default true. When false, passes --force flag.
  // Frontend typically passes force=true after user confirms re-run.
  const force = req.query.force !== "false";
  // process_id_tag query param: optional dedupe tag for the py-runner
  // registry (defaults to singleton-run:<sec_type>:<code>:<algo>:<ft>).
  // A POST racing an already-running process with the SAME tag resolves
  // immediately with already_running=true instead of spawning a
  // duplicate.
  const processIdTag = typeof req.query.process_id_tag === "string"
    ? req.query.process_id_tag.trim() : undefined;
  const result = await runStrategyScript(
    code, rawSecType as MaSpreadSecType, algo, faultTolerance, force,
    processIdTag,
  );
  res.json(result);
}));

// Train Model — Optuna param optimization (`_optm_engine`). Runs an
// in-memory study and upserts the best params into strategy.algo_configs;
// the next "Run Strategy" run picks them up automatically.
router.post("/singleton/train", wrapAsync(async (req: Request, res: Response) => {
  const { code, rawSecType } = parseParams(req);
  if (!code) throw HttpError.missingParam("code");
  requireValidSecType(rawSecType);
  // algo query param: single algo name (training is binary-mode only;
  // a serialized selection takes its first component).
  const algo = typeof req.query.algo === "string" && req.query.algo.trim()
    ? req.query.algo.trim() : DEFAULT_STRATEGY_NAME;
  // trials query param: number of Optuna trials (default 50, 1-500).
  const trialsRaw = typeof req.query.trials === "string"
    ? req.query.trials.trim() : "";
  const trials = trialsRaw ? Number(trialsRaw) || 50 : 50;
  // process_id_tag query param: optional dedupe tag for the py-runner
  // registry (defaults to singleton-train:<sec_type>:<code>:<algo>).
  const processIdTag = typeof req.query.process_id_tag === "string"
    ? req.query.process_id_tag.trim() : undefined;
  const result = await runTrainingScript(
    code, rawSecType as MaSpreadSecType, algo, trials,
    processIdTag,
  );
  res.json(result);
}));

// Running-state of UI-triggered WSL processes, keyed by process-id-tag.
// The UI polls this on mount (and while a REMOTE process runs) so a page
// refresh puts the Run/Train buttons straight back into their spinning
// state and reloads data when the process exits.
// GET /api/strategy/singleton/process-status?process_id_tag=a,b
//   → { status: { [tag]: boolean } }
router.get("/singleton/process-status", wrapAsync(async (req: Request, res: Response) => {
  const raw = strParam(req, "process_id_tag") ?? "";
  const tags = raw.split(",").map((t) => t.trim()).filter(Boolean);
  res.json({ status: await getPythonProcessStatus(tags) });
}));

// Train Model history — default + trained algo_configs rows, training run
// headers, and the per-point trial log for the latest (or ?run_id) run.
// The two loss types (set_a_omega / set_b_calmar) are tagged per row so
// the UI can display them separately.
// GET /api/strategy/singleton/train-info?sec_type=index&code=000300&strategy_name=macd[&run_id=1]
router.get("/singleton/train-info", wrapAsync(async (req: Request, res: Response) => {
  const { code, rawSecType, strategyName } = parseParams(req);
  if (!code) throw HttpError.missingParam("code");
  requireValidSecType(rawSecType);
  const runIdRaw = typeof req.query.run_id === "string" ? req.query.run_id.trim() : "";
  const runId = runIdRaw ? Number(runIdRaw) || null : null;
  const result = await fetchTrainInfo(
    code, rawSecType as MaSpreadSecType, strategyName, runId,
  );
  res.json(result);
}));

export default router;
