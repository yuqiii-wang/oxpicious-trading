/**
 * POST /orchestration/run — manual orchestration tier run via the shared
 * serial runner (scripts/run_steps.sh).
 * Full route docs: see index.ts.
 */

import { existsSync } from "fs";
import path from "path";
import { Router, type Request, type Response } from "express";
import {
  isPythonProcessRunning,
  runWslScript,
} from "../../services/py-runner.service.js";
import { REPO_ROOT } from "./_shared.js";

const router = Router();

// ---------------------------------------------------------------------------
//  Orchestration tier run — scripts/run_steps.sh (the Orchestration
//  config tab's title Run button)
// ---------------------------------------------------------------------------

/** POST /api/monitor/orchestration/run { file, dryRun? } — fire-and-forget
 *  spawn of the shared serial runner over ONE orchestration step file:
 *  `bash scripts/run_steps.sh scripts/orchestration/<file>` (the exact
 *  delegation the scripts/<tier>.sh guards end in — calling run_steps.sh
 *  directly IS the manual run, no trading-day/time gate). The steps spawn
 *  one by one and each registers its own heartbeat, so the tier's steps
 *  show up on the Pipeline processes table as they start. dryRun=true
 *  passes the runner's own DRY_RUN=1 knob (echoes the plan, executes
 *  nothing) — verification / smoke only, never surfaced in the UI.
 *  Deduped per tier by process-id-tag; the wsl.exe client is held for the
 *  whole tier (same keeper mechanics as the stream starters). */
router.post("/orchestration/run", (req: Request, res: Response) => {
  const file = typeof req.body?.file === "string" ? req.body.file.trim() : "";
  const dryRun = req.body?.dryRun === true;
  if (!/^[A-Za-z0-9_-]+\.json$/.test(file)) {
    res.status(400).json({
      success: false,
      error: `file must be an orchestration step file name like builds_daily.json, got '${file}'`,
    });
    return;
  }
  // Forward slashes: the path is joined into the WSL bash command, where
  // a Windows separator would not resolve.
  const jsonRel = `scripts/orchestration/${file}`;
  if (!existsSync(path.join(REPO_ROOT, "scripts", "orchestration", file))) {
    res.status(400).json({
      success: false,
      error: `no orchestration step file ${jsonRel}`,
    });
    return;
  }
  const tier = file.replace(/\.json$/, "");
  const tag = `orchestration-run:${tier}`;
  if (isPythonProcessRunning(tag)) {
    res.json({
      success: true,
      started: true,
      alreadyRunning: true,
      tier,
      message: `${tier} is already running (this UI spawn) — deduped, no new process`,
    });
    return;
  }
  const cmd = `${dryRun ? "DRY_RUN=1 " : ""}bash scripts/run_steps.sh ${jsonRel}`;
  void runWslScript(cmd, `run_steps.sh ${file}`, { processIdTag: tag }).then((r) => {
    if (r.success) {
      console.log(`[monitor/orchestration/run] ${file} exited 0`);
    } else {
      console.error(
        `[monitor/orchestration/run] ${file} exited ${r.exitCode}: ${r.stderr.slice(-500)}`,
      );
    }
  });
  res.json({
    success: true,
    started: true,
    tier,
    message: `spawned ${cmd}${dryRun ? " (dry run)" : ""} — steps start one by one, each heartbeating onto Pipeline processes (~60s)`,
  });
});

export default router;
