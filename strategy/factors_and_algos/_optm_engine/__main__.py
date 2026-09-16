"""CLI entry: ``python -m strategy.factors_and_algos._optm_engine``.

The "Train Model" entry point for the UI strategy page. Drives the
NESTED hybrid trainer (``training/trainer.py``) — the 5-step master
plan that separates the two parameter sets and gives each its own
optimizer, reusing the existing regime losses (``loss/``):

  Step 1  Set A SIGNAL params (conf_threshold + algo TUNABLE_SPACE):
      Optuna (TPE) with Set B at neutral defaults (entry lag 0 / exit
      lag 0). Loss: −Omega (Omega Ratio of per-exit returns; hard
      constraint > 55% positive monthly PnL). Evaluated on the IS
      split (default: first 80% of each code's rows).

  Step 2  Top-K distinct Set A candidates by Stage-A loss (default 5).

  Step 3  Analytical Kelly per candidate: f* = μ/σ² of the raw
      per-exit returns, capped at 20% portfolio risk, × 0.25 (fractional
      Kelly) → the static base amount (fixed position size for Set B).

  Step 4  Set B EXECUTION params (buy/sell_exec_delay,
      min_holding_period): per candidate, an exhaustive vanilla grid
      on the OOS split with the Kelly-derived amount as position size.
      Loss: −Calmar (total return / max drawdown; hard constraint max
      DD ≤ 25% — breaching grid points are discarded).

  Step 5  Final selection: the (Set A, Set B) combo with the best
      Calmar under the DD cap across all candidates, plus a
      full-series sanity check of the winner (report only).

Per-trial backtests run purely in-memory against fetch-once data (no
strategy_identity / trade_decision rows are written). The combined
best params (Set A ∪ Set B) are upserted into ``strategy.algo_configs``
so the next "Run Strategy" run uses them automatically.

The :class:`OptmEngineStrategy` entry class (a :class:`StrategyPipeline`
subclass) owns the runtime lifecycle + DB connection lifecycle.
Bootstrap-first layout with one wrinkle: the ``--gpu`` mode must be
pre-scanned from argv BEFORE activation (the full argparse runs later
inside execute()), so the module top calls
``bootstrap_runtime(_gpu_mode_from_argv())`` and execute() skips the
already-completed bootstrap.

Examples
--------
    python -m strategy.factors_and_algos._optm_engine \
        --algo macd --sec-type index --codes 000300 --trials 300

    python -m strategy.factors_and_algos._optm_engine \
        --algo macd --sec-type index --codes 000300 000922 \
        --trials 300 --top-k 5 --seed 42 --oos-frac 0.2 \
        --statics-json '{"fee_rate": 0.001, "slippage_band": 0.03}'
"""
from __future__ import annotations

import json
import sys

# GPU mode pre-scanned from argv — the module-level imports below (optuna,
# trainer, ...) pull pandas, so activation MUST happen right here with the
# requested mode before them.
from _common.data_pipeline import bootstrap_runtime


def _gpu_mode_from_argv(default: str = "auto") -> str:
    """Minimal argv pre-scan for --gpu {auto,on,off} (full parse later)."""
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--gpu" and i + 1 < len(argv):
            mode = argv[i + 1]
            return mode if mode in ("auto", "on", "off") else default
        if a.startswith("--gpu="):
            mode = a.split("=", 1)[1]
            return mode if mode in ("auto", "on", "off") else default
    return default


bootstrap_runtime(_gpu_mode_from_argv())

# Imported AFTER the GPU decision so cudf.pandas patches pandas first.
import optuna

from strategy._common.constants import DEFAULT_BUY_NOTIONAL
from strategy._trading.constants import FEE_RATE, SLIPPAGE_BAND
from strategy.factors_and_algos import get_algo
from strategy.factors_and_algos._optm_engine.objective import OptmContext
from strategy.factors_and_algos._optm_engine.persist import (
    upsert_best_params_for_codes,
)
from strategy.factors_and_algos._optm_engine.training import training_store
from strategy.factors_and_algos._optm_engine.training.trainer import (
    NestedTrainer,
)

from _common.log_setup import setup_logging
from strategy._common.strategy_base import StrategyPipeline

logger = setup_logging("_optm_engine")


def _split_is_oos(dfs: dict, oos_frac: float):
    """Split each code's series into IS (Step 1) / OOS (Step 4) rows.

    Codes too short to split meaningfully (< 60 rows on either side)
    run BOTH segments on the full series (with a warning).
    """
    if oos_frac <= 0.0:
        return dfs, dfs
    is_dfs, oos_dfs = {}, {}
    for code, df in dfs.items():
        n = len(df)
        cut = int(round(n * (1.0 - oos_frac)))
        if cut < 60 or (n - cut) < 60:
            logger.info(f"  warning: {code} too short to split ({n} rows) — "
                  f"both segments use the full series")
            is_dfs[code] = df
            oos_dfs[code] = df
            continue
        is_dfs[code] = df.iloc[:cut]
        oos_dfs[code] = df.iloc[cut:]
    return is_dfs, oos_dfs


class OptmEngineStrategy(StrategyPipeline):
    """``python -m strategy.factors_and_algos._optm_engine`` — nested
    hybrid param trainer for pluggable algos."""

    component = "_optm_engine"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--algo", default="macd",
                            help="Algo to train (registry name). Default: macd.")
        parser.add_argument("--sec-type", choices=("index", "etf", "stock"),
                            default="index")
        parser.add_argument("--codes", nargs="+", required=True,
                            help="Security code(s) to train on.")
        parser.add_argument("--trials", type=int, default=50,
                            help="Step 1 TPE trial count over Set A. Default: 50.")
        parser.add_argument("--top-k", type=int, default=5,
                            help="Step 2 distinct Set A candidates carried into "
                                 "the Kelly + grid stages. Default: 5.")
        parser.add_argument("--seed", type=int, default=None,
                            help="TPESampler seed (reproducibility).")
        parser.add_argument(
            "--statics-json", type=str, default=None,
            help="Static (non-optimized) execution params as a JSON object: "
                 "fee_rate, slippage_band, buy_notional. Defaults come from "
                 "strategy/_trading/constants.py.",
        )
        parser.add_argument(
            "--params-json", type=str, default=None,
            help="Base algo param overrides (JSON object) merged over the "
                 "algo's DEFAULT_PARAMS before the study — e.g. fixing one "
                 "model param outside the search.",
        )
        parser.add_argument("--oos-frac", type=float, default=0.2,
                            help="Fraction of each code's rows reserved as the "
                                 "OUT-OF-SAMPLE segment for the Set B grid "
                                 "(Calmar). Step 1 (Omega) runs on the remaining "
                                 "IS rows. 0 disables the split. Clamped to "
                                 "[0, 0.5]. Default: 0.2.")
        parser.add_argument("--gpu", choices=("auto", "on", "off"), default="auto",
                            help="cudf.pandas acceleration mode. Default: auto.")
        parser.add_argument("--no-upsert", action="store_true",
                            help="Do not write best params to algo_configs "
                                 "(study/dry-run only).")

    def apply_args(self) -> None:
        import optuna

        optuna.logging.set_verbosity(optuna.logging.WARNING)

        # Dedup + validate the code list BEFORE any DB work.
        self.codes = sorted(set(c.strip() for c in self.args.codes if c.strip()))
        if not self.codes:
            logger.error("error: no codes provided")
            sys.exit(2)

        # Statics: execution-cost assumptions — fixed inputs, not searched.
        self.statics: dict = {
            "fee_rate": FEE_RATE,
            "slippage_band": SLIPPAGE_BAND,
            "buy_notional": DEFAULT_BUY_NOTIONAL,
        }
        if self.args.statics_json:
            overrides = json.loads(self.args.statics_json)
            if not isinstance(overrides, dict):
                raise TypeError("--statics-json must be a JSON object")
            self.statics.update(overrides)

        self.oos_frac = min(max(float(self.args.oos_frac), 0.0), 0.5)

    async def run(self) -> None:
        conn = self.conn
        args = self.args
        codes = self.codes
        statics = self.statics
        oos_frac = self.oos_frac

        algo = get_algo(args.algo)
        logger.info(f"\n=== nested training '{algo.ALGO_NAME}' on {args.sec_type} "
              f"{codes} ===")
        logger.info(f"  trials={args.trials}  top-k={args.top_k}  seed={args.seed}  "
              f"oos-frac={oos_frac:.2f}  statics={statics}")
        logger.info(f"  algo TUNABLE_SPACE keys: "
              f"{sorted(getattr(algo, 'TUNABLE_SPACE', {}) or {}) or '(none)'}")

        # Fetch ONCE — every backtest runs purely in-memory.
        df = await algo.fetch_signal_data(conn, args.sec_type, codes)
        if df.empty:
            logger.error("error: fetched data is empty — check codes/sec_type")
            sys.exit(2)
        dfs = {code: g for code, g in df.groupby("code", sort=False)}
        missing = [c for c in codes if c not in dfs]
        if missing:
            logger.info(f"warning: no data for codes {missing} (skipped)")
        codes = [c for c in codes if c in dfs]

        base_params = algo.build_params_from_json(args.params_json)
        is_dfs, oos_dfs = _split_is_oos(dfs, oos_frac)
        if oos_frac > 0.0:
            logger.info(f"  IS/OOS split: step 1 (Omega) on IS, step 4 grid "
                  f"(Calmar) on OOS (last {oos_frac:.0%} per code)")
        ctx = OptmContext(
            algo=algo, sec_type=args.sec_type, codes=codes,
            dfs=is_dfs, oos_dfs=oos_dfs, statics=statics,
            base_params=base_params,
        )

        # ------------------------------------------------------------------
        #  Training-process persistence: 'running' header rows (one per
        #  code) BEFORE the study, so crashed runs stay visible in the
        #  UI; flipped to completed/failed + trials after the study.
        # ------------------------------------------------------------------
        run_ids = [
            await training_store.start_training_run(
                conn, args.sec_type, code, algo.ALGO_NAME,
                trials=args.trials, top_k=args.top_k, seed=args.seed,
                oos_frac=oos_frac, statics=statics, gpu_mode=args.gpu,
            )
            for code in codes
        ]

        # ------------------------------------------------------------------
        #  The nested master plan (steps 1-5, see training/trainer.py)
        # ------------------------------------------------------------------
        trainer = NestedTrainer(
            ctx, seed=args.seed, top_k=args.top_k,
            log=lambda msg: logger.info(msg),
        )
        try:
            result = trainer.run(args.trials)
        except Exception as exc:
            log_text = "\n".join(trainer.log_lines)
            for run_id in run_ids:
                await training_store.finish_training_run(
                    conn, run_id, "failed",
                    error_text=f"{type(exc).__name__}: {exc}",
                    log_text=log_text,
                )
            raise

        # Flip the header rows to 'completed' with the outcome, then
        # bulk-insert the buffered per-point records (both loss types)
        # under EVERY run row — the study is cross-code, but the UI
        # queries runs+trials per code.
        log_text = "\n".join(trainer.log_lines)
        n_rows = 0
        for run_id in run_ids:
            await training_store.finish_training_run(
                conn, run_id, "completed", result=result, log_text=log_text,
            )
            n_rows += await training_store.insert_training_trials(
                conn, run_id, trainer.trial_records,
            )
        logger.info(f"[training] persisted {len(run_ids)} run row(s) + "
              f"{n_rows} trial row(s) (set_a_omega + set_b_calmar)")

        # ------------------------------------------------------------------
        #  Summary
        # ------------------------------------------------------------------
        a_m, b_m = result.best_a_metrics, result.best_b_metrics
        fs = result.full_series_metrics or {}
        logger.info(f"\n=== nested training summary "
              f"(winner: stage-A trial {result.winner_trial_no} of "
              f"{result.n_candidates} candidates × {result.grid_size} "
              f"grid points) ===")
        logger.info(f"  step 1 omega      = {a_m.get('omega')} "
              f"(pos_months {a_m.get('positive_month_fraction')}, "
              f"trades {a_m.get('n_trades')})")
        if result.kelly is not None:
            k = result.kelly
            logger.info(f"  step 3 kelly      = f* {k.full_kelly:.3f} → capped "
                  f"{k.capped_kelly:.3f} → fractional "
                  f"{k.fractional_kelly:.3f} → notional {k.notional:,.0f} "
                  f"(reported static, not persisted)")
        logger.info(f"  step 4/5 calmar   = {b_m.get('calmar')} "
              f"(OOS ret {b_m.get('total_return')}, "
              f"max_dd {b_m.get('max_dd_pct')}, "
              f"trades {b_m.get('n_trades')})")
        if fs:
            logger.info(f"  full-series check = calmar {fs.get('calmar')} "
                  f"(ret {fs.get('total_return')}, "
                  f"max_dd {fs.get('max_dd_pct')}) — report only")
        if a_m.get("no_trades"):
            logger.warning("  WARNING: the winning stage-A trial produced NO trades "
                  "on IS.")
        elif not a_m.get("constraint_ok"):
            logger.warning("  WARNING: no stage-A trial satisfied the >55% "
                  "positive-months constraint — the best omega under the "
                  "penalty was used.")

        logger.info("\n=== combined best params (Set A ∪ Set B) ===")
        for k in sorted(result.best_params):
            logger.info(f"    {k} = {result.best_params[k]}")

        if args.no_upsert:
            logger.info("\n--no-upsert: best params NOT written to algo_configs.")
        else:
            n = await upsert_best_params_for_codes(
                conn, args.sec_type, codes, algo.ALGO_NAME,
                result.best_params,
            )
            logger.info(f"\n[algo_configs] upserted tuned params for {n} code(s) "
                  f"({args.sec_type}, strategy '{algo.ALGO_NAME}', "
                  f"trained row [today, ∞], is_default=FALSE) — next Run "
                  f"Strategy will use them.")


if __name__ == "__main__":
    OptmEngineStrategy().execute()
