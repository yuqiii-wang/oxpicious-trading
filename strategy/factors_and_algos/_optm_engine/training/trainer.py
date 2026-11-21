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
    evaluate_set_c,
    make_objective_set_a,
)
from strategy.factors_and_algos._optm_engine.loss import CALMAR_LOSS, LossEvaluation
from strategy.factors_and_algos._optm_engine.space import (
    SET_B_DEFAULTS,
    SET_B_SPACE,
    set_b_grid,
    set_c_comb_grid,
)

from strategy.factors_and_algos._optm_engine.training.data_definitions import (
    Candidate,
    CandidateGridResult,
    KellyResult,
    TrainingResult,
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




# ======================================================================
#  Data transfer objects
# ======================================================================




# ======================================================================
#  Preference helper (module-level for _optm_engine/__init__ lazy load)
# ======================================================================

def _preference(metrics: Dict[str, Any]) -> tuple:
    """Hard-DD-cap discard ordering for Calmar bundles (min is best).

    Mirrors the ``__main__._pick_best_b`` preference: valid (trades +
    no breach) < no-trade-no-breach < breach; ties broken by loss, then
    by the official risk score (skill Part 9 — the study measured the
    official ``risk_score`` separating combs 8-12x where Calmar alone
    barely separates them). ``risk_score`` is None/missing on non-valid
    points → +inf keeps them last within their rank.
    """
    if not metrics.get("no_trades") and not metrics.get("violation"):
        rank = 0
    elif not metrics.get("violation"):
        rank = 1
    else:
        rank = 2
    risk = metrics.get("risk_score")
    return (rank, metrics.get("loss", float("inf")),
            float(risk) if risk is not None else float("inf"))


# ======================================================================
#  PBO diagnostic (2026-11-19 validation study follow-up)
# ======================================================================

def _record_score(record: Dict[str, Any]) -> float:
    """One trial record's ranking score — the NEGATED loss.

    ``-loss`` is the search's own grading: the RegimeLoss template already
    pushes constraint breaches (100 + 20·deficit) and no-trade points
    (1000) above every valid trial, so negation alone reproduces the
    funnel's ordering closely enough for a coarse diagnostic.
    """
    try:
        return -float(record.get("loss", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _avg_ranks(values: List[float]) -> List[float]:
    """Competitive ranks (1 = best = highest value), ties → average rank."""
    n = len(values)
    order = sorted(range(n), key=lambda i: -values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def compute_pbo(trial_records: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """CSCV-lite backtest-overfitting diagnostic from the buffered records.

    Pairs each candidate's IS-stage score (its ``set_c_calmar`` cell in
    mixed mode / ``set_a_omega`` trial in binary mode) with its best
    OOS-stage score (the best ``set_b_calmar`` grid point by ``-loss``).
    The Set-B stage grades OOS only for the top-K carried candidates, so
    the pairing is small and this is a COARSE diagnostic — never a
    selection input:

      - ``pbo``  — fraction of candidates whose IS rank sits in the top
        half but whose OOS rank lands in the bottom half (classic CSCV
        discordance; 0 = IS ranking fully agrees with OOS).
      - ``rho``  — Spearman rank correlation between IS and OOS scores
        (1 = the funnel's IS ranking transferred to OOS perfectly).

    ``None`` when fewer than 3 pairs exist (log it as unavailable rather
    than fabricating a number — the study convention).
    """
    is_scores: Dict[int, float] = {}
    oos_best: Dict[int, float] = {}
    for r in trial_records:
        loss_type = r.get("loss_type")
        try:
            tn = int(r.get("trial_no", 0))
        except (TypeError, ValueError):
            continue
        score = _record_score(r)
        if loss_type in ("set_c_calmar", "set_a_omega"):
            is_scores[tn] = score
        elif loss_type == "set_b_calmar":
            prev = oos_best.get(tn)
            if prev is None or score > prev:
                oos_best[tn] = score
    pairs = [
        (is_scores[t], oos_best[t])
        for t in sorted(is_scores) if t in oos_best
    ]
    n = len(pairs)
    if n < 3:
        return None
    is_vals = [p[0] for p in pairs]
    oos_vals = [p[1] for p in pairs]
    is_ranks = _avg_ranks(is_vals)
    oos_ranks = _avg_ranks(oos_vals)
    top_half = n // 2          # strict top half (median-exclusive)
    bottom_half = n - (n + 1) // 2
    discordant = sum(
        1 for i in range(n)
        if is_ranks[i] <= top_half and oos_ranks[i] > n - bottom_half
    )
    d2 = sum((is_ranks[i] - oos_ranks[i]) ** 2 for i in range(n))
    rho = 1.0 - 6.0 * d2 / (n * (n * n - 1))
    return {
        "pbo": round(discordant / n, 3),
        "n_pairs": n,
        "spearman_rho": round(rho, 3),
    }


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
        """Run the full nested workflow: TPE → top-K → Kelly → grid → OOS.

        Binary mode: Set A TPE over conf_threshold (+ algo tunables).
        Mixed mode: the curated Set C comb grid (the portfolio's weight
        rules — adaptive weights × regime gate × vol-state × threshold)
        REPLACES Set A TPE; steps 2-5 are shared.
        """
        if self.ctx.is_mixed:
            self._log("--- step 1 (mixed): Set C comb grid (exhaustive, "
                      "Calmar loss on IS, max DD ≤ 25%; official-risk "
                      "tiebreak) ---")
            candidates = self.optimize_set_c()
        else:
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
    #  Step 1 (mixed) — Set C comb grid (exhaustive, Calmar on IS)
    # ------------------------------------------------------------------
    @staticmethod
    def _reject_evidence_present(backtester) -> bool:
        """True when the fetched per-algo frames carry reject-tier rows
        (any non-null ``rej_action`` — the analysis tier's
        negative-confidence evidence). Gates the Set C reject axis:
        the offsetting is trainable exactly when there is evidence to
        train on (skill Part 16; the tier's soft-launch state returns
        False and the grid stays at its reject-OFF shape)."""
        from _common.df_utils import safe_columns

        collector = getattr(backtester, "collector", None)
        frames = getattr(collector, "_mixed_dfs", None) or {}
        for frame in frames.values():
            if frame.empty or "rej_action" not in safe_columns(frame):
                continue
            if frame["rej_action"].notna().any():
                return True
        return False

    @staticmethod
    def _no_action_evidence_present(backtester) -> bool:
        """True when the fetched per-algo frames carry no-action
        overlay marks (any non-null ``na_action`` — the day-level
        "do NOT take this action here" marks on fired positive days).
        Gates the Set C no-action axis with the same availability
        semantics as the reject tier's (skill Part 17): the marked-day
        suppression is trainable exactly when there are marks to
        suppress on."""
        from _common.df_utils import safe_columns

        collector = getattr(backtester, "collector", None)
        frames = getattr(collector, "_mixed_dfs", None) or {}
        for frame in frames.values():
            if frame.empty or "na_action" not in safe_columns(frame):
                continue
            if frame["na_action"].notna().any():
                return True
        return False

    def optimize_set_c(self) -> List[Candidate]:
        """Exhaustive Set C comb grid on IS; Calmar loss + risk tiebreak.

        The curated cells (``space.set_c_comb_grid``) ARE the search —
        no sampler: the axes are discrete rules whose interactions are
        what the study measured (skill Parts 3-12). Cells are ranked by
        ``_preference`` (DD-cap discard ordering) and a gate-diverse
        top-K of structurally distinct cells carry on as candidates (a
        comb cell plays the role a Set-A TPE trial plays in binary
        mode).

        Reject availability: when the fetched frames carry reject rows,
        every cell gains a reject-ON twin (the family-local
        self-suppression joins the trained space); otherwise the grid
        is unchanged. No-action availability: the same twin rule for
        the day-level overlay marks (a no-action-ON winner must beat
        its OFF twin through the same funnel).

        Two anti-collapse rules guard the funnel (the 2026-11-17 run-18
        lesson: plain IS-Calmar ranking filled every top-K slot with
        near-identical gate-OFF cells, so the regime-gated thesis — the
        study's strongest measured comb form — never reached the OOS
        stage that is supposed to choose between theses):

        - STRUCTURAL DEDUPE — cells differing only in ``conf_threshold``
          collapse to one variant per (weight mode × gate × vol_state ×
          reject): thr neighbors measured metrically IDENTICAL (run 18:
          thr 2.5 ≡ 5.0, 100/100 same trades), so they must not eat
          top-K slots; exact ties prefer the thr closest to the 5.0
          default.
        - GATE-DIVERSE TOP-K — the best variant of EACH gate family
          (off / 120 / 255) takes a slot first (breach-ranked leaders
          are skipped), the rest fill by global rank. IS prunes WITHIN
          a thesis; the OOS Set-B stage chooses BETWEEN theses (the
          study's IS/OOS disagreement is the warning, not noise — skill
          Parts 3/5).
        """
        has_reject = self._reject_evidence_present(self.ctx.backtester)
        has_na = self._no_action_evidence_present(self.ctx.backtester)
        grid = set_c_comb_grid(has_reject_rows=has_reject,
                               has_no_action_rows=has_na)
        if has_reject:
            self._log("  reject-tier rows present in the fetched frames "
                      "— Set C includes reject-ON twin cells (family-local "
                      "self-suppression; graded by the same DD-cap funnel)")
        if has_na:
            self._log("  no-action overlay marks present in the fetched "
                      "frames — Set C includes no-action-ON twin cells "
                      "(family-local suppression of marked fired days; "
                      "graded by the same DD-cap funnel)")
        ranked: List[tuple] = []  # (preference, cell_idx, metrics)
        for idx, cell in enumerate(grid):
            metrics = evaluate_set_c(self.ctx, cell)
            pref = _preference(metrics)
            ranked.append((pref, idx, metrics))
            self.trial_records.append({
                "loss_type": "set_c_calmar",
                "trial_no": idx,
                "grid_idx": 0,
                "params": cell,
                "metrics": metrics,
                "loss": metrics.get("loss", 0.0),
                "constraint_ok": bool(metrics.get("constraint_ok", False)),
                "no_trades": bool(metrics.get("no_trades", False)),
            })
            comb = cell["comb"]
            mode = comb["weights"]["mode"]
            gate = ("off" if not comb["regime_gate"]["enabled"]
                    else f"w{comb['regime_gate']['window']}")
            if idx % 15 == 0 or idx == len(grid) - 1:
                self._log(
                    f"  cell {idx:3d}/{len(grid) - 1}  "
                    f"{mode:>10s} gate={gate:>5s} "
                    f"u3={'on' if comb['vol_state']['enabled'] else 'off'} "
                    f"rej={'on' if comb['reject']['enabled'] else 'off'} "
                    f"na={'on' if comb['no_action']['enabled'] else 'off'} "
                    f"thr={cell['conf_threshold']:4.1f}  "
                    f"calmar={metrics.get('calmar', 0.0):8.3f}  "
                    f"dd={metrics.get('max_dd_pct', 0.0):6.1%}  "
                    f"trades={metrics.get('n_decisions', 0)}"
                )
        ranked.sort(key=lambda r: r[0])

        def _cell_group(cell: dict) -> tuple:
            """Structural variant key:
            (weight mode, gate, vol_state, reject, no_action)."""
            comb = cell["comb"]
            gate = (0 if not comb["regime_gate"]["enabled"]
                    else comb["regime_gate"]["window"])
            return (comb["weights"]["mode"], gate,
                    comb["vol_state"]["enabled"],
                    comb["reject"]["enabled"],
                    comb["no_action"]["enabled"])

        def _cell_gate(cell: dict) -> int:
            return _cell_group(cell)[1]

        # Structural dedupe: best cell per variant group (ties → the
        # conf_threshold closest to the 5.0 production default).
        groups: Dict[tuple, List[tuple]] = {}
        for entry in ranked:
            groups.setdefault(_cell_group(grid[entry[1]]), []).append(entry)
        variants: List[tuple] = []
        for members in groups.values():
            variants.append(min(
                members,
                key=lambda r: (r[0],
                               abs(float(grid[r[1]]["conf_threshold"]) - 5.0),
                               r[1]),
            ))
        variants.sort(key=lambda r: (r[0], r[1]))

        # Gate-diverse top-K: one slot per gate family's best variant
        # (skip breach-ranked leaders), then fill by global variant rank.
        picked: List[tuple] = []
        chosen: set = set()
        leaders = []
        for gate in sorted({_cell_gate(grid[v[1]]) for v in variants}):
            bucket = [v for v in variants if _cell_gate(grid[v[1]]) == gate]
            leader = bucket[0]  # variants sorted → the bucket's best
            if leader[0][0] < 2:  # rank 2 = DD-cap breach — skip
                leaders.append(leader)
        leaders.sort(key=lambda r: (r[0], r[1]))
        for entry in leaders + variants:
            if len(picked) >= self.top_k:
                break
            if entry[1] in chosen:
                continue
            picked.append(entry)
            chosen.add(entry[1])
        candidates = [
            Candidate(trial_no=idx, params=grid[idx], metrics=metrics)
            for _, idx, metrics in picked
        ]
        gates_desc = "/".join(
            (str(_cell_gate(grid[e[1]])) if _cell_gate(grid[e[1]]) else "off")
            for e in picked
        )
        self._log(f"  deduped {len(grid)} cells → {len(variants)} structural "
                  f"variants (mode × gate × vol); top {len(candidates)} "
                  f"gate-diverse [{gates_desc}] — IS prunes within a thesis, "
                  f"the OOS Set-B stage chooses between theses "
                  f"(preference = DD-cap rank, loss, official risk)")
        return candidates

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
            agg = _aggregate(_run_backtests(
                self.ctx, self.ctx.dfs, params, segment="is",
            ))
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
        Breaching grid points are discarded via ``_preference``. Mixed
        mode grades the ``min_holding_period`` axis only — exec delays
        are no-ops on the collector path (``set_b_grid(mixed=True)``);
        binary algos whose replay never shifts execution dates
        (``TUNES_EXEC_DELAYS`` False) drop the delay axes, and algos
        that pin the holding gate (``TUNES_MIN_HOLDING`` False — the
        oracle full-fidelity replay) drop the ``min_holding_period``
        axis the same way.
        """
        grid = set_b_grid(
            mixed=self.ctx.is_mixed,
            drop_delays=not getattr(
                self.ctx.backtester, "TUNES_EXEC_DELAYS", True),
            drop_min_hp=not getattr(
                self.ctx.backtester, "TUNES_MIN_HOLDING", True),
        )
        results: List[CandidateGridResult] = []
        for cand in candidates:
            ctx_k = self._ctx_with_kelly_notional(cand)
            best: Optional[tuple] = None  # (preference, point, metrics)
            calmars: List[float] = []
            for idx, point in enumerate(grid, start=1):
                metrics = evaluate_set_b(ctx_k, point, cand.params)
                pref = _preference(metrics)
                if best is None or pref < best[0]:
                    best = (pref, point, metrics)
                calmars.append(float(metrics.get("calmar") or 0.0))
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
                    risk = m.get("risk_score")
                    self._log(
                        f"  cand#{cand.trial_no} grid {idx:3d}/{len(grid)}  "
                        f"best_calmar={m.get('calmar', 0.0):9.3f}  "
                        f"dd={m.get('max_dd_pct', 0.0):6.1%}  "
                        f"ret={m.get('total_return', 0.0):+7.1%}  "
                        f"risk={risk if risk is not None else 'n/a'}"
                    )
            # Stability read (2026-11-19 study): a wide Calmar spread over
            # the grid means the execution-axis choice dominates the
            # outcome — the selected point is then more selection noise
            # than signal.
            spread = max(calmars) - min(calmars) if calmars else 0.0
            self._log(
                f"  cand#{cand.trial_no} Set-B grid spread: "
                f"calmar max={max(calmars):.3f} min={min(calmars):.3f} "
                f"spread={spread:.3f} over {len(grid)} points"
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
        w_risk = winner.metrics.get("risk_score")
        self._log(
            f"  winner: trial#{winner.candidate.trial_no} "
            f"calmar={winner.metrics.get('calmar', 0.0):.3f} "
            f"dd={winner.metrics.get('max_dd_pct', 0.0):.1%} "
            f"ret={winner.metrics.get('total_return', 0.0):+.1%} "
            f"official_risk={w_risk if w_risk is not None else 'n/a'}"
        )
        # Boundary-solution warning (2026-11-19 study: every min_hp tuning
        # degraded the untouched holdout — hp=1/hp=13 winners, a champion
        # degrading 1.63 → 1.02 when hp-tuned). A grid-edge winner usually
        # means the axis is noise, not signal; the train gate (below) is
        # the structural guard, this line is the visibility.
        hp = winner.set_b_params.get("min_holding_period")
        hp_lo = int(SET_B_SPACE["min_holding_period"]["low"])
        hp_hi = int(SET_B_SPACE["min_holding_period"]["high"])
        if hp is not None and int(hp) in (hp_lo, hp_hi):
            self._log(
                f"  WARNING: winning min_holding_period={hp} sits on the "
                f"Set-B grid edge [{hp_lo}..{hp_hi}] — a boundary solution "
                f"usually means the hp axis is noise, not signal"
            )
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
            grid_size=len(set_b_grid(
                mixed=self.ctx.is_mixed,
                drop_delays=not getattr(
                    self.ctx.backtester, "TUNES_EXEC_DELAYS", True),
                drop_min_hp=not getattr(
                    self.ctx.backtester, "TUNES_MIN_HOLDING", True),
            )),
            full_series_metrics=full_series,
        )

    # ------------------------------------------------------------------
    #  Train gate — winner vs the shipped default on the untouched tail
    # ------------------------------------------------------------------
    def attach_pbo(self, result: TrainingResult) -> None:
        """Compute the CSCV-lite PBO diagnostic from the buffered trial
        records, set ``result.pbo``, and log it (via ``_log`` so the line
        persists into ``training_runs.log_text``)."""
        result.pbo = compute_pbo(self.trial_records)
        if result.pbo is not None:
            self._log(
                f"  PBO ≈ {result.pbo['pbo']:.2f} "
                f"({result.pbo['n_pairs']} candidate IS/OOS pairs, "
                f"rho={result.pbo['spearman_rho']:+.2f}) — coarse "
                f"diagnostic (the Set-B stage grades OOS only for the "
                f"top-K carried candidates)")
        else:
            self._log(
                "  PBO unavailable (<3 candidate IS/OOS pairs) — the "
                "IS/OOS pairing is too thin to be meaningful")

    def run_train_gate(
        self, result: TrainingResult, gate_ctx: OptmContext,
    ) -> Dict[str, Any]:
        """The 2026-11-19 train gate: VALIDATE the winner on an untouched
        segment before shipping it.

        The gate segment (the last ``--gate-frac`` of each code's rows,
        carved by ``__main__`` and never read by Steps 1-5) grades TWO
        configs through the SAME ``evaluate_set_b`` machinery:

          (a) the study winner (its Set-A/C cell + Set-B point as chosen),
          (b) the untrained default — mixed mode: the shipped
              ``DEFAULT_COMB`` champion (gate 120 + tstat@252 + U2/U3,
              conf_threshold 5.0, min_holding_period 5); binary mode: the
              algo's ``DEFAULT_PARAMS`` + the same trading defaults.

        Ship rule (applied by ``__main__``): upsert the trained
        ``algo_configs`` row ONLY when the winner's ``_preference`` tuple
        (DD-cap rank, loss, official risk) STRICTLY beats the default's —
        ties retain the default (the conservative side; the study measured
        the untrained default beating every trained selection on the
        untouched tail). The returned block is persisted in the run's
        report bundle and rendered by the UI.
        """
        from strategy.factors_and_algos._comb.config import DEFAULT_COMB

        winner_metrics = evaluate_set_b(
            gate_ctx, result.best_b_params, result.best_a_params,
        )
        if self.ctx.is_mixed:
            default_a: Dict[str, Any] = {
                "comb": DEFAULT_COMB.model_dump(),
                "conf_threshold": 5.0,
            }
        else:
            default_a = {
                **dict(getattr(self.ctx.backtester, "DEFAULT_PARAMS", {}) or {}),
            }
            # conf_threshold is part of the binary default only when the
            # algo consumes the dust filter (the oracle's full-fidelity
            # replay ignores it — the key stays out of the gate default).
            if getattr(self.ctx.backtester, "DUST_FILTER", True):
                default_a["conf_threshold"] = 5.0
        default_b: Dict[str, Any] = {
            k: v for k, v in SET_B_DEFAULTS.items()
            if k not in self.ctx.base_params
        }
        default_metrics = evaluate_set_b(gate_ctx, default_b, default_a)

        w_pref = _preference(winner_metrics)
        d_pref = _preference(default_metrics)
        passed = w_pref < d_pref

        def _summary(m: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "pref_rank": _preference(m)[0],
                "loss": m.get("loss"),
                "calmar": m.get("calmar"),
                "total_return": m.get("total_return"),
                "max_dd_pct": m.get("max_dd_pct"),
                "risk_score": m.get("risk_score"),
                "n_decisions": m.get("n_decisions"),
                "no_trades": bool(m.get("no_trades", False)),
                "violation": bool(m.get("violation", False)),
            }

        block: Dict[str, Any] = {
            "passed": passed,
            "winner": {"metrics": _summary(winner_metrics)},
            "default": {"metrics": _summary(default_metrics)},
        }

        self._log("--- train gate: winner vs the untrained default on the "
                  "UNTOUCHED gate segment ---")
        self._log(
            f"  winner  calmar={winner_metrics.get('calmar', 0.0):.3f}  "
            f"dd={winner_metrics.get('max_dd_pct', 0.0):.1%}  "
            f"ret={winner_metrics.get('total_return', 0.0):+.1%}  "
            f"risk={winner_metrics.get('risk_score') if winner_metrics.get('risk_score') is not None else 'n/a'}  "
            f"(pref rank {w_pref[0]})"
        )
        self._log(
            f"  default calmar={default_metrics.get('calmar', 0.0):.3f}  "
            f"dd={default_metrics.get('max_dd_pct', 0.0):.1%}  "
            f"ret={default_metrics.get('total_return', 0.0):+.1%}  "
            f"risk={default_metrics.get('risk_score') if default_metrics.get('risk_score') is not None else 'n/a'}  "
            f"(pref rank {d_pref[0]})"
        )
        if passed:
            self._log("  verdict: TRAINED CONFIG SHIPPED — the winner beats "
                      "the default on the gate segment (preference rank, "
                      "loss, official risk)")
        else:
            self._log(
                "  train-gate: default retained (trained "
                f"calmar={winner_metrics.get('calmar', 0.0):.3f}/"
                f"risk={winner_metrics.get('risk_score')} vs default "
                f"calmar={default_metrics.get('calmar', 0.0):.3f}/"
                f"risk={default_metrics.get('risk_score')} on gate segment)"
            )
        return block

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
        agg = _aggregate(_run_backtests(ctx_k, full, params, segment="full"))
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
    "compute_pbo",
    "KELLY_CAP",
    "KELLY_FRACTION",
    "MIN_TRADES",
]
