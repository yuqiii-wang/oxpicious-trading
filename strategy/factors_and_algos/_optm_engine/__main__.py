"""CLI entry: ``python -m strategy.factors_and_algos._optm_engine``.

The "Train Model" entry point for the UI strategy page. Drives the
NESTED hybrid trainer (``training/trainer.py``) — the 5-step master
plan that separates the two parameter sets and gives each its own
optimizer, reusing the existing regime losses (``loss/``). Two modes:

BINARY (``--algo macd``) — the historical single-family flow:

  Step 1  Set A SIGNAL params (conf_threshold + algo TUNABLE_SPACE):
      Optuna (TPE) with Set B at neutral defaults (entry lag 0 / exit
      lag 0). Loss: −Omega (Omega Ratio of per-exit returns; hard
      constraint > 55% positive monthly PnL). Evaluated on the IS
      split (default: first 80% of each code's rows).

  Step 2  Top-K distinct Set A candidates by Stage-A loss (default 5).

MIXED (``--algo macd,mov_rsi``) — Step 1 is REPLACED by the Set C comb
grid (the portfolio's "rules to assign weights under different
scenarios"): an exhaustive curated grid over the ``_comb`` stack —
adaptive family weights (tstat / mean_bar / equal / ewma …) ×
thesis-matched regime gate (120 / 255 / off) × vol-state overlay ×
blend threshold — graded by −Calmar on IS with the official risk score
as tiebreaker (the comb study measured that combs must be judged on
Calmar/DD, never omega alone). The winning comb block rides the
candidate params through the shared steps:

  Step 3  Analytical Kelly per candidate: f* = μ/σ² of the raw
      per-exit returns, capped at 20% portfolio risk, × 0.25 (fractional
      Kelly) → the static base amount (fixed position size for Set B).

  Step 4  Set B EXECUTION params (buy/sell_exec_delay,
      min_holding_period): per candidate, an exhaustive vanilla grid
      on the OOS split with the Kelly-derived amount as position size.
      Loss: −Calmar (total return / max drawdown; hard constraint max
      DD ≤ 25% — breaching grid points are discarded).

  Step 5  Final selection: the best combo under the DD cap across all
      candidates, plus a full-series sanity check of the winner
      (report only).

  Train gate (2026-11-19, default ON at 0.15): the last ``--gate-frac``
  of each code's rows is carved UNTOUCHED before the study; after Step 5
  the winner and the UNTRAINED default (mixed: the shipped DEFAULT_COMB
  champion; binary: the algo's DEFAULT_PARAMS) are graded there through
  the same evaluate_set_b machinery + preference tuple (DD-cap rank,
  loss, official risk). The trained algo_configs row is upserted — and
  the post-training run spawned — ONLY when the winner strictly beats
  the default; on a gate fail the default is retained (the study
  measured the untrained default beating every trained selection on the
  untouched tail). ``--gate-frac 0`` disables. A CSCV-lite PBO
  diagnostic over the IS/OOS candidate pairing is logged with every run.

Per-trial backtests run purely in-memory against fetch-once data (no
strategy_identity / trade_decision rows are written). The combined
best params are upserted into ``strategy.algo_configs`` under the
FT-LESS strategy name (the family name, or the ``portfolio:...`` name
in mixed mode) so the next "Run Strategy" run uses them automatically
at ANY fault tolerance.

THE FINAL STEP of a finished training (user-directed 2026-11-18) is a
REAL strategy run: after the params are persisted, the trainer spawns
the run-once CLI (``strategy.singleton_trading`` — the same entry point
the UI's "Run Strategy" button drives) for the same
(algo, sec-type, codes) with ``--force`` at the pinned default fault
tolerance. The run loads the freshly upserted trained row, and the run
history (``strategy_identity`` seq) records the trained strategy —
independent of any browser being open. ``--no-run`` skips this final
run (study-only invocations; skipped too under ``--no-upsert``, where
the run would load stale params).

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
        --algo macd,mov_rsi --sec-type index --codes 000300 \
        --top-k 5 --oos-frac 0.2

    python -m strategy.factors_and_algos._optm_engine \
        --algo macd --sec-type index --codes 000300 000922 \
        --trials 300 --top-k 5 --seed 42 --oos-frac 0.2 \
        --statics-json '{"fee_rate": 0.001, "slippage_band": 0.03}'
"""
from __future__ import annotations

import json
import sys
from typing import Optional

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
import pandas as pd

from strategy._common.constants import DEFAULT_BUY_NOTIONAL
from strategy._trading.constants import (
    DEFAULT_FAULT_TOLERANCE,
    FEE_RATE,
    SLIPPAGE_BAND,
)
from strategy.factors_and_algos import (
    AlgoSignalCollector,
    get_algo,
    portfolio_name,
)
from strategy.factors_and_algos._optm_engine.backtester import (
    AlgoBacktester,
    CollectorBacktester,
    OptmBacktester,
)
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


async def _run_trained_strategy(algo_arg: str, sec_type: str, codes: list[str]) -> int:
    """Spawn the run-once CLI (``strategy.singleton_trading``) for the
    freshly trained params — the FINAL RESULT of a finished training is a
    real strategy run (user-directed 2026-11-18), identical to the UI's
    "Run Strategy" button BY CONSTRUCTION: the same entry point, the same
    argument surface (``--force`` + the pinned default fault tolerance).

    ``--force`` purges + regenerates the scope, so the run history gains a
    NEW seq record even when an identical run exists. The child inherits
    stdio so its output streams into the same job log; its exit code is
    propagated (a failed run fails the whole command honestly — the
    training_runs row stays 'completed', the study itself succeeded). The
    blocking wait runs in a thread so the trainer's loop stays live.

    Returns the child's exit code (0 = the run wrote its history rows)."""
    import asyncio
    import subprocess

    cmd = [
        sys.executable, "-m", "strategy.singleton_trading",
        "--algo", algo_arg,
        "--sec-type", sec_type,
        "--codes", *codes,
        "--force",
        "--fault-tolerance", str(DEFAULT_FAULT_TOLERANCE),
    ]
    logger.info(f"\n=== post-training run (final result = a Run Strategy "
          f"execution): {' '.join(cmd[1:])} ===")
    completed = await asyncio.to_thread(subprocess.run, cmd)
    logger.info(f"=== post-training run exit code {completed.returncode} ===")
    if completed.returncode != 0:
        from _common.caution_log import record
        record(
            "service", "error",
            "Post-training strategy run failed — run history has no record "
            "of this training",
            dedupe_key="_optm_engine:post-train-run-failed",
            detail={
                "algo": algo_arg, "sec_type": sec_type, "codes": codes,
                "exit_code": completed.returncode,
            },
        )
    return completed.returncode


def _split_is_oos(dfs: dict, oos_frac: float):
    """Split each code's series into IS (Step 1) / OOS (Step 4) rows.

    Codes too short to split meaningfully (< 60 rows on either side)
    run BOTH segments on the full series (with a warning). Slices are
    materialized as COPIES: the collector's blend writes signal/sig
    columns IN PLACE on the passed frame, and shared views would let
    overlapping segments bleed state into each other (the 2026-11-19
    study's contamination probe; production mixed splits already
    materialize copies via concat).
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
        is_dfs[code] = df.iloc[:cut].copy()
        oos_dfs[code] = df.iloc[cut:].copy()
    return is_dfs, oos_dfs


def _split_is_oos_gate(dfs: dict, oos_frac: float, gate_frac: float):
    """Three-way positional split: IS / OOS (the funnel's segments) plus
    the UNTOUCHED gate tail (the train-gate segment, 2026-11-19).

    IS+OOS span the first ``(1 - gate_frac)`` of each code's rows
    (``oos_frac`` splits THAT dev span); the gate tail is the last
    ``gate_frac`` — never read by Steps 1-5, so a winner graded there is
    an honest validation. Per-code <60-row fallbacks mirror
    ``_split_is_oos``; a code whose gate tail is too short (<60 rows)
    DISABLES the gate for the whole study — a starved tail grades on
    noise, which is worse than no gate.

    Returns ``(is_dfs, oos_dfs, gate_dfs)``; ``gate_dfs`` is None when
    the gate is disabled (``gate_frac == 0``) or dropped.
    """
    if gate_frac <= 0.0:
        is_dfs, oos_dfs = _split_is_oos(dfs, oos_frac)
        return is_dfs, oos_dfs, None
    is_dfs, oos_dfs, gate_dfs = {}, {}, {}
    for code, df in dfs.items():
        n = len(df)
        gate_cut = int(round(n * (1.0 - gate_frac)))
        if (n - gate_cut) < 60 or gate_cut < 60:
            logger.info(
                f"  warning: {code} too short for a gate tail ({n} rows, "
                f"tail {n - gate_cut}) — train gate DISABLED for this study")
            is_dfs, oos_dfs = _split_is_oos(dfs, oos_frac)
            return is_dfs, oos_dfs, None
        gate_dfs[code] = df.iloc[gate_cut:].copy()
        dev_cut = int(round(gate_cut * (1.0 - oos_frac)))
        if dev_cut < 60 or (gate_cut - dev_cut) < 60:
            logger.info(
                f"  warning: {code} dev span too short to split "
                f"({gate_cut} rows) — IS and OOS both use the dev span")
            is_dfs[code] = df.iloc[:gate_cut].copy()
            oos_dfs[code] = df.iloc[:gate_cut].copy()
            continue
        is_dfs[code] = df.iloc[:dev_cut].copy()
        oos_dfs[code] = df.iloc[dev_cut:gate_cut].copy()
    return is_dfs, oos_dfs, gate_dfs


def _parse_selection(raw: str) -> dict:
    """Parse the --algo CLI value into ``{family: 1.0}`` (no weights).

    Mirrors ``strategy.singleton_trading.__main__._parse_algo_arg`` (the
    same tolerant comma list; legacy ":weight" suffixes stripped) — kept
    local because importing another package's ``__main__`` would re-run
    its bootstrap. ORACLE is SOLO-ONLY (2026-11-21): a multi-name
    selection carrying "oracle" is rejected — the existing trainer
    optimizes the oracle's confidence-scaling knobs on the oracle
    signals ALONE, never on a blend with other families.
    """
    selection: dict = {}
    for part in raw.split(","):
        name = part.split(":", 1)[0].strip()
        if name:
            selection[name] = 1.0
    if not selection:
        raise ValueError(f"--algo '{raw}' parsed to an empty selection")
    if "oracle" in selection and len(selection) > 1:
        raise SystemExit(
            "oracle is solo-only (oracle mode): the trainer optimizes "
            "the oracle signals alone, never a blend with other "
            f"families — got '--algo {raw}'"
        )
    return selection


def _split_mixed_segments(
    collector: AlgoSignalCollector, oos_frac: float, gate_frac: float = 0.0,
) -> dict:
    """Positional IS/OOS split of the collector's per-algo frames.

    Mirrors ``_split_is_oos``'s per-code positional rule applied to every
    algo's OWN frame (each family has its own date set; the unified frame
    is the union). Returns ``{"is": {algo: df}, "oos": {algo: df},
    "full": {algo: df}, "gate": {algo: df}}`` — the funnel segments swap
    through :class:`CollectorBacktester.prepare`; the ``gate`` segment
    feeds the train-gate backtester built by ``run()`` (it swaps the
    collector's frames to the gate slices via its own ``"oos"`` entry).
    Codes too short to split keep the FULL frame in both segments (same
    fallback as ``_split_is_oos``); with ``gate_frac > 0`` each algo-code
    pair contributes its last ``gate_frac`` rows to the gate segment
    (a pair with too few rows contributes none — the gate decision is
    made globally on the unified frames).
    """
    segments: dict = {"is": {}, "oos": {}, "full": {}, "gate": {}}
    for algo_name, frame in collector._mixed_dfs.items():
        parts_is: list = []
        parts_oos: list = []
        parts_gate: list = []
        for _code, g in frame.groupby("code", sort=False):
            n = len(g)
            gate_cut = (int(round(n * (1.0 - gate_frac)))
                        if gate_frac > 0.0 else n)
            if gate_frac > 0.0 and 0 < gate_cut < n:
                parts_gate.append(g.iloc[gate_cut:].copy())
            cut = int(round(gate_cut * (1.0 - oos_frac)))
            if oos_frac <= 0.0 or cut < 60 or (gate_cut - cut) < 60:
                parts_is.append(g.iloc[:gate_cut])
                parts_oos.append(g.iloc[:gate_cut])
                continue
            parts_is.append(g.iloc[:cut])
            parts_oos.append(g.iloc[cut:gate_cut])
        segments["full"][algo_name] = frame
        segments["is"][algo_name] = (
            pd.concat(parts_is) if parts_is else frame.iloc[0:0])
        segments["oos"][algo_name] = (
            pd.concat(parts_oos) if parts_oos else frame.iloc[0:0])
        segments["gate"][algo_name] = (
            pd.concat(parts_gate) if parts_gate else frame.iloc[0:0])
    return segments


class OptmEngineStrategy(StrategyPipeline):
    """``python -m strategy.factors_and_algos._optm_engine`` — nested
    hybrid param trainer for pluggable algos."""

    component = "_optm_engine"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--algo", default="macd",
                            help="Algo(s) to train (registry names). Single "
                                 "family ('macd') trains BINARY mode "
                                 "(Set A TPE); a comma list "
                                 "('macd,mov_rsi') trains the MIXED "
                                 "portfolio — the Set C comb grid (the "
                                 "portfolio's weight rules) with the "
                                 "result persisted under the "
                                 "'portfolio:...' strategy_name. "
                                 "Default: macd.")
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
        parser.add_argument("--gate-frac", type=float, default=0.15,
                            help="Fraction of each code's rows reserved as the "
                            "UNTOUCHED train-gate tail (2026-11-19): after the "
                            "study the winner must beat the UNTRAINED default "
                            "there (preference rank, loss, official risk) or "
                            "the trained algo_configs row is NOT written and "
                            "the post-training run is skipped. IS/OOS span the "
                            "remaining (1 - gate_frac). 0 disables the gate. "
                            "Clamped to [0, 0.5]. Default: 0.15.")
        parser.add_argument("--gpu", choices=("auto", "on", "off"), default="auto",
                            help="cudf.pandas acceleration mode. Default: auto.")
        parser.add_argument("--no-upsert", action="store_true",
                            help="Do not write best params to algo_configs "
                                 "(study/dry-run only).")
        parser.add_argument("--no-run", action="store_true",
                            help="Skip the post-training strategy run (the "
                                 "finished training's final result is a "
                                 "run-once execution by default; this flag "
                                 "keeps the invocation study-only).")

    def apply_args(self) -> None:
        import optuna

        optuna.logging.set_verbosity(optuna.logging.WARNING)

        # Dedup + validate the code list BEFORE any DB work.
        self.codes = sorted(set(c.strip() for c in self.args.codes if c.strip()))
        if not self.codes:
            logger.error("error: no codes provided")
            sys.exit(2)

        # Statics: execution-cost assumptions — fixed inputs, not searched.
        # fault_tolerance is pinned at the production default so trained
        # params are graded under the same BUY-fill stress runs use;
        # ft_annotate=False skips the per-trial post-hoc stressed-conf
        # annotation (pure study overhead — the engine stress stays on).
        self.statics: dict = {
            "fee_rate": FEE_RATE,
            "slippage_band": SLIPPAGE_BAND,
            "buy_notional": DEFAULT_BUY_NOTIONAL,
            "fault_tolerance": DEFAULT_FAULT_TOLERANCE,
            "ft_annotate": False,
        }
        if self.args.statics_json:
            overrides = json.loads(self.args.statics_json)
            if not isinstance(overrides, dict):
                raise TypeError("--statics-json must be a JSON object")
            self.statics.update(overrides)

        self.oos_frac = min(max(float(self.args.oos_frac), 0.0), 0.5)
        self.gate_frac = min(max(float(self.args.gate_frac), 0.0), 0.5)

    async def run(self) -> None:
        conn = self.conn
        args = self.args
        codes = self.codes
        statics = self.statics
        oos_frac = self.oos_frac
        gate_frac = self.gate_frac

        # -- Mode selection: BINARY (single family) vs MIXED (portfolio) --
        # Mixed mode trains the PORTFOLIO's comb stack (Set C grid: the
        # rules that assign family weights under different scenarios)
        # and persists under the ft-less 'portfolio:...' name — the same
        # key a mixed Run Strategy loads its comb config from.
        selection = _parse_selection(args.algo)
        is_mixed = len(selection) > 1

        backtester: OptmBacktester
        if is_mixed:
            collector = AlgoSignalCollector(selection)
            strategy_name = portfolio_name(selection)
            logger.info(f"\n=== nested training (MIXED) '{strategy_name}' on "
                  f"{args.sec_type} {codes} ===")
            logger.info(f"  families: {sorted(selection)}  top-k={args.top_k}  "
                  f"seed={args.seed}  oos-frac={oos_frac:.2f}  "
                  f"statics={statics}")
            logger.info("  mode: Set C comb grid (exhaustive) replaces "
                  "Set A TPE; steps 2-5 unchanged")
            fetcher = collector
        else:
            algo = get_algo(args.algo)
            strategy_name = algo.ALGO_NAME
            logger.info(f"\n=== nested training '{strategy_name}' on "
                  f"{args.sec_type} {codes} ===")
            logger.info(f"  trials={args.trials}  top-k={args.top_k}  "
                  f"seed={args.seed}  oos-frac={oos_frac:.2f}  "
                  f"statics={statics}")
            logger.info(f"  algo TUNABLE_SPACE keys: "
                  f"{sorted(getattr(algo, 'TUNABLE_SPACE', {}) or {}) or '(none)'}")
            fetcher = algo

        # Fetch ONCE — every backtest runs purely in-memory. Mixed mode
        # populates the collector's per-algo frames as a fetch side
        # effect (the unified df carries only OHLC + keys).
        df = await fetcher.fetch_signal_data(conn, args.sec_type, codes)
        if df.empty:
            logger.error("error: fetched data is empty — check codes/sec_type")
            sys.exit(2)
        dfs = {code: g for code, g in df.groupby("code", sort=False)}
        missing = [c for c in codes if c not in dfs]
        if missing:
            logger.info(f"warning: no data for codes {missing} (skipped)")
        codes = [c for c in codes if c in dfs]

        is_dfs, oos_dfs, gate_dfs = _split_is_oos_gate(dfs, oos_frac, gate_frac)
        if is_mixed:
            base_params: dict = {}
            if args.params_json:
                base_params = json.loads(args.params_json)
                if not isinstance(base_params, dict):
                    raise TypeError("--params-json must be a JSON object")
            segments = _split_mixed_segments(collector, oos_frac, gate_frac)
            backtester = CollectorBacktester(collector, segments)
        else:
            base_params = algo.build_params_from_json(args.params_json)
            backtester = AlgoBacktester(algo)
        if oos_frac > 0.0:
            logger.info(f"  IS/OOS split: step 1 on IS, step 4 grid "
                  f"(Calmar) on OOS (last {oos_frac:.0%} per code)")
        if gate_frac > 0.0 and gate_dfs is not None:
            logger.info(f"  train gate: last {gate_frac:.0%} of each code's "
                  f"rows reserved UNTOUCHED — the trained config ships only "
                  f"if it beats the untrained default there (preference "
                  f"rank, loss, official risk)")
        ctx = OptmContext(
            backtester=backtester, sec_type=args.sec_type, codes=codes,
            dfs=is_dfs, oos_dfs=oos_dfs, statics=statics,
            base_params=base_params, is_mixed=is_mixed,
        )

        # ------------------------------------------------------------------
        #  Training-process persistence: 'running' header rows (one per
        #  code) BEFORE the study, so crashed runs stay visible in the
        #  UI; flipped to completed/failed + trials after the study.
        # ------------------------------------------------------------------
        run_ids = [
            await training_store.start_training_run(
                conn, args.sec_type, code, strategy_name,
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

        # ------------------------------------------------------------------
        #  Diagnostics + train gate (2026-11-19 validation study): the PBO
        #  diagnostic from the buffered records, then the winner-vs-default
        #  gate on the UNTOUCHED tail. Both log through trainer._log so the
        #  lines persist into training_runs.log_text, and both land on the
        #  result BEFORE finish_training_run serializes it.
        # ------------------------------------------------------------------
        trainer.attach_pbo(result)
        gate_pass: Optional[bool] = None
        if gate_frac > 0.0 and gate_dfs:
            if is_mixed:
                gate_bt = CollectorBacktester(collector, {
                    "is": segments["gate"], "oos": segments["gate"],
                    "full": segments["full"],
                })
                gate_ctx = OptmContext(
                    backtester=gate_bt, sec_type=args.sec_type, codes=codes,
                    dfs=gate_dfs, oos_dfs=gate_dfs, statics=statics,
                    base_params=base_params, is_mixed=True,
                )
            else:
                gate_ctx = OptmContext(
                    backtester=backtester, sec_type=args.sec_type,
                    codes=codes, dfs=gate_dfs, oos_dfs=gate_dfs,
                    statics=statics, base_params=base_params,
                    is_mixed=False,
                )
            result.gate_metrics = trainer.run_train_gate(result, gate_ctx)
            gate_pass = bool(result.gate_metrics["passed"])
        elif gate_frac > 0.0:
            trainer._log("  train gate unavailable (gate tail too short) "
                         "— the trained config ships ungated")

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
              f"{n_rows} trial row(s) "
              f"({'set_c_calmar' if is_mixed else 'set_a_omega'} + "
              f"set_b_calmar)")

        # ------------------------------------------------------------------
        #  Summary
        # ------------------------------------------------------------------
        a_m, b_m = result.best_a_metrics, result.best_b_metrics
        fs = result.full_series_metrics or {}
        stage1_label = "set C comb" if is_mixed else "stage-A"
        logger.info(f"\n=== nested training summary "
              f"(winner: {stage1_label} trial {result.winner_trial_no} of "
              f"{result.n_candidates} candidates × {result.grid_size} "
              f"grid points) ===")
        if is_mixed:
            logger.info(f"  set C (IS)       = calmar {a_m.get('calmar')} "
                  f"(ret {a_m.get('total_return')}, "
                  f"max_dd {a_m.get('max_dd_pct')}, "
                  f"trades {a_m.get('n_decisions')}, "
                  f"risk {a_m.get('risk_score')})")
        else:
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
            logger.warning(f"  WARNING: the winning {stage1_label} trial "
                  "produced NO trades on IS.")
        elif not is_mixed and not a_m.get("constraint_ok"):
            logger.warning("  WARNING: no stage-A trial satisfied the >55% "
                  "positive-months constraint — the best omega under the "
                  "penalty was used.")

        logger.info(f"\n=== combined best params "
              f"({'Set C comb ∪ Set B' if is_mixed else 'Set A ∪ Set B'}) ===")
        for k in sorted(result.best_params):
            logger.info(f"    {k} = {result.best_params[k]}")

        # ------------------------------------------------------------------
        #  Ship rule: the trained row is written only when it is not
        #  gate-rejected (--no-upsert still wins). A gate FAIL retains the
        #  untrained default — the 2026-11-19 study measured the default
        #  beating every trained selection on the untouched tail, so the
        #  gate is the structural guard making the trainer a VALIDATOR.
        # ------------------------------------------------------------------
        ship = gate_pass is not False  # None (gate off/unavailable) or True
        if args.no_upsert:
            logger.info("\n--no-upsert: best params NOT written to algo_configs.")
        elif not ship:
            logger.info("\n[train-gate] best params NOT written to "
                  "algo_configs — the winner did not beat the untrained "
                  "default on the untouched gate segment; Run Strategy "
                  "keeps the default config.")
        else:
            n = await upsert_best_params_for_codes(
                conn, args.sec_type, codes, strategy_name,
                result.best_params,
            )
            comb_note = (" (includes the trained comb block)" if is_mixed
                         else "")
            logger.info(f"\n[algo_configs] upserted tuned params for {n} code(s) "
                  f"({args.sec_type}, strategy '{strategy_name}', "
                  f"trained row [today, ∞], is_default=FALSE){comb_note} — "
                  f"next Run Strategy will use them.")

        # ------------------------------------------------------------------
        #  FINAL RESULT of a finished training = a real strategy run (the
        #  run-once CLI above). Skipped under --no-run, under --no-upsert,
        #  and on a train-gate FAIL (same rationale: nothing trained was
        #  persisted, so a record would misrepresent the study — the run
        #  would load the retained default row).
        # ------------------------------------------------------------------
        if args.no_run:
            logger.info("\n--no-run: post-training strategy run SKIPPED "
                  "(study only — run history gets no record).")
        elif args.no_upsert:
            logger.info("\n--no-upsert: post-training strategy run SKIPPED "
                  "(params were not persisted — the run would use stale "
                  "params).")
        elif not ship:
            logger.info("\n[train-gate] post-training strategy run SKIPPED "
                  "(the trained params were not persisted — the run would "
                  "load the retained default row and misrepresent the "
                  "study).")
        else:
            run_rc = await _run_trained_strategy(args.algo, args.sec_type, codes)
            if run_rc != 0:
                # Propagate honestly: the training's final result FAILED,
                # so the command fails even though the study completed.
                raise SystemExit(run_rc)


if __name__ == "__main__":
    OptmEngineStrategy().execute()
