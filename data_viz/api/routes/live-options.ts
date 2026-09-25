/**
 * Live Options API routes — intraday OI-weighted moneyness skewness for
 * the Live Data "Options" tab.
 *
 * GET /dates                — spot-bar dates for an underlying
 * GET /oi-skew-intraday     — spot bars + computed series; when the series
 *                             is missing or stale for a not-in-the-future
 *                             date, spawns python -m
 *                             live.options_intraday_skewness --date D
 *                             --underlying U ON DEMAND (the trading
 *                             signals pattern: process-id-tag dedupe +
 *                             bounded wait + per-key cooldown) and
 *                             re-reads.
 * GET /run/status           — process-id tag → running (spinner restore).
 */
import { Router, type Request, type Response } from "express";
import {
  getLiveOiSkewIntraday,
  isSeriesStale,
  listLiveOptionsDates,
} from "../services/live-options.service.js";
import {
  getPythonProcessStatus,
  isPythonProcessRunning,
  runPythonModule,
} from "../services/py-runner.service.js";
import type {
  LiveOptionsOiSkewResponse,
  LiveOptionsRunStatusResponse,
} from "../../shared/types.js";

const router = Router();

const PY_MODULE = "live.options_intraday_skewness";

// The on-demand compute waits at most this long for the python run before
// responding with whatever rows exist (the spawn keeps running past the
// cap — deduped by process-id-tag — and later requests pick its rows up).
const COMPUTE_WAIT_MS = 90_000;
// One compute attempt per (underlying, date) per cooldown window.
const COMPUTE_COOLDOWN_MS = 10 * 60_000;
const computeAt = new Map<string, number>();

function computeAllowed(key: string): boolean {
  const last = computeAt.get(key) ?? 0;
  return Date.now() - last >= COMPUTE_COOLDOWN_MS;
}

/** Asia/Shanghai business day (same resolution as live-data.ts). */
function shanghaiToday(): string {
  const now = new Date();
  const utc = now.getTime() + now.getTimezoneOffset() * 60_000;
  return new Date(utc + 8 * 60 * 60_000).toISOString().slice(0, 10);
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

router.get("/dates", async (req: Request, res: Response) => {
  try {
    const underlying =
      typeof req.query.underlying === "string" ? req.query.underlying : "";
    if (!underlying) {
      res.status(400).json({ error: "Missing 'underlying' query parameter" });
      return;
    }
    const targetType =
      typeof req.query.target_type === "string" ? req.query.target_type : undefined;
    res.json(await listLiveOptionsDates(underlying, targetType));
  } catch (err) {
    console.error("[live-options/dates] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/oi-skew-intraday", async (req: Request, res: Response) => {
  try {
    const underlying =
      typeof req.query.underlying === "string" ? req.query.underlying : "";
    if (!underlying) {
      res.status(400).json({ error: "Missing 'underlying' query parameter" });
      return;
    }
    const dateParam =
      typeof req.query.date === "string" && req.query.date
        ? req.query.date
        : undefined;
    const targetType =
      typeof req.query.target_type === "string" ? req.query.target_type : undefined;

    let resp: LiveOptionsOiSkewResponse = await getLiveOiSkewIntraday({
      underlying,
      date: dateParam,
      target_type: targetType,
    });

    // On-demand compute: rows missing (never computed / pruned) or stale
    // (the day grew a newer spot bar than the series covers) and the date
    // is not in the future → spawn the pipeline, wait bounded, re-read.
    const key = `${underlying}:${resp.date}`;
    if (
      resp.date !== "" &&
      resp.date <= shanghaiToday() &&
      isSeriesStale(resp) &&
      computeAllowed(key)
    ) {
      computeAt.set(key, Date.now());
      const tag = `live-options:compute:${underlying}:${resp.date}`;
      console.log(`[live-options] on-demand compute: ${PY_MODULE} ${key} (tag=${tag})`);
      await Promise.race([
        runPythonModule(PY_MODULE, ["--date", resp.date, "--underlying", underlying], {
          processIdTag: tag,
        }),
        sleep(COMPUTE_WAIT_MS),
      ]);
      resp = await getLiveOiSkewIntraday({
        underlying,
        date: resp.date,
        target_type: targetType,
      });
      resp.computing = true;
    }

    res.json(resp);
  } catch (err) {
    console.error("[live-options/oi-skew-intraday] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/run/status", async (req: Request, res: Response) => {
  try {
    const raw = typeof req.query.process_id_tag === "string" ? req.query.process_id_tag : "";
    const tags = raw.split(",").map((t) => t.trim()).filter(Boolean);
    const out: LiveOptionsRunStatusResponse = {
      status: getPythonProcessStatus(tags),
    };
    res.json(out);
  } catch (err) {
    console.error("[live-options/run/status] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// ----------------------------------------------------------------------------
//  POST /api/live-options/run  (mode "ref" — the Live Options page's
//  "Build Yday Ref" button; mirrors live-data.ts's sec-alloc-live ref chain,
//  deduped by process-id-tag across ALL phases — a second click / page
//  refresh / second tab while ANY phase runs resolves immediately with
//  already_running: true):
//    1. downloads.options.sse.price (tag "…:dl") — refresh the SSE contract
//       listing CSVs (the terms source; the download itself is nightly-owned
//       and cached, so this is a cheap catch-up). Failure is non-fatal.
//    2. builds.options --date <ref_date> (tag "…:build") — force single-date
//       rebuild of the prev-trading-day options snapshot (identity / terms /
//       quote sub-tables) the skewness estimator bases its OI on. builds.
//       options.sse accepts a non-final quotes file for the forced date, so
//       a day the streamer died mid-session is still buildable. Failure is
//       non-fatal (the compute below just re-flags the gap).
//    3. live.options_intraday_skewness --date <date> [--underlying U]
//       (tag "…:compute") — recompute the series for the on-screen
//       underlying-day so the chart refills the moment the chain ends.
//  body: { snapshot_date: "YYYY-MM-DD" (the yday ref to rebuild — the
//          response's snapshot_date), date?: "YYYY-MM-DD" (series date,
//          defaults to the latest), underlying?: string, process_id_tag? }
// ----------------------------------------------------------------------------
router.post("/run", async (req: Request, res: Response) => {
  try {
    const refDate: string =
      typeof req.body?.snapshot_date === "string" ? req.body.snapshot_date.trim() : "";
    if (!/^\d{4}-\d{2}-\d{2}$/.test(refDate)) {
      res.status(400).json({
        success: false,
        mode: "ref",
        stderr_tail: "body.snapshot_date (YYYY-MM-DD, the yday ref to rebuild) is required",
      });
      return;
    }
    const seriesDate: string =
      typeof req.body?.date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(req.body.date.trim())
        ? req.body.date.trim()
        : "";
    const underlying: string =
      typeof req.body?.underlying === "string" && req.body.underlying.trim()
        ? req.body.underlying.trim()
        : "";
    const tag: string =
      (typeof req.body?.process_id_tag === "string" && req.body.process_id_tag.trim())
      || "live-options:ref";

    // Whole-chain dedupe (see live-data.ts sec-alloc-live ref mode).
    const phaseTags = [tag, `${tag}:dl`, `${tag}:build`, `${tag}:compute`];
    if (phaseTags.some((t) => isPythonProcessRunning(t))) {
      res.json({
        success: true,
        mode: "ref" as const,
        process_id_tag: tag,
        already_running: true,
        stdout_tail: "",
        stderr_tail: "",
      });
      return;
    }

    // Step 1: refresh the SSE contract-listing CSVs (terms source). Non-fatal.
    const dl = await runPythonModule("downloads.options.sse.price", [], {
      processIdTag: `${tag}:dl`,
    });
    if (!dl.success && !dl.already_running) {
      console.error(
        "[live-options/run] downloads pre-step failed " +
        `(exit ${dl.exitCode}); continuing:`, dl.stderr.slice(-500),
      );
    }

    // Step 2: rebuild the yday snapshot (all venue legs; SSE accepts a
    // non-final quotes file for the forced date). Non-fatal.
    const build = await runPythonModule(
      "builds.options",
      ["--date", refDate],
      { processIdTag: `${tag}:build` },
    );
    if (!build.success && !build.already_running) {
      console.error(
        "[live-options/run] build pre-step failed " +
        `(exit ${build.exitCode}); continuing:`, build.stderr.slice(-500),
      );
    }

    // Step 3: recompute the series for the on-screen underlying-day.
    const computeArgs = seriesDate ? ["--date", seriesDate] : [];
    if (underlying) {
      computeArgs.push("--underlying", underlying);
    }
    const result = await runPythonModule(PY_MODULE, computeArgs, {
      processIdTag: `${tag}:compute`,
    });

    // A finished compute invalidates the route's own cooldown so a
    // subsequent GET re-arms the on-demand path immediately.
    if (underlying && seriesDate) {
      computeAt.set(`${underlying}:${seriesDate}`, 0);
    }

    res.json({
      success: result.success,
      mode: "ref" as const,
      process_id_tag: tag,
      already_running:
        result.already_running === true || dl.already_running === true
        || build.already_running === true,
      stdout_tail: (dl.stdout + "\n" + build.stdout + "\n" + result.stdout)
        .slice(-2000),
      stderr_tail: (dl.stderr + "\n" + build.stderr + "\n" + result.stderr)
        .slice(-2000),
    });
  } catch (err) {
    console.error("[live-options/run] error:", err);
    res.status(500).json({ success: false, mode: "ref", stderr_tail: String(err) });
  }
});

export default router;
