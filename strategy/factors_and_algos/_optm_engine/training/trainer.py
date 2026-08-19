"""The nested hybrid trainer — 5-step master plan class.

Scaffolds the "Nested Master Plan" for the two parameter sets,
reusing the EXISTING regime losses (``loss/``) and search spaces
(``space.py``) unchanged:

  Step 1  Optimize Set A (signals)     — Optuna TPE over the algo's
          TUNABLE_SPACE + conf_threshold, with Set B held at NEUTRAL
          defaults (entry lag 0 / exit lag 0 / strategy min-holding).
          Loss: existing ``OmegaLoss`` (Omega ratio of per-exit
          returns, >55% positive-months hard constraint). The notional
          is constant across trials (equal-notional by construction),
          so the Omega ratio is sizing-neutral.

  Step 2  Extract top candidates       — top-K DISTINCT param sets by
          Stage-A loss (deduped; TPE resamples near-identical points).

  Step 3  Apply analytical Kelly       — per candidate, re-run the IS
          backtest with neutral Set B, take the per-exit return
          sequence, f* = μ/σ² → cap 20% → ×0.25 fractional Kelly →
          static base amount (``buy_notional`` static for Set B).

  Step 4  Optimize Set B (execution)   — per candidate, an exhaustive
          vanilla grid over the existing Set B space
          (buy/sell_exec_delay, min_holding_period) with the
          Kelly-derived amount as the fixed position size. Loss:
          existing ``CalmarLoss`` on the OOS split (max DD ≤ 25% hard
          constraint — breaching grid points are discarded).

  Step 5  Final selection              — the single (Set A, Set B)
          combination with the best Calmar under the DD cap across ALL
          candidates, plus a full-series sanity check of the winner
          (report only — selection stays on the OOS split).

Engine extension points (NOT implemented — the trading engine has no
such knobs yet): stop_loss_pct / take_profit_pct grid axes and true
sizing-multiplier sensitivity would slot into Step 4's grid; today the
in-memory backtest is notional-invariant, so the Kelly amount threads
through ``buy_notional`` as the deployment static (reported, not
persisted — ``persist._STRIP_KEYS`` keeps per-study assumptions out of
``algo_configs``).

GPU: the nested loop is compute-intensive (trials × top-K × grid
backtests) — the trainer warns when cudf.pandas is not active; the
process-level hook is enabled by ``__main__`` before any import.
"""
from __future__ import annotations

import copy
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from strategy.factors_and_algos._optm_engine.objective import (
    OptmContext,
    _aggregate,
    _merge_params,
    _run_backtests,
    evaluate_set_b,
    make_objective_set_a,
)
from strategy.factors_and_algos._optm_engine.loss import CALMAR_LOSS, LossEvaluation
from strategy.factors_and_algos._optm_engine.space import (
    SET_B_DEFAULTS,
    set_b_grid,
)


# ======================================================================
#  Kelly components — consolidated into trainer.py (class-bound)
# ======================================================================

#: Hard cap on the Full Kelly fraction (max 20% portfolio risk).
KELLY_CAP: float = 0.20
#: Fractional-Kelly multiplier (quarter Kelly).
KELLY_FRACTION: float = 0.25
#: Minimum per-exit trades before Kelly is meaningful.
MIN_TRADES: int = 3


@dataclass(frozen=True)
class KellyResult:
    """Analytical Kelly outcome for one candidate's raw signal returns."""

    n_trades: int
    #: μ — mean per-exit return.
    mean: float
    #: σ² — sample variance (ddof=1) of per-exit returns.
    variance: float
    #: f* = μ/σ², floored at 0 (negative edge → never bet).
    full_kelly: float
    #: min(f*, cap) — the 20% portfolio-risk cap applied.
    capped_kelly: float
    #: capped × fraction — the quarter-Kelly betting fraction.
    fractional_kelly: float
    #: base_notional × fractional_kelly — the static base amount for
    #: Set B (fixed position size during the execution grid).
    notional: float


# ======================================================================
#  Data transfer objects
# ======================================================================

@dataclass
class Candidate:
    """One top-K Set A param set (step 2), enriched with Kelly (step 3)."""

    trial_no: int
    #: Set A params (conf_threshold + algo model params).
    params: Dict[str, Any]
    #: Stage A loss bundle (OmegaLoss evaluate output).
    metrics: Dict[str, Any]
    #: Analytical Kelly outcome from this candidate's raw signal returns.
    kelly: Optional[KellyResult] = None


@dataclass
class CandidateGridResult:
    """One candidate's best grid point from step 4."""

    candidate: Candidate
    #: Best Set B execution params for this candidate.
    set_b_params: Dict[str, Any]
    #: Stage B loss bundle (CalmarLoss evaluate output) at that point.
    metrics: Dict[str, Any]


@dataclass
class TrainingResult:
    """Final nested-training outcome (step 5)."""

    #: Combined best params (Set A ∪ Set B) — what gets upserted.
    best_params: Dict[str, Any]
    best_a_params: Dict[str, Any]
    best_b_params: Dict[str, Any]
    best_a_metrics: Dict[str, Any]
    best_b_metrics: Dict[str, Any]
    #: Kelly outcome of the winning candidate (sizing static, reported).
    kelly: Optional[KellyResult]
    winner_trial_no: int
    n_candidates: int
    grid_size: int
    #: Full-series sanity check of the winner (report only).
    full_series_metrics: Optional[Dict[str, Any]] = None


# ======================================================================
#  Preference helper (module-level for _optm_engine/__init__ lazy load)
# ======================================================================

def _preference(metrics: Dict[str, Any]) -> tuple:
    """Hard-DD-cap discard ordering for Calmar bundles (min is best).

    Mirrors the ``__main__._pick_best_b`` preference: valid (trades +
    no breach) < no-trade-no-breach < breach; ties broken by loss.
    """
    if not metrics.get("no_trades") and not metrics.get("violation"):
        rank = 0
    elif not metrics.get("violation"):
        rank = 1
    else:
        rank = 2
    return (rank, metrics.get("loss", float("inf")))


# ======================================================================
#  Main trainer class — 5-step nested hybrid workflow
# ======================================================================

class NestedTrainer:
    """Orchestrates the 5-step nested hybrid workflow on one OptmContext.

    Algo-agnostic: works on any ``AlgoBase`` subclass with a
    ``TUNABLE_SPACE`` (the context's algo drives signal generation).

    Kelly constants are class-level defaults; they can be overridden
    per-instance via the ``kelly_cap`` / ``kelly_fraction`` constructor
    arguments.
    """

    # -- Kelly class-level defaults (mirror the former kelly.py module) --
    KELLY_CAP: float = KELLY_CAP
    KELLY_FRACTION: float = KELLY_FRACTION
    MIN_TRADES: int = MIN_TRADES

    def __init__(
        self,
        ctx: OptmContext,
        *,
        seed: Optional[int] = None,
        top_k: int = 5,
        kelly_cap: float = KELLY_CAP,
        kelly_fraction: float = KELLY_FRACTION,
        log: Optional[Callable[[str], None]] = None,
    ):
        self.ctx = ctx
        self.seed = seed
        self.top_k = max(1, int(top_k))
        self.kelly_cap = kelly_cap
        self.kelly_fraction = kelly_fraction
        #: Buffered per-point records for strategy.training_trials —
        #: tagged loss_type 'set_a_omega' / 'set_b_calmar'. Persisted by
        #: ``training_store.insert_training_trials`` AFTER the (sync)
        #: study loop ends; the DB writes are async.
        self.trial_records: List[Dict[str, Any]] = []
        #: Every message passed through ``self._log`` — persisted as
        #: training_runs.log_text so the UI can replay the run log.
        self.log_lines: List[str] = []
        user_log = log or (lambda msg: print(msg, flush=True))

        def _log(msg: str) -> None:
            self.log_lines.append(msg)
            user_log(msg)

        self._log = _log
        self._warn_if_cpu()

    # ------------------------------------------------------------------
    #  Analytical Kelly — formerly in kelly.py, now a class static
    # ------------------------------------------------------------------
    @staticmethod
    def analytical_kelly(
        returns: Sequence[float],
        base_notional: float,
        *,
        cap: float = KELLY_CAP,
        fraction: float = KELLY_FRACTION,
        min_trades: int = MIN_TRADES,
    ) -> KellyResult:
        """Compute the (capped, fractional) analytical Kelly for a PnL series.

        ``returns`` are the raw signal's per-exit return rates (equal-notional,
        un-levered); ``base_notional`` is the portfolio notional scale the
        fraction is applied to.

        Closed-form formula (not an ML model): the theoretically optimal
        fraction of capital to bet on a sequence of trades to maximize the
        geometric growth rate of the portfolio. For a series of continuous,
        approximately normally distributed per-trade returns:

            Full Kelly    f* = μ / σ²

            f_capped     = min(f*, KELLY_CAP)         # cap at 20% portfolio risk
            f_fractional = f_capped × KELLY_FRACTION  # quarter-Kelly (× 0.25)
        """
        rs: List[float] = [float(r) for r in returns]
        n = len(rs)
        if n < min_trades:
            return KellyResult(n, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

        mean = sum(rs) / n
        variance = sum((r - mean) ** 2 for r in rs) / (n - 1)

        if variance <= 0.0:
            # Degenerate: every trade returned the same amount.
            full = cap if mean > 0.0 else 0.0
        else:
            full = max(mean / variance, 0.0)

        capped = min(full, cap)
        frac = capped * fraction
        return KellyResult(
            n_trades=n,
            mean=mean,
            variance=variance,
            full_kelly=full,
            capped_kelly=capped,
            fractional_kelly=frac,
            notional=base_notional * frac,
        )

    # ------------------------------------------------------------------
    #  Orchestration
    # ------------------------------------------------------------------
    def run(self, trials: int) -> TrainingResult:
        """Run the full nested workflow: TPE → top-K → Kelly → grid → OOS."""
        self._log("--- step 1: Set A signal params (TPE, Omega loss, "
                  ">55% positive months; Set B neutral) ---")
        study = self.optimize_set_a(trials)
        self._log("--- step 2: top-K distinct candidates ---")
        candidates = self.extract_top_candidates(study, self.top_k)
        self._log("--- step 3: analytical Kelly sizing (f*=μ/σ², cap 20%, "
                  "×0.25) ---")
        candidates = self.apply_kelly(candidates)
        self._log("--- step 4: Set B execution params (vanilla grid, "
                  "Calmar loss on OOS, max DD ≤ 25%) ---")
        grid_results = self.optimize_set_b(candidates)
        self._log("--- step 5: final selection ---")
        return self.final_selection(grid_results)

    # ------------------------------------------------------------------
    #  Step 1 — Optimize Set A (TPE, existing OmegaLoss)
    # ------------------------------------------------------------------
    def optimize_set_a(self, trials: int):
        """TPE over Set A with Set B at neutral defaults; Omega loss.

        ``evaluate_set_a`` already pins the Set B execution keys to the
        neutral/strategy defaults, so signal quality is never graded
        through an untuned execution path.
        """
        import optuna

        # Scale TPE's random-startup budget to the trial count: the
        # default n_startup_trials=10 means a small study (e.g. --trials 6)
        # NEVER leaves random sampling, so the loss can't trend down.
        # With e.g. 6 trials → 3 random + 3 model-guided.
        n_startup = min(10, max(3, int(trials) // 3))
        study = optuna.create_study(
            direction="minimize",
            sampler=optuna.samplers.TPESampler(
                seed=self.seed, n_startup_trials=n_startup,
            ),
        )

        def _on_trial(study_, trial):
            m = trial.user_attrs.get("metrics", {})
            self._log(
                f"  trial {trial.number:3d}  loss={trial.value:9.4f}  "
                f"omega={m.get('omega', 0.0):8.3f}  "
                f"pos_months={m.get('positive_month_fraction', 0.0):6.1%}  "
                f"trades={m.get('n_trades', 0)}"
            )
            self.trial_records.append({
                "loss_type": "set_a_omega",
                "trial_no": trial.number,
                "grid_idx": 0,
                "params": trial.user_attrs.get("params_used", trial.params),
                "metrics": m,
                "loss": trial.value,
                "constraint_ok": bool(m.get("constraint_ok", False)),
                "no_trades": bool(m.get("no_trades", False)),
            })

        study.optimize(
            make_objective_set_a(self.ctx), n_trials=trials,
            show_progress_bar=False, callbacks=[_on_trial],
        )
        return study

    # ------------------------------------------------------------------
    #  Step 2 — Extract top-K distinct candidates
    # ------------------------------------------------------------------
    def extract_top_candidates(self, study, k: int) -> List[Candidate]:
        """Top-K DISTINCT Set A param sets by Stage-A loss."""
        ranked = sorted(
            (t for t in study.trials if t.value is not None),
            key=lambda t: t.value,
        )
        seen = set()
        out: List[Candidate] = []
        for t in ranked:
            params = t.user_attrs.get("params_used", t.params)
            key = tuple(sorted((str(kk), str(v)) for kk, v in params.items()))
            if key in seen:
                continue
            seen.add(key)
            out.append(Candidate(
                trial_no=t.number,
                params=params,
                metrics=t.user_attrs.get("metrics", {}),
            ))
            if len(out) >= k:
                break
        self._log(f"  top {len(out)} distinct candidate(s) extracted")
        return out

    # ------------------------------------------------------------------
    #  Step 3 — Analytical Kelly per candidate
    # ------------------------------------------------------------------
    def apply_kelly(self, candidates: List[Candidate]) -> List[Candidate]:
        """f* = μ/σ² from each candidate's raw signal returns (IS, neutral B).

        Re-runs the IS backtest with neutral Set B (cheap — K backtests)
        to recover the per-exit return sequence the Stage-A bundle does
        not carry, then caps at 20% and scales by the 0.25 fractional
        multiplier into a static base amount.
        """
        base_notional = float(self.ctx.statics.get("buy_notional", 0.0) or 0.0)
        neutral_b = {
            key: val for key, val in SET_B_DEFAULTS.items()
            if key not in self.ctx.base_params
        }
        for cand in candidates:
            params = _merge_params(self.ctx, {**cand.params, **neutral_b})
            agg = _aggregate(_run_backtests(self.ctx, self.ctx.dfs, params))
            cand.kelly = self.analytical_kelly(
                agg["returns"], base_notional,
                cap=self.kelly_cap, fraction=self.kelly_fraction,
            )
            k = cand.kelly
            self._log(
                f"  cand#{cand.trial_no}: n={k.n_trades}  "
                f"μ={k.mean:+.4f}  σ²={k.variance:.6f}  "
                f"f*={k.full_kelly:.3f}  capped={k.capped_kelly:.3f}  "
                f"frac={k.fractional_kelly:.3f}  notional={k.notional:,.0f}"
            )
        return candidates

    # ------------------------------------------------------------------
    #  Step 4 — Vanilla grid per candidate (existing CalmarLoss)
    # ------------------------------------------------------------------
    def optimize_set_b(
        self, candidates: List[Candidate],
    ) -> List[CandidateGridResult]:
        """Exhaustive Set B grid per candidate on OOS; Calmar loss.

        The Kelly-derived amount is threaded as the ``buy_notional``
        static (fixed position size) for the candidate's whole grid.
        Breaching grid points are discarded via ``_preference``.
        """
        grid = set_b_grid()
        results: List[CandidateGridResult] = []
        for cand in candidates:
            ctx_k = self._ctx_with_kelly_notional(cand)
            best: Optional[tuple] = None  # (preference, point, metrics)
            for idx, point in enumerate(grid, start=1):
                metrics = evaluate_set_b(ctx_k, point, cand.params)
                pref = _preference(metrics)
                if best is None or pref < best[0]:
                    best = (pref, point, metrics)
                self.trial_records.append({
                    "loss_type": "set_b_calmar",
                    # trial_no = the candidate's Stage A trial number —
                    # joins the grid point back to its signal params.
                    "trial_no": cand.trial_no,
                    "grid_idx": idx,
                    "params": point,
                    "metrics": metrics,
                    "loss": metrics.get("loss", 0.0),
                    "constraint_ok": bool(metrics.get("constraint_ok", False)),
                    "no_trades": bool(metrics.get("no_trades", False)),
                })
                if idx % 96 == 0 or idx == len(grid):
                    m = best[2]
                    self._log(
                        f"  cand#{cand.trial_no} grid {idx:3d}/{len(grid)}  "
                        f"best_calmar={m.get('calmar', 0.0):9.3f}  "
                        f"dd={m.get('max_dd_pct', 0.0):6.1%}  "
                        f"ret={m.get('total_return', 0.0):+7.1%}"
                    )
            results.append(CandidateGridResult(
                candidate=cand,
                set_b_params=best[1],
                metrics=best[2],
            ))
        return results

    # ------------------------------------------------------------------
    #  Step 5 — Final selection (best Calmar under the DD cap)
    # ------------------------------------------------------------------
    def final_selection(
        self, results: List[CandidateGridResult],
    ) -> TrainingResult:
        """Pick the single best (Set A, Set B) combo across all candidates."""
        if not results:
            raise ValueError("no candidate grid results to select from")
        winner = min(results, key=lambda r: _preference(r.metrics))
        if _preference(winner.metrics)[0] == 2:
            self._log("  WARNING: ALL grid points breach the 25% drawdown "
                      "cap — using the least-bad combination.")
        elif _preference(winner.metrics)[0] == 1:
            self._log("  WARNING: no trading grid point — using the best "
                      "no-trade combination.")

        best_params = {**winner.candidate.params, **winner.set_b_params}
        full_series = self._verify_full_series(winner)
        return TrainingResult(
            best_params=best_params,
            best_a_params=winner.candidate.params,
            best_b_params=winner.set_b_params,
            best_a_metrics=winner.candidate.metrics,
            best_b_metrics=winner.metrics,
            kelly=winner.candidate.kelly,
            winner_trial_no=winner.candidate.trial_no,
            n_candidates=len(results),
            grid_size=len(set_b_grid()),
            full_series_metrics=full_series,
        )

    # ------------------------------------------------------------------
    #  Internals
    # ------------------------------------------------------------------
    def _ctx_with_kelly_notional(self, cand: Candidate) -> OptmContext:
        """Shallow ctx copy with the Kelly amount as the sizing static."""
        ctx = copy.copy(self.ctx)
        statics = dict(self.ctx.statics)
        if cand.kelly is not None:
            statics["buy_notional"] = cand.kelly.notional
        ctx.statics = statics
        return ctx

    def _verify_full_series(
        self, winner: CandidateGridResult,
    ) -> Dict[str, Any]:
        """Sanity-check the winner on the FULL series (report only).

        Selection stays on the OOS split; this re-run over IS+OOS
        (deduped when ``--oos-frac 0`` makes them the same frame) only
        reports what the combined config would have done overall.
        """
        import pandas as pd

        full: Dict[str, Any] = {}
        for code in self.ctx.codes:
            parts = [
                d for d in (self.ctx.dfs.get(code), self.ctx.oos_dfs.get(code))
                if d is not None and not d.empty
            ]
            if not parts:
                continue
            if len(parts) > 1 and parts[0] is not parts[1]:
                full[code] = pd.concat(parts)
            else:
                full[code] = parts[0]

        ctx_k = self._ctx_with_kelly_notional(winner.candidate)
        params = _merge_params(
            ctx_k, {**winner.candidate.params, **winner.set_b_params},
        )
        agg = _aggregate(_run_backtests(ctx_k, full, params))
        return CALMAR_LOSS.evaluate(LossEvaluation.from_aggregate(agg))

    def _warn_if_cpu(self) -> None:
        """The nested loop is compute-heavy — warn when GPU pandas is off."""
        if "cudf.pandas" not in sys.modules:
            self._log(
                "  WARNING: cudf.pandas not active — the nested loop "
                "(trials × top-K × grid backtests) is compute-intensive; "
                "run with --gpu auto/on on a CUDA host."
            )


# Module-level aliases for backward-compatible public API.
# External code may still do:
#   from ...training import analytical_kelly, KELLY_CAP, ...
analytical_kelly = NestedTrainer.analytical_kelly


__all__ = [
    "NestedTrainer",
    "Candidate",
    "CandidateGridResult",
    "TrainingResult",
    "KellyResult",
    "analytical_kelly",
    "KELLY_CAP",
    "KELLY_FRACTION",
    "MIN_TRADES",
]
