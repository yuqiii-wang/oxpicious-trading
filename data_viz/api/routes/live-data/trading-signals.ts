/**
 * The Trading Signals (analysis scheme) tier: POST /trading-signals/run,
 * POST /trading-signals/run-analysis, GET /trading-signals/run/status,
 * /configs, /history, /portfolio-history, /rules, /portfolios,
 * /portfolio, /trend-spans and GET /trading-signals. The module-level
 * on-demand compute cooldown state moves with GET /trading-signals, its
 * only consumer.
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import { runPythonModule, getPythonProcessStatus } from "../../services/py-runner.service.js";
import {
  fetchSignalRules,
  fetchSignalPortfolios,
  fetchPortfolioSignals,
  fetchPortfolioSignalHistory,
  fetchTrendSpans,
  fetchTradingSignalConfigs,
  fetchTriggeredSignals,
  fetchTradingSignalDates,
  fetchTradingSignalHistory,
  isTradingSignalComputeEligible,
} from "../../services/trading-signals.service.js";

const router = Router();

// ---------------------------------------------------------------------------
//  Trading Signals (analysis scheme) — the signals tier: signal_rules
//  (the rule cells) + the history/live breach records they replay into.
// ---------------------------------------------------------------------------

/** Asia/Shanghai "biz today" (YYYY-MM-DD) — UTC+8 year-round, so the
 *  wall-clock date is built from the UTC offset regardless of the API
 *  server's own timezone. Mirrors the UI's live-markets biz-day logic. */
function shanghaiToday(): string {
  const now = new Date();
  const utc = now.getTime() + now.getTimezoneOffset() * 60_000;
  const sh = new Date(utc + 8 * 60 * 60_000);
  return sh.toISOString().slice(0, 10);
}

const TRADING_SIGNALS_RUN_TAG = "trading-signals:run";
const TRADING_SIGNALS_RUN_ANALYSIS_TAG = "trading-signals:run-analysis";

/** POST /api/live-data/trading-signals/run
 *  body: { sec_types?: string[] } — spawns
 *  `python -m live.live_signals --sec-type <csv>` (batch mode: every code
 *  with ACTIVE analysis_signals.signal_rules rule cells of the given
 *  sec_types; codes
 *  without intraday price are skipped server-side). Fired by the page's
 *  Refresh button and the once-per-biz-day 13:30 scheduler. Deduped by
 *  process-id-tag. */
router.post("/trading-signals/run", async (req: Request, res: Response) => {
  const valid = new Set(["index", "etf", "stock"]);
  const secTypes = Array.isArray(req.body?.sec_types)
    ? (req.body.sec_types as unknown[]).filter(
        (s): s is string => typeof s === "string" && valid.has(s),
      )
    : [];
  if (secTypes.length === 0) secTypes.push("index");
  const tag = TRADING_SIGNALS_RUN_TAG;
  try {
    const result = await runPythonModule(
      "live.live_signals",
      ["--sec-type", secTypes.join(",")],
      { processIdTag: tag },
    );
    res.json({
      success: result.success,
      sec_types: secTypes,
      process_id_tag: tag,
      already_running: result.already_running === true,
      stdout_tail: result.stdout.slice(-2000),
      stderr_tail: result.stderr.slice(-2000),
    });
  } catch (err) {
    console.error("[live-data/trading-signals/run] error:", err);
    res.status(500).json({ success: false, stderr_tail: String(err) });
  }
});

/** POST /api/live-data/trading-signals/run-analysis
 *  body: { sec_types?: string[] } — spawns
 *  `python -m analysis.analysis_signals --sec-type <csv>`: the signals
 *  pipeline (re-measures the sec_type's signal_rules cells on each
 *  code's own history and replays them into history_signals).
 *  Fired by the page's old-date refresh when no intraday data exists
 *  for that date. Deduped by process-id-tag. */
router.post("/trading-signals/run-analysis", async (req: Request, res: Response) => {
  const valid = new Set(["index", "etf", "stock"]);
  const secTypes = Array.isArray(req.body?.sec_types)
    ? (req.body.sec_types as unknown[]).filter(
        (s): s is string => typeof s === "string" && valid.has(s),
      )
    : [];
  if (secTypes.length === 0) secTypes.push("index");
  const tag = TRADING_SIGNALS_RUN_ANALYSIS_TAG;
  try {
    const result = await runPythonModule(
      "analysis.analysis_signals",
      ["--sec-type", secTypes.join(",")],
      { processIdTag: tag },
    );
    res.json({
      success: result.success,
      sec_types: secTypes,
      process_id_tag: tag,
      already_running: result.already_running === true,
      stdout_tail: result.stdout.slice(-2000),
      stderr_tail: result.stderr.slice(-2000),
    });
  } catch (err) {
    console.error("[live-data/trading-signals/run-analysis] error:", err);
    res.status(500).json({ success: false, stderr_tail: String(err) });
  }
});

/** GET /api/live-data/trading-signals/run/status
 *  → { status: { [tag]: boolean } } — running-state of BOTH run tags
 *  (intraday run + signals-pipeline run) so a page refresh can restore
 *  the Refresh button's spinning state. */
router.get("/trading-signals/run/status", async (_req: Request, res: Response) => {
  try {
    res.json({ status: getPythonProcessStatus([
      TRADING_SIGNALS_RUN_TAG,
      TRADING_SIGNALS_RUN_ANALYSIS_TAG,
    ]) });
  } catch (err) {
    console.error("[live-data/trading-signals/run/status] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/live-data/trading-signals/configs?sec_type=index
 *  → { sec_type, configs: [{signal_type, signal_sub_type, n_configs}] } —
 *  the ACTIVE analysis_signals.signal_rules rule cells (signal menu;
 *  default = all). */
router.get("/trading-signals/configs", async (req: Request, res: Response) => {
  try {
    const secType = typeof req.query.sec_type === "string"
      ? req.query.sec_type : "index";
    const configs = await fetchTradingSignalConfigs(secType);
    res.json({ sec_type: secType, configs });
  } catch (err) {
    const status = (err as { status?: number })?.status ?? 500;
    console.error("[live-data/trading-signals/configs] error:", err);
    res.status(status).json({ error: String(err) });
  }
});

/** GET /api/live-data/trading-signals/history?sec_type=index&code=000300
 *  &signal_type=&signal_sub_type=&date=YYYY-MM-DD
 *  → { sec_type, code, date (echoed window anchor; null = newest close),
 *      recent_window_start, signals: [...] } — EVERY signal event of the
 *  code (newest first): analysis_signals.history_signals (the multi-year
 *  record, one population per event since the 2026-11-01 variant-axes
 *  unlink — is_typical the day's episode pass) UNION live.live_signals
 *  (this year's recorded breaches), one record per config-day — plus
 *  the last-20-trading-days boundary anchored on `date`: the UI scopes
 *  its history TABLE to rows after it while the code-trend chart draws
 *  every row as a buy/sell marker. signal_type / signal_sub_type
 *  optionally narrow the history to one signal family. */
router.get("/trading-signals/history", async (req: Request, res: Response) => {
  try {
    const secType = typeof req.query.sec_type === "string"
      ? req.query.sec_type : "index";
    const code = typeof req.query.code === "string"
      ? req.query.code.trim() : "";
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    const signalType = typeof req.query.signal_type === "string"
      && req.query.signal_type.trim()
      ? req.query.signal_type.trim() : null;
    const signalSubType = typeof req.query.signal_sub_type === "string"
      && req.query.signal_sub_type.trim()
      ? req.query.signal_sub_type.trim() : null;
    const date = typeof req.query.date === "string"
      && /^\d{4}-\d{2}-\d{2}$/.test(req.query.date)
      ? req.query.date : null;
    const { signals, recent_window_start } = await fetchTradingSignalHistory(
      secType, code, signalType, signalSubType, date,
    );
    res.json({
      sec_type: sec_type_valid(secType),
      code,
      signal_type: signalType,
      signal_sub_type: signalSubType,
      date,
      recent_window_start,
      signals,
    });
  } catch (err) {
    const status = (err as { status?: number })?.status ?? 500;
    console.error("[live-data/trading-signals/history] error:", err);
    res.status(status).json({ error: String(err) });
  }
});

/** GET /api/live-data/trading-signals/portfolio-history?sec_type=index
 *  &code=A~B[&signal_type=&signal_sub_type=]
 *  → { sec_type, code, signal_type, signal_sub_type, events: [...] } —
 *  the PORTFOLIO tier's EVENT history (analysis_signals.
 *  portfolio_history_signals, 2026-10-21): one row per coordinated
 *  portfolio event with THE EVENT DATE and the member association —
 *  the member CELL-LEVEL signal events that triggered it (each with
 *  its OWN date; the components' own history is dropped from
 *  history_signals, so the member records ride the payload). The
 *  portfolio history panel's expandable rows. signal_type /
 *  signal_sub_type optionally narrow to one family / sub. */
router.get("/trading-signals/portfolio-history", async (req: Request, res: Response) => {
  try {
    const secType = typeof req.query.sec_type === "string"
      ? req.query.sec_type : "index";
    const code = typeof req.query.code === "string"
      ? req.query.code.trim() : "";
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    const signalType = typeof req.query.signal_type === "string"
      && req.query.signal_type.trim()
      ? req.query.signal_type.trim() : null;
    const signalSubType = typeof req.query.signal_sub_type === "string"
      && req.query.signal_sub_type.trim()
      ? req.query.signal_sub_type.trim() : null;
    const events = await fetchPortfolioSignalHistory(
      secType, code, signalType, signalSubType,
    );
    res.json({
      sec_type: sec_type_valid(secType),
      code,
      signal_type: signalType,
      signal_sub_type: signalSubType,
      events,
    });
  } catch (err) {
    const status = (err as { status?: number })?.status ?? 500;
    console.error("[live-data/trading-signals/portfolio-history] error:", err);
    res.status(status).json({ error: String(err) });
  }
});

/** GET /api/live-data/trading-signals/rules?sec_type=index&code=000001
 *  → { sec_type, code, rules: [...] } — the code's RULE CELLS
 *  (analysis_signals.signal_rules, the 2026-10-15 rules tier): one row
 *  per (sub signal, trend, regime, action) with the engine entry, the
 *  SELF-ADAPTIVE prepared params (bar def, gate, measured evidence)
 *  and the family knowledge document (static.signal_rules_docs,
 *  joined per family). The Recent Movements
 *  code-trend's
 *  RULES panel; the history events below are populated by REPLAYING
 *  these cells. (Replaces the retired /curves + /thresholds overlays.) */
router.get("/trading-signals/rules", async (req: Request, res: Response) => {
  try {
    const secType = typeof req.query.sec_type === "string"
      ? req.query.sec_type : "index";
    const code = typeof req.query.code === "string"
      ? req.query.code.trim() : "";
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    const rules = await fetchSignalRules(secType, code);
    res.json({
      sec_type: sec_type_valid(secType),
      code,
      rules,
    });
  } catch (err) {
    const status = (err as { status?: number })?.status ?? 500;
    console.error("[live-data/trading-signals/rules] error:", err);
    res.status(status).json({ error: String(err) });
  }
});

/** GET /api/live-data/trading-signals/portfolios?sec_type=index
 *  [&portfolio_type=neg_corr_hedge_pair]
 *  → { sec_type, portfolio_type, portfolios: [...] } — the managed
 *  PORTFOLIO codes of the sec_type (analysis_signals.
 *  portfolio_signal_rules, the 2026-10-18 portfolio tier: per-sub-type
 *  rule groups with signal_rule_ids BIGINT[] arrays — the neg_corr_
 *  hedge_pair 'A~B' pair portfolios), each with its census (rule
 *  cells, gate-passed share, sub-type groups, latest event date,
 *  latest build). The managed-pair roster. */
router.get("/trading-signals/portfolios", async (req: Request, res: Response) => {
  try {
    const secType = typeof req.query.sec_type === "string"
      ? req.query.sec_type : "index";
    const portfolioType = typeof req.query.portfolio_type === "string"
      ? req.query.portfolio_type.trim() : null;
    const portfolios = await fetchSignalPortfolios(
      secType, portfolioType || null);
    res.json({
      sec_type: sec_type_valid(secType),
      portfolio_type: portfolioType,
      portfolios,
    });
  } catch (err) {
    const status = (err as { status?: number })?.status ?? 500;
    console.error("[live-data/trading-signals/portfolios] error:", err);
    res.status(status).json({ error: String(err) });
  }
});

/** GET /api/live-data/trading-signals/portfolio?sec_type=index
 *  &codes=DIV_GENERAL,TECH_GENERAL
 *  → { sec_type, codes, portfolios: [...], rules: [...] } — the
 *  portfolios whose legs sit in the requested CODES (the pair types:
 *  both legs of 'A~B' must be in the list — industry scope for now),
 *  each with its PORTFOLIO row (the whole member signal_rule_ids
 *  array + the rule_params construction with the per-sub
 *  coordination specs under subs, + its LATEST
 *  history_portfolio_signal_performance audit snapshot — the
 *  members' combined stats + what the portfolio took; the hedge
 *  type: max drawdown reduction) and census, plus the FULL rule rows
 *  expanded from the ids. The shared portfolio-signal UI component's
 *  one round trip. */
router.get("/trading-signals/portfolio", async (req: Request, res: Response) => {
  try {
    const secType = typeof req.query.sec_type === "string"
      ? req.query.sec_type : "index";
    const codes = typeof req.query.codes === "string"
      ? req.query.codes.split(",") : [];
    res.json(await fetchPortfolioSignals(secType, codes));
  } catch (err) {
    const status = (err as { status?: number })?.status ?? 500;
    console.error("[live-data/trading-signals/portfolio] error:", err);
    res.status(status).json({ error: String(err) });
  }
});

/** GET /api/live-data/trading-signals/trend-spans?sec_type=index&code=000001
 *  [&window_days=1095]
 *  → { sec_type, code, window_days, spans: { top|bottom|rise|fall|unknown:
 *  [{start_date, end_date, span_days}] } } — the code's CONTIGUOUS
 *  same-TREND runs (gaps-and-islands over analysis.trend_days, the
 *  rule tier's per-(code, date) classification; default window the
 *  last 3y). The shared CodeTrendChart's Trend toggle shades these
 *  spans per trend — the regime-bands precedent. */
router.get("/trading-signals/trend-spans", async (req: Request, res: Response) => {
  try {
    const secType = typeof req.query.sec_type === "string"
      ? req.query.sec_type : "index";
    const code = typeof req.query.code === "string"
      ? req.query.code.trim() : "";
    if (!code) {
      res.status(400).json({ error: "Missing 'code' parameter" });
      return;
    }
    const windowDays = typeof req.query.window_days === "string"
      && /^\d+$/.test(req.query.window_days)
      ? Number(req.query.window_days) : null;
    res.json(await fetchTrendSpans(secType, code, windowDays));
  } catch (err) {
    const status = (err as { status?: number })?.status ?? 500;
    console.error("[live-data/trading-signals/trend-spans] error:", err);
    res.status(status).json({ error: String(err) });
  }
});

// ---- Trading-signals ON-DEMAND compute (selected-date backfill) -------------
//      When the requested (sec_type, date) has NO live_signals rows, the
//      route spawns `python -m live.live_signals --sec-type X --date D`
//      (the as-of evaluation: last intraday bar of the date when the
//      intraday table covers it, else the official daily close at 15:00
//      with is_day_close_trigger = TRUE) and re-reads the table. Guards:
//        • eligibility probe first (ACTIVE strategies + daily closes for
//          the date — otherwise every request would spawn pointlessly),
//        • per (sec_type, date) cooldown (a compute that legitimately
//          yields ZERO breaches must not respawn on every poll),
//        • bounded wait — the spawn keeps running past the cap (deduped
//          by process-id-tag) and later requests pick its rows up.
const TRADING_SIGNALS_COMPUTE_WAIT_MS = 90_000;
const TRADING_SIGNALS_COMPUTE_COOLDOWN_MS = 10 * 60_000;
const tradingSignalsComputeAt = new Map<string, number>();

function tradingSignalsComputeAllowed(key: string): boolean {
  const last = tradingSignalsComputeAt.get(key) ?? 0;
  return Date.now() - last >= TRADING_SIGNALS_COMPUTE_COOLDOWN_MS;
}

/** GET /api/live-data/trading-signals?sec_type=index&date=YYYY-MM-DD
 *  → { sec_type, date (resolved: param || biz today), available_dates,
 *      signals: [...] } — one day's triggered breaches, confidence DESC.
 *  A date with no rows is computed ON DEMAND (see the block above). */
router.get("/trading-signals", async (req: Request, res: Response) => {
  try {
    const secType = typeof req.query.sec_type === "string"
      ? req.query.sec_type : "index";
    const dateParam = typeof req.query.date === "string"
      && /^\d{4}-\d{2}-\d{2}$/.test(req.query.date)
      ? req.query.date
      : shanghaiToday();
    const [fetchedSignals, availableDates] = await Promise.all([
      fetchTriggeredSignals(secType, dateParam),
      fetchTradingSignalDates(secType),
    ]);
    let signals = fetchedSignals;
    if (
      signals.length === 0
      && dateParam <= shanghaiToday()
      && tradingSignalsComputeAllowed(`${secType}:${dateParam}`)
      && await isTradingSignalComputeEligible(secType, dateParam)
    ) {
      tradingSignalsComputeAt.set(`${secType}:${dateParam}`, Date.now());
      const tag = `trading-signals:compute:${secType}:${dateParam}`;
      console.info(
        `[live-data/trading-signals] no rows for ${secType} @ ${dateParam} — ` +
        "spawning on-demand as-of compute",
      );
      await Promise.race([
        runPythonModule(
          "live.live_signals",
          ["--sec-type", secType, "--date", dateParam],
          { processIdTag: tag },
        ),
        new Promise((resolve) => setTimeout(resolve, TRADING_SIGNALS_COMPUTE_WAIT_MS)),
      ]);
      signals = await fetchTriggeredSignals(secType, dateParam);
    }
    res.json({ sec_type: sec_type_valid(secType), date: dateParam, available_dates: availableDates, signals });
  } catch (err) {
    const status = (err as { status?: number })?.status ?? 500;
    console.error("[live-data/trading-signals] error:", err);
    res.status(status).json({ error: String(err) });
  }
});

function sec_type_valid(v: string): string {
  return ["index", "etf", "stock"].includes(v) ? v : "index";
}

export default router;
