/**
 * GET /sec-alloc-live/attribution, POST /sec-alloc-live/run and
 * GET /sec-alloc-live/run/status — the sec-alloc live attribution read,
 * pipeline trigger (live / ref / compute modes) and run-state poll.
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import { wrapAsync, strParam, HttpError } from "../../lib/http.js";
import { runPythonModule, getPythonProcessStatus, isPythonProcessRunning } from "../../services/infra/py-runner.service.js";
import { getSecAllocLiveAttribution } from "../../services/sec/sec-alloc-live-attribution.service.js";

const router = Router();

// ---- Sec-Alloc Live Attribution data — per-industry weighted/equal
//      aggregates at one 5-min tick from the live schema tables.
//      weighted_available drives the UI "By Trading Amt" disable state.
//      GET /api/live-data/sec-alloc-live/attribution
router.get("/sec-alloc-live/attribution", wrapAsync(async (req: Request, res: Response) => {
  const benchmarkCode = strParam(req, "benchmark_code") ?? "";
  const time = strParam(req, "time") ?? "";
  if (!benchmarkCode || !time) {
    throw new HttpError(400, "MISSING_PARAM", "Missing 'benchmark_code' or 'time' parameter");
  }
  const date = strParam(req, "date") ?? null;
  res.json(await getSecAllocLiveAttribution(benchmarkCode, date, time));
}));

// ---- Sec-Alloc Live Attribution pipeline trigger.
//      The pipeline is split into THREE processes, selected via body.mode:
//        • "live" (default) — the 5-min equal-weight tick path. Fired by
//          the App-root keeper (every route) + the Market Movements page
//          refresh. No yday-ref dependency: prev close = prev-day last
//          5-min bar close, fallback TRUE rows. Fast (~seconds).
//        • "ref" — the FULL yday-ref chain, run sequentially and deduped
//          by process-id-tag (a second POST while ANY phase runs resolves
//          immediately with already_running: true):
//            1. downloads.index.csindex.price --ensure-prev-trading-day
//               (tag "…:ref:dl") — TARGETED refresh: skip all network
//               for codes whose local CSVs already contain the prev
//               trading day; only laggards fetch the 1m window (PE /
//               intraday stay nightly-owned). Failure is non-fatal.
//            2. builds.index.daily --refresh-estimated-days 10 (tag
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
router.post("/sec-alloc-live/run", wrapAsync(async (req: Request, res: Response) => {
  const mode: "live" | "ref" | "compute" =
    req.body?.mode === "ref" ? "ref"
    : req.body?.mode === "compute" ? "compute"
    : "live";
  if (mode === "compute") {
    const computeDate: string =
      typeof req.body?.date === "string" ? req.body.date.trim() : "";
    if (!/^\d{4}-\d{2}-\d{2}$/.test(computeDate)) {
      throw new HttpError(400, "INVALID_PARAM", "mode 'compute' requires body.date as YYYY-MM-DD");
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
    const phaseRunning = await Promise.all(
      phaseTags.map((t) => isPythonProcessRunning(t)),
    );
    if (phaseRunning.some(Boolean)) {
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
    // the CSVs on disk. (The price service — the former
    // downloads.index.csindex.quote, split 2026-11-23.)
    const dl = await runPythonModule(
      "downloads.index.csindex.price",
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
      "builds.index.daily",
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
}));

/** GET /api/live-data/sec-alloc-live/run/status?process_id_tag=a,b
 *  → { status: { [tag]: boolean } } — running-state of the tags, so the
 *  UI can detect a process started before a page refresh and spin the
 *  button until it exits. */
router.get("/sec-alloc-live/run/status", wrapAsync(async (req: Request, res: Response) => {
  const raw = strParam(req, "process_id_tag") ?? "";
  const tags = raw.split(",").map((t) => t.trim()).filter(Boolean);
  res.json({ status: await getPythonProcessStatus(tags) });
}));

export default router;
