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
 *  GET /api/strategy/singleton/run-history?sec_type=index&code=000970&strategy_name=macd
 *    Lists the recent strategy_identity runs (newest first) with their
 *    stored params config — feeds the UI's run-history selector.
 *    Optional &seq_id= on /singleton/backtest + /singleton/risks displays
 *    one historical run instead of the latest/active one.
 *
 *  POST /api/strategy/singleton/run?sec_type=index&code=000970
 *    Submits the Python backtest (`strategy.singleton_trading`) to the
 *    server-cluster STRATEGIST (127.0.0.1:8772 via the shared py-runner
 *    service — no local spawn) and returns at the cluster ACCEPT
 *    ({accepted, already_running, pid, process_id_tag}) — the HTTP
 *    request does NOT wait for the job to exit (2026-11-18 async
 *    submit). The frontend polls GET /singleton/process-status for the
 *    running state and GET /singleton/process-result for the outcome +
 *    log tail under the same process-id-tag.
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
  fetchRunHistory,
  fetchForecast,
  runForecastScript,
  DEFAULT_STRATEGY_NAME,
  DEFAULT_FAULT_TOLERANCE,
} from "../services/strategy/index.js";
import {
  getPythonProcessDetails,
  getPythonProcessResult,
} from "../services/infra/py-runner.service.js";
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
  // seq_id query param (optional): load THIS run instead of the latest/
  // active one — the run-history selector. Absent/invalid → latest.
  const seqIdRaw = typeof req.query.seq_id === "string" ? req.query.seq_id.trim() : "";
  const seqId = seqIdRaw ? Number(seqIdRaw) || null : null;
  return { code, rawSecType, strategyName, seqId };
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
  const { code, rawSecType, strategyName, seqId } = parseParams(req);
  if (!code) throw HttpError.missingParam("code");
  requireValidSecType(rawSecType);
  const result = await runSingletonBacktest(code, rawSecType as MaSpreadSecType, strategyName, seqId);
  res.json(result);
}));

router.get("/singleton/risks", wrapAsync(async (req: Request, res: Response) => {
  const { code, rawSecType, strategyName, seqId } = parseParams(req);
  if (!code) throw HttpError.missingParam("code");
  requireValidSecType(rawSecType);
  const result = await fetchStrategyRisks(code, rawSecType as MaSpreadSecType, strategyName, seqId);
  res.json(result);
}));

// Run history — the recent strategy_identity runs for one
// (sec_type, code, strategy_name), newest first, each with its stored
// params config. Feeds the UI's run-history selector; pair with
// ?seq_id= on /singleton/backtest + /singleton/risks to display a run.
// GET /api/strategy/singleton/run-history?sec_type=index&code=000970&strategy_name=macd
router.get("/singleton/run-history", wrapAsync(async (req: Request, res: Response) => {
  const { code, rawSecType, strategyName } = parseParams(req);
  if (!code) throw HttpError.missingParam("code");
  requireValidSecType(rawSecType);
  const result = await fetchRunHistory(code, rawSecType as MaSpreadSecType, strategyName);
  res.json(result);
}));

// Check if a strategy run already exists for the natural key.
// GET /api/strategy/singleton/check?sec_type=index&code=000970&strategy_name=macd
// Returns { exists: true, seq_id, seq_no, start_date, end_date, status,
//           analysis_latest_date, stale }
// or { exists: false } if no run exists yet. stale = the run's end_date
// ends before the code's analysis horizon (a rerun extends it) — the UI
// force-reruns it without asking; a fresh run still confirms first.
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

// Submit the strategy run — resolves at the cluster accept (already_
// running dedupe included); the outcome is polled via process-status /
// process-result below.
router.post("/singleton/run", wrapAsync(async (req: Request, res: Response) => {
  const { code, rawSecType } = parseParams(req);
  if (!code) throw HttpError.missingParam("code");
  requireValidSecType(rawSecType);
  // algo query param: serialized selection like "macd,mov_rsi"
  // (understood by Python's _parse_algo_arg). Falls back to the default
  // binary algo (macd) when absent.
  const algo = typeof req.query.algo === "string" && req.query.algo.trim()
    ? req.query.algo.trim() : DEFAULT_STRATEGY_NAME;
  // fault_tolerance query param: 0-20, pinned to the production default
  // 10 (see strategy/_trading/constants.py DEFAULT_FAULT_TOLERANCE —
  // the UI no longer varies it). >0 shifts BUY fills adversely
  // (engine stress), runs the two-pass stress annotation, and appends
  // the _ft{N} suffix to the RUN identity (config keys stay ft-less).
  const ftRaw = typeof req.query.fault_tolerance === "string"
    ? req.query.fault_tolerance.trim() : "";
  const faultTolerance = ftRaw
    ? Math.max(0, Math.min(20, Number(ftRaw) || 0))
    : DEFAULT_FAULT_TOLERANCE;
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

// Train Model — the nested trainer (`_optm_engine`), submitted to the
// server-cluster strategist. BINARY when `algo` is one family (Set A
// TPE); MIXED when it is a serialized selection (the Set C comb grid —
// the portfolio's weight-rule search). Upserts the best params into
// strategy.algo_configs under the ft-less config name; the next "Run
// Strategy" picks them up automatically. The trainer's FINAL STEP is
// the trained strategy's own run (it spawns the run-once CLI,
// strategy.singleton_trading, with --force at the pinned default FT) —
// a finished training leaves a real run-history record without anyone
// clicking Run. The response resolves at the cluster accept (one job
// covers train + run; the run tag never becomes active) — train + run
// take minutes, so the outcome is polled via process-status /
// process-result under the train tag.
router.post("/singleton/train", wrapAsync(async (req: Request, res: Response) => {
  const { code, rawSecType } = parseParams(req);
  if (!code) throw HttpError.missingParam("code");
  requireValidSecType(rawSecType);
  // algo query param: a single family name ("macd", binary training)
  // or a serialized selection ("macd,mov_rsi", mixed portfolio
  // training) — passed through verbatim; Python's trainer parses it.
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

// Running-state of UI-triggered cluster jobs, keyed by process-id-tag —
// the cluster registry's ENRICHED entry per tag (running / known / pid /
// started_at / ended_at / exit_code / adopted), not just a boolean. The
// UI polls this on mount and on an adaptive cadence (60s idle, faster
// while a job runs) so a page refresh puts the Run/Train buttons
// straight back into their spinning state and reloads data when the job
// exits; exit_code drives the failure-tail display.
// GET /api/strategy/singleton/process-status?process_id_tag=a,b
//   → { status: { [tag]: { running, known, pid, started_at, ended_at,
//                           exit_code, adopted } } }
router.get("/singleton/process-status", wrapAsync(async (req: Request, res: Response) => {
  const raw = strParam(req, "process_id_tag") ?? "";
  const tags = raw.split(",").map((t) => t.trim()).filter(Boolean);
  res.json({ status: await getPythonProcessDetails(tags) });
}));

// One submitted job's outcome + log tail, keyed by process-id-tag —
// the strategy UI fetches this when the status poll sees a job exit
// with a non-zero exit code, to surface the process's own stderr tail
// (the blocking-POST era returned the same tail in the POST response).
// GET /api/strategy/singleton/process-result?process_id_tag=tag
//   → { result: { known, running, exit_code, stdout, stderr } }
router.get("/singleton/process-result", wrapAsync(async (req: Request, res: Response) => {
  const tag = (strParam(req, "process_id_tag") ?? "").trim();
  if (!tag) throw HttpError.missingParam("process_id_tag");
  res.json({ result: await getPythonProcessResult(tag) });
}));

// Train Model history — default + trained algo_configs rows, training run
// headers, and the per-point trial log for the latest (or ?run_id) run.
// The loss types (set_a_omega / set_c_calmar / set_b_calmar) are tagged
// per row so the UI can display them separately.
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

// ---------------------------------------------------------------------------
//  Forecast next day — the σ-ladder scenario register (Strategy → Forecast)
// ---------------------------------------------------------------------------

// The forecast register read — strategy.forecast_next_day rows with
// labels decoded SQL-side. One code's ladder when &code= is given,
// else every code's LATEST anchor (the page's default view).
// GET /api/strategy/forecast?sec_type=index[&code=000001][&anchor_date=…]
router.get("/forecast", wrapAsync(async (req: Request, res: Response) => {
  const rawSecType = typeof req.query.sec_type === "string"
    ? req.query.sec_type.trim().toLowerCase() : "index";
  requireValidSecType(rawSecType);
  const code = typeof req.query.code === "string" && req.query.code.trim()
    ? req.query.code.trim() : null;
  const anchorDate = typeof req.query.anchor_date === "string"
    && req.query.anchor_date.trim() ? req.query.anchor_date.trim() : null;
  const result = await fetchForecast(
    rawSecType as MaSpreadSecType, code, anchorDate,
  );
  res.json(result);
}));

// Run Forecast — submit the forecast pipeline (strategy.forecast_
// next_day) to the cluster strategist. The module is incremental and
// auto-links to the strategy run (it self-spawns the nightly portfolio
// CLI when the latest active run is behind the bar horizon). Resolves
// at the cluster accept; poll /singleton/process-status under the
// forecast tag for the outcome.
// POST /api/strategy/forecast/run?sec_type=index[&process_id_tag=…]
router.post("/forecast/run", wrapAsync(async (req: Request, res: Response) => {
  const rawSecType = typeof req.query.sec_type === "string"
    ? req.query.sec_type.trim().toLowerCase() : "index";
  requireValidSecType(rawSecType);
  const processIdTag = typeof req.query.process_id_tag === "string"
    ? req.query.process_id_tag.trim() : undefined;
  const result = await runForecastScript(
    rawSecType as MaSpreadSecType, processIdTag,
  );
  res.json(result);
}));

export default router;
