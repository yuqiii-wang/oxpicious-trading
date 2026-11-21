/**
 * Run / Train strategy scripts — submits the Python backtest and the
 * nested trainer to the server-cluster STRATEGIST (strategy.* module
 * prefix → 127.0.0.1:8772) via the shared py-runner service and returns
 * at the cluster ACCEPT (2026-11-18 async submit — a 50-trial training
 * must not hold the HTTP request open for its whole runtime). No
 * process is spawned locally. The frontend calls these when the user
 * clicks "Run Strategy" / "Train Model"; the job's outcome travels
 * through GET /singleton/process-status (enriched per-tag registry
 * entry) and GET /singleton/process-result (outcome + log tail) under
 * the same process-id-tag.
 */
import { submitPythonModule, type SubmitScriptResult } from "../infra/py-runner.service.js";
import type { MaSpreadSecType } from "../../../shared/types.js";
import {
  DEFAULT_FAULT_TOLERANCE,
  DEFAULT_STRATEGY_NAME,
} from "./_shared.js";

/** Result of submitting a strategy script (re-exported from py-runner
 *  service) — the accept receipt, not the process's outcome. */
export type RunStrategyResult = SubmitScriptResult;

/**
 * Run the backtest (`strategy.singleton_trading`) for one
 * (sec_type, code). The backtest script also computes + upserts risk metrics
 * internally (via `strategy._risks.compute_and_upsert_risks`), so no separate
 * risk command is needed.
 *
 * When `force` is true (default), passes `--force` to Python so existing
 * seq rows are deleted and replaced. When `force` is false, the Python
 * script will skip already-existing runs (used after the user confirms
 * they want to re-run).
 *
 * `faultTolerance` defaults to the pinned 10% (see
 * `_shared.DEFAULT_FAULT_TOLERANCE`); >0 shifts BUY fills adversely
 * (engine stress), runs the two-pass stress annotation, and appends the
 * `_ft{N}` suffix to the RUN identity in strategy_identity. Config keys
 * (algo_configs / training_runs) stay ft-less.
 *
 * Returns at the cluster accept (accepted / already_running / refusal);
 * the frontend polls /singleton/process-status for the running state and
 * reloads from DB when the tag exits.
 */
export async function runStrategyScript(
  rawCode: string,
  rawSecType: string | undefined | null,
  serializedAlgo: string = DEFAULT_STRATEGY_NAME,
  faultTolerance: number = DEFAULT_FAULT_TOLERANCE,
  force: boolean = true,
  processIdTag?: string,
): Promise<RunStrategyResult> {
  const secType = (rawSecType as MaSpreadSecType) ?? "index";
  const code = rawCode.trim();
  // serializedAlgo is either an algo name ("macd") or a serialized selection
  // ("macd,mov_rsi") — both are understood by Python's
  // _parse_algo_arg in strategy/singleton_trading/__main__.py.
  const args = ["--algo", serializedAlgo, "--sec-type", secType, "--codes", code];
  if (force) args.push("--force");
  if (faultTolerance && faultTolerance > 0) {
    // Clamp to the supported range (0-20) for safety.
    const ft = Math.max(0, Math.min(20, Number(faultTolerance) || 0));
    if (ft > 0) args.push("--fault-tolerance", String(ft));
  }
  return submitPythonModule("strategy.singleton_trading", args, {
    processIdTag:
      processIdTag?.trim() ||
      `singleton-run:${secType}:${code}:${serializedAlgo}:${faultTolerance}`,
  });
}

/**
 * Train Model — submit the nested trainer
 * (`python -m strategy.factors_and_algos._optm_engine`) to the cluster
 * strategist.
 *
 * BINARY (`serializedAlgo` is one family, e.g. "macd"): the 5-step plan —
 * (1) Set A signal params (conf threshold + algo tunable space) run an
 * in-memory TPE study maximizing the Omega ratio of per-exit returns
 * (hard constraint: >55% positive monthly PnL); (2) top-K distinct Set A
 * candidates; (3) analytical Kelly sizing per candidate (f*=μ/σ², capped
 * at 20%, ×0.25 fractional); (4) per candidate a vanilla grid over the
 * Set B execution params maximizes the Calmar ratio on the out-of-sample
 * split (hard constraint: max drawdown ≤ 25%); (5) the best combo under
 * the DD cap is selected.
 *
 * MIXED (`serializedAlgo` is a selection, e.g. "macd,mov_rsi"): step 1
 * is REPLACED by the exhaustive Set C comb grid — the curated search
 * over the portfolio's weight rules (adaptive family weights × regime
 * gate × vol-state × blend threshold), graded by Calmar on IS with the
 * official risk score as tiebreaker. The trained row (comb block +
 * Set B keys, params_version 3) is upserted into strategy.algo_configs
 * under the ft-less config name (the family name, or "portfolio:…"),
 * so the NEXT "Run Strategy" run uses it at ANY fault tolerance.
 *
 * The trainer's FINAL STEP (2026-11-18, user-directed) is the trained
 * strategy's own run: the Python trainer spawns the run-once CLI
 * (`strategy.singleton_trading` — this file's `runStrategyScript`
 * command surface) for the same (algo, sec-type, codes) with --force at
 * the pinned default fault tolerance, so a finished training leaves a
 * real run-history record with no browser open. One job covers train +
 * run (the run tag never becomes active — the train tag covers the
 * whole thing); the submission resolves at the cluster accept, the
 * outcome is polled via the process-status endpoints.
 */
export async function runTrainingScript(
  rawCode: string,
  rawSecType: string | undefined | null,
  serializedAlgo: string = DEFAULT_STRATEGY_NAME,
  trials: number = 50,
  processIdTag?: string,
): Promise<RunStrategyResult> {
  const secType = (rawSecType as MaSpreadSecType) ?? "index";
  const code = rawCode.trim();
  // The FULL serialized selection goes to the trainer verbatim — a
  // single family trains binary mode, a comma list trains the mixed
  // portfolio (Set C comb grid). Python parses + validates the names.
  const args = [
    "--algo", serializedAlgo,
    "--sec-type", secType,
    "--codes", code,
    "--trials", String(Math.max(1, Math.min(500, Number(trials) || 50))),
  ];
  return submitPythonModule("strategy.factors_and_algos._optm_engine", args, {
    processIdTag:
      processIdTag?.trim() || `singleton-train:${secType}:${code}:${serializedAlgo}`,
  });
}
