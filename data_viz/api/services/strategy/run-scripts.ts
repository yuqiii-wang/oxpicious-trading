/**
 * Run / Train strategy scripts — spawns the Python backtest and the Optuna
 * param optimizer via the shared py-runner service and waits for exit.
 * The frontend calls these when the user clicks "Run Strategy" /
 * "Train Model", then reloads data from DB on success.
 */
import { runPythonModule, type RunScriptResult } from "../py-runner.service.js";
import type { MaSpreadSecType } from "../../../shared/types.js";
import { DEFAULT_STRATEGY_NAME } from "./_shared.js";

/** Result of running a strategy script (re-exported from py-runner service). */
export type RunStrategyResult = RunScriptResult;

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
 * When `faultTolerance` is in (0, 20], passes `--fault-tolerance <ft>` to
 * Python, which runs a two-pass stress test: baseline run finds decision
 * dates, then OHLC is adversely perturbed on those dates (BUY up, SELL down)
 * by `ft%` of `|delta_close|`, indicators are recomputed, and the algo
 * re-runs on stressed data. The strategy_name gets an `_ft{N}` suffix so
 * the FT variant is a distinct strategy in the DB.
 *
 * Returns after the process exits. The frontend then invalidates cache and
 * reloads from DB to pick up the fresh results.
 */
export async function runStrategyScript(
  rawCode: string,
  rawSecType: string | undefined | null,
  serializedAlgo: string = DEFAULT_STRATEGY_NAME,
  faultTolerance: number = 0,
  force: boolean = true,
  processIdTag?: string,
): Promise<RunStrategyResult> {
  const secType = (rawSecType as MaSpreadSecType) ?? "index";
  const code = rawCode.trim();
  // serializedAlgo is either an algo name ("macd") or a serialized selection
  // ("macd:0.5,bb:0.5") — both are understood by Python's
  // _parse_algo_arg in strategy/singleton_trading/__main__.py.
  const args = ["--algo", serializedAlgo, "--sec-type", secType, "--codes", code];
  if (force) args.push("--force");
  if (faultTolerance && faultTolerance > 0) {
    // Clamp to the supported range (0-20) for safety.
    const ft = Math.max(0, Math.min(20, Number(faultTolerance) || 0));
    if (ft > 0) args.push("--fault-tolerance", String(ft));
  }
  return runPythonModule("strategy.singleton_trading", args, {
    processIdTag:
      processIdTag?.trim() ||
      `singleton-run:${secType}:${code}:${serializedAlgo}:${faultTolerance}`,
  });
}

/**
 * Train Model — spawn the nested hybrid param trainer
 * (`python -m strategy.factors_and_algos._optm_engine`) via WSL.
 *
 * 5-step master plan: (1) Stage A signal params (conf threshold + algo
 * tunable space) run an in-memory TPE study maximizing the Omega ratio
 * of per-exit returns (hard constraint: >55% positive monthly PnL) with
 * Set B at neutral defaults; (2) top-K distinct Set A candidates are
 * extracted; (3) analytical Kelly sizing per candidate (f*=μ/σ², capped
 * at 20%, ×0.25 fractional); (4) per candidate a vanilla grid over the
 * Set B execution params maximizes the Calmar ratio on the out-of-sample
 * split (hard constraint: max drawdown ≤ 25%); (5) the best combo under
 * the DD cap is selected. The combined best params are upserted into
 * strategy.algo_configs so the NEXT "Run Strategy" run uses them. No
 * strategy_identity / trade_decision rows are written by training itself.
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
  // Training only supports a single algo (binary mode) — take the first
  // component of the serialized selection.
  const algoName = serializedAlgo.split(",")[0].split(":")[0].trim() ||
    DEFAULT_STRATEGY_NAME;
  const args = [
    "--algo", algoName,
    "--sec-type", secType,
    "--codes", code,
    "--trials", String(Math.max(1, Math.min(500, Number(trials) || 50))),
  ];
  return runPythonModule("strategy.factors_and_algos._optm_engine", args, {
    processIdTag:
      processIdTag?.trim() || `singleton-train:${secType}:${code}:${algoName}`,
  });
}
