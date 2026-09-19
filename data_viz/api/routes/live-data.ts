/**
 * Live Data API routes.
 *
 * Three endpoints:
 *   GET /api/live-data/dates?type=index|stock
 *     → { type, dates: ["2026-07-31", ...] }  (descending — most recent first)
 *
 *   GET /api/live-data/themes?type=index|stock
 *     → L1 sector → L2 industry tree (SectorNode[]) restricted to codes with
 *       at least one intraday bar.
 *
 *   GET /api/live-data/combined?type=index|stock&date=&sector=&industry=
 *       &exchange=&code=&page=&page_size=
 *     → paginated list of codes with their intraday bars for the requested
 *       date (defaults to the latest available date).
 *
 * ETF ('type=etf') is intentionally NOT supported here — no
 * stats.etf_intraday_5min table exists yet. The frontend renders an empty
 * placeholder for the ETF tab until that table is added.
 */
import { Router, type Request, type Response } from "express";
import {
  listLiveDataDates,
  listLiveDataThemes,
  listStrategyThemes,
  getLiveDataCombined,
  type LiveDataSecType,
} from "../services/live-data.service.js";
import {
  getIntradayMovements,
  listIntradayMovementsBenchmarks,
  listIntradayMovementsDates,
  getIntradayMovementsPrevDayOhlc,
} from "../services/analysis/index.js";
import { runPythonModule, getPythonProcessStatus, isPythonProcessRunning } from "../services/py-runner.service.js";
import { getSecAllocLiveAttribution } from "../services/sec-alloc-live-attribution.service.js";
import {
  fetchTradingSignalConfigs,
  fetchTriggeredSignals,
  fetchTradingSignalDates,
  fetchTradingSignalHistory,
  isTradingSignalComputeEligible,
} from "../services/trading-signals.service.js";

const router = Router();

function parseSecType(v: unknown): LiveDataSecType {
  return v === "stock" ? "stock" : "index";
}

/** GET /api/live-data/dates?type=index */
router.get("/dates", async (req: Request, res: Response) => {
  try {
    const secType = parseSecType(req.query.type);
    res.json(await listLiveDataDates(secType));
  } catch (err) {
    console.error("[live-data/dates] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/live-data/themes?type=index */
router.get("/themes", async (req: Request, res: Response) => {
  try {
    const secType = parseSecType(req.query.type);
    const exchange = typeof req.query.exchange === "string" ? req.query.exchange : undefined;
    res.json(await listLiveDataThemes(secType, exchange));
  } catch (err) {
    console.error("[live-data/themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/live-data/strategy-themes?type=index — parallel strategy → theme tree */
router.get("/strategy-themes", async (req: Request, res: Response) => {
  try {
    const secType = parseSecType(req.query.type);
    const exchange = typeof req.query.exchange === "string" ? req.query.exchange : undefined;
    res.json(await listStrategyThemes(secType, exchange));
  } catch (err) {
    console.error("[live-data/strategy-themes] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

/** GET /api/live-data/combined?type=index&date=&sector=&industry=... */
router.get("/combined", async (req: Request, res: Response) => {
  try {
    const secType = parseSecType(req.query.type);
    const query = {
      sector: typeof req.query.sector === "string" ? req.query.sector : undefined,
      industry: typeof req.query.industry === "string" ? req.query.industry : undefined,
      strategy: typeof req.query.strategy === "string" ? req.query.strategy : undefined,
      theme: typeof req.query.theme === "string" ? req.query.theme : undefined,
      code: typeof req.query.code === "string" ? req.query.code : undefined,
      exchange: typeof req.query.exchange === "string" ? req.query.exchange : undefined,
      date: typeof req.query.date === "string" ? req.query.date : undefined,
      page:
        typeof req.query.page === "string"
          ? parseInt(req.query.page, 10)
          : undefined,
      page_size:
        typeof req.query.page_size === "string"
          ? parseInt(req.query.page_size, 10)
          : undefined,
    };
    res.json(await getLiveDataCombined(secType, query));
  } catch (err) {
    console.error("[live-data/combined] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// ---- Intraday Movements — per-5-min-tick % change vs prev day close for
//      the benchmark + ALL industries (shaded areas) + member indices.
//      Drives the "Market Movements" tab on the Live Data page.
//      Data is read from live.sec_alloc_live_attribution (populated by
//      python -m live.sec_alloc_live_attribution); industry identity
//      (stats.sec_classification) and aggregates are joined/computed at
//      query time.
//
//   GET /api/live-data/intraday-movements
//     ?benchmark_code=000922&date=YYYY-MM-DD
//     Returns IntradayMovementsResponse: benchmark % change per tick +
//     all industries' % change per tick (for shades + middle bar chart) +
//     member indices' % change per (code, tick) (for bottom bar chart).
//     `date` optional → latest available for the benchmark.
//   GET /api/live-data/intraday-movements/benchmarks
//     Returns the list of benchmark codes that appear in
//     stats.cross_stats / live.sec_alloc_live_attribution
//     (broad-market benchmarks first).
//   GET /api/live-data/intraday-movements/prev-day-ohlc
//     ?benchmark_code=000922&date=YYYY-MM-DD
//     Returns PrevDayOhlcResponse: raw prev-trading-day OHLC of the benchmark
//     + every member index (with industry_id) — drives the single prev-day
//     OHLC bar before the 09:30 tick on the top plot.
router.get("/intraday-movements/benchmarks", async (_req: Request, res: Response) => {
  try {
    const benchmarks = await listIntradayMovementsBenchmarks();
    res.json({ benchmarks });
  } catch (err) {
    console.error("[live-data/intraday-movements/benchmarks] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

//   GET /api/live-data/intraday-movements/dates
//     ?benchmark_code=000300
//     Returns the distinct dates available for the benchmark (raw intraday
//     bars UNION live tick rows), newest first — drives the date selector.
router.get("/intraday-movements/dates", async (req: Request, res: Response) => {
  try {
    const benchmarkCode = typeof req.query.benchmark_code === "string" ? req.query.benchmark_code : "";
    const dates = await listIntradayMovementsDates(benchmarkCode);
    res.json({ benchmark_code: benchmarkCode, dates });
  } catch (err) {
    console.error("[live-data/intraday-movements/dates] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/intraday-movements/prev-day-ohlc", async (req: Request, res: Response) => {
  try {
    const benchmarkCode = typeof req.query.benchmark_code === "string"
      ? req.query.benchmark_code.trim()
      : "";
    if (!benchmarkCode) {
      res.status(400).json({ error: "Missing 'benchmark_code' parameter" });
      return;
    }
    const date = typeof req.query.date === "string" ? req.query.date.trim() : null;
    res.json(await getIntradayMovementsPrevDayOhlc(benchmarkCode, date || null));
  } catch (err) {
    console.error("[live-data/intraday-movements/prev-day-ohlc] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/intraday-movements", async (req: Request, res: Response) => {
  try {
    const benchmarkCode = typeof req.query.benchmark_code === "string"
      ? req.query.benchmark_code.trim()
      : "";
    if (!benchmarkCode) {
      res.status(400).json({ error: "Missing 'benchmark_code' parameter" });
      return;
    }
    const date = typeof req.query.date === "string" ? req.query.date.trim() : null;
    res.json(await getIntradayMovements(benchmarkCode, date || null));
  } catch (err) {
    console.error("[live-data/intraday-movements] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// ---- Sec-Alloc Live Attribution data — per-industry weighted/equal
//      aggregates at one 5-min tick from the live schema tables.
//      weighted_available drives the UI "By Trading Amt" disable state.
//      GET /api/live-data/sec-alloc-live/attribution
router.get("/sec-alloc-live/attribution", async (req: Request, res: Response) => {
  try {
    const benchmarkCode = typeof req.query.benchmark_code === "string"
      ? req.query.benchmark_code.trim()
      : "";
    const time = typeof req.query.time === "string" ? req.query.time.trim() : "";
    const date = typeof req.query.date === "string" ? req.query.date.trim() : null;
    if (!benchmarkCode || !time) {
      res.status(400).json({ error: "Missing 'benchmark_code' or 'time' parameter" });
      return;
    }
    res.json(await getSecAllocLiveAttribution(benchmarkCode, date || null, time));
  } catch (err) {
    console.error("[live-data/sec-alloc-live/attribution] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// ---- Sec-Alloc Live Attribution pipeline trigger.
//      The pipeline is split into THREE processes, selected via body.mode:
//        • "live" (default) — the 5-min equal-weight tick path. Fired by
//          the App-root keeper (every route) + the Market Movements page
//          refresh. No yday-ref dependency: prev close = prev-day last
//          5-min bar close, fallback TRUE rows. Fast (~seconds).
//        • "ref" — the FULL yday-ref chain, run sequentially and deduped
//          by process-id-tag (a second POST while ANY phase runs resolves
//          immediately with already_running: true):
//            1. downloads.index.csindex.quote --ensure-prev-trading-day
//               (tag "…:ref:dl") — TARGETED refresh: skip all network
//               for codes whose local CSVs already contain the prev
//               trading day; only laggards fetch the 1m window (PE /
//               intraday stay nightly-owned). Failure is non-fatal.
//            2. builds.index.baseline --refresh-estimated-days 10 (tag
//               "…:ref:base") — rebuild recent ESTIMATED daily rows from
//               the fresh CSVs so prev-day OHLC is real, not gap-filled.
//               Failure is non-fatal (logged, chain continues).
//            3. live.sec_alloc_live_attribution --mode ref
//               --rebuild-latest-date (tag "…:ref") — invalidates this
//               date's tick rows (they may have been computed from
//               stale/estimated closes), then rebuilds daily-close-basis
//               (weighted) ticks with prev closes computed at tick time
//               from stats.index_basic_stats. Fired by the "Build Yday
//               Ref" button on the Market Movements page; may take
//               minutes on the first run of a date.
//      body.process_id_tag overrides the default tag
//      ("sec-alloc-live:<mode>") — the py-runner registry dedupes
//      concurrent spawns of the SAME tag and exposes running-state via
//      the status endpoint below so a page refresh can restore the
//      button's spinning state. The modes use separate PG advisory
//      locks in Python so they never block each other.
//        • "compute" — ON-DEMAND backfill of a selected date (body.date
//          REQUIRED, optional body.benchmark csv): recomputes that
//          date's tick rows from the base tables (weighted pass +
//          fallback fill; idempotent). This is the same py main the
//          attribution GET service auto-invokes when a requested
//          (benchmark, date) has no tick rows — exposed here for
//          explicit/manual triggering and status tracking.

/** POST /api/live-data/sec-alloc-live/run
 *  body: { mode?: "live" | "ref" | "compute", date?: string,
 *          benchmark?: string, process_id_tag?: string } */
router.post("/sec-alloc-live/run", async (req: Request, res: Response) => {
  const mode: "live" | "ref" | "compute" =
    req.body?.mode === "ref" ? "ref"
    : req.body?.mode === "compute" ? "compute"
    : "live";
  try {
    if (mode === "compute") {
      const computeDate: string =
        typeof req.body?.date === "string" ? req.body.date.trim() : "";
      if (!/^\d{4}-\d{2}-\d{2}$/.test(computeDate)) {
        res.status(400).json({
          success: false,
          mode,
          stderr_tail: "mode 'compute' requires body.date as YYYY-MM-DD",
        });
        return;
      }
      const benchmark: string =
        typeof req.body?.benchmark === "string" && req.body.benchmark.trim()
          ? req.body.benchmark.trim()
          : "";
      const tag: string =
        (typeof req.body?.process_id_tag === "string" && req.body.process_id_tag.trim())
        || `sec-alloc-live:compute:${benchmark || "all"}:${computeDate}`;
      const args = ["--mode", "compute", "--date", computeDate];
      if (benchmark) {
        args.push("--benchmark", benchmark);
      }
      const result = await runPythonModule(
        "live.sec_alloc_live_attribution",
        args,
        { processIdTag: tag },
      );
      res.json({
        success: result.success,
        mode,
        process_id_tag: tag,
        already_running: result.already_running === true,
        stdout_tail: result.stdout.slice(-2000),
        stderr_tail: result.stderr.slice(-2000),
      });
      return;
    }
    const tag: string =
      (typeof req.body?.process_id_tag === "string" && req.body.process_id_tag.trim())
      || `sec-alloc-live:${mode}`;
    if (mode === "ref") {
      // Whole-chain dedupe: if ANY phase of another ref chain is running
      // (CSV downloads, baseline rebuild, or the ref process itself),
      // resolve immediately instead of racing a duplicate spawn into the
      // gap between phases.
      const phaseTags = [tag, `${tag}:dl`, `${tag}:base`];
      if (phaseTags.some((t) => isPythonProcessRunning(t))) {
        return res.json({
          success: true,
          mode,
          process_id_tag: tag,
          already_running: true,
          stdout_tail: "",
          stderr_tail: "",
        });
      }
      // Step 1: refresh the local CSIndex CSVs (prev-day EOD source).
      // Targeted mode: only codes whose CSVs lack the prev trading day
      // hit the network. Non-fatal on failure — the chain continues with
      // the CSVs on disk.
      const dl = await runPythonModule(
        "downloads.index.csindex.quote",
        ["--ensure-prev-trading-day"],
        { processIdTag: `${tag}:dl` },
      );
      if (!dl.success && !dl.already_running) {
        console.error(
          "[live-data/sec-alloc-live/run] downloads pre-step failed " +
          `(exit ${dl.exitCode}); continuing:`, dl.stderr.slice(-500),
        );
      }
      // Step 2: rebuild recent ESTIMATED daily rows from the fresh CSVs
      // so prev-day OHLC is real. Non-fatal on failure — the ref pass
      // works with whatever stats.index_basic_stats already has.
      const base = await runPythonModule(
        "builds.index.baseline",
        ["--refresh-estimated-days", "10"],
        { processIdTag: `${tag}:base` },
      );
      if (!base.success && !base.already_running) {
        console.error(
          "[live-data/sec-alloc-live/run] baseline pre-step failed " +
          `(exit ${base.exitCode}); continuing:`, base.stderr.slice(-500),
        );
      }
      // Step 3: the ref process — invalidates this date's ref + tick
      // rows first (they may have been built from stale/estimated
      // closes), then rebuilds the heavy ref + weighted ticks.
      const result = await runPythonModule(
        "live.sec_alloc_live_attribution",
        ["--mode", mode, "--rebuild-latest-date"],
        { processIdTag: tag },
      );
      return res.json({
        success: result.success,
        mode,
        process_id_tag: tag,
        already_running:
          result.already_running === true || dl.already_running === true
          || base.already_running === true,
        stdout_tail: (dl.stdout + "\n" + base.stdout + "\n" + result.stdout)
          .slice(-2000),
        stderr_tail: (dl.stderr + "\n" + base.stderr + "\n" + result.stderr)
          .slice(-2000),
      });
    }
    const result = await runPythonModule(
      "live.sec_alloc_live_attribution",
      ["--mode", mode],
      { processIdTag: tag },
    );
    res.json({
      success: result.success,
      mode,
      process_id_tag: tag,
      already_running: result.already_running === true,
      stdout_tail: result.stdout.slice(-2000),
      stderr_tail: result.stderr.slice(-2000),
    });
  } catch (err) {
    console.error("[live-data/sec-alloc-live/run] error:", err);
    res.status(500).json({ success: false, mode, stderr_tail: String(err) });
  }
});

/** GET /api/live-data/sec-alloc-live/run/status?process_id_tag=a,b
 *  → { status: { [tag]: boolean } } — running-state of the tags, so the
 *  UI can detect a process started before a page refresh and spin the
 *  button until it exits. */
router.get("/sec-alloc-live/run/status", async (req: Request, res: Response) => {
  try {
    const raw = typeof req.query.process_id_tag === "string"
      ? req.query.process_id_tag : "";
    const tags = raw.split(",").map((t) => t.trim()).filter(Boolean);
    res.json({ status: getPythonProcessStatus(tags) });
  } catch (err) {
    console.error("[live-data/sec-alloc-live/run/status] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// ---------------------------------------------------------------------------
//  Trading Signals (analysis scheme) — live breach records for the
//  analysis_signals.signal_strategies threshold set.
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
 *  with ACTIVE analysis_signals.signal_strategies configs of the given
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
 *  `python -m analyze.analysis_signals --sec-type <csv>`: the signals
 *  pipeline (re-emits analysis_signals.signal_strategies +
 *  analysis_signals.history_signals for the missing months plus the
 *  newest refresh months, from the analysis_forecasts buckets).
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
      "analyze.analysis_signals",
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
 *  the ACTIVE analysis_signals.signal_strategies configs (signal menu;
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
 *  &signal_type=&signal_sub_type=
 *  → { sec_type, code, signals: [...] } — EVERY live_signals row of the
 *  code (newest first): the row-expansion panel's history-signals table +
 *  buy/sell markers on the code-trend chart. signal_type / signal_sub_type
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
    const signals = await fetchTradingSignalHistory(
      secType, code, signalType, signalSubType,
    );
    res.json({
      sec_type: sec_type_valid(secType),
      code,
      signal_type: signalType,
      signal_sub_type: signalSubType,
      signals,
    });
  } catch (err) {
    const status = (err as { status?: number })?.status ?? 500;
    console.error("[live-data/trading-signals/history] error:", err);
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
    let [signals, availableDates] = await Promise.all([
      fetchTriggeredSignals(secType, dateParam),
      fetchTradingSignalDates(secType),
    ]);
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
