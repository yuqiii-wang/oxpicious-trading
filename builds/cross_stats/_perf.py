"""Perf-blocker logging helpers for builds.cross_stats.

The refactor mandate: "log performance blocker while doing the refactor".
Every phase prints its wall time; a phase exceeding its budget emits an
explicit ``[PERF-BLOCKER]`` line so regressions surface in the run log
instead of hiding inside aggregate wall time. Known structural blockers
are also declared here (with the accepted rationale) so the next reader
does not re-discover them.
"""
from __future__ import annotations

import time
from contextlib import contextmanager

# Wall-time budgets per phase label (seconds). Exceeding the budget logs
# a [PERF-BLOCKER] line (warning only — never aborts).
_BUDGETS_S: dict[str, float] = {
    "fetch": 120.0,
    "pair-grain": 600.0,
    "industry-grain": 600.0,
    "corr": 300.0,
    "write": 300.0,
    "code-summary": 120.0,
}


@contextmanager
def timed(label: str, budget_s: float | None = None):
    """Time a phase; print wall time and flag [PERF-BLOCKER] on overrun."""
    t0 = time.time()
    try:
        yield
    finally:
        elapsed = time.time() - t0
        print(f"    [perf] {label}: {elapsed:.1f}s", flush=True)
        budget = budget_s if budget_s is not None else _BUDGETS_S.get(label)
        if budget is not None and elapsed > budget:
            print(
                f"    [PERF-BLOCKER] {label} took {elapsed:.1f}s "
                f"(budget {budget:.0f}s) — investigate before the next run",
                flush=True,
            )


# ---------------------------------------------------------------------------
#  Declared structural blockers (accepted, with rationale — do NOT re-derive)
# ---------------------------------------------------------------------------
DECLARED_BLOCKERS: tuple[tuple[str, str], ...] = (
    (
        "pair-grain per-subject loop",
        "compute/_orchestrator._insert_rows iterates subjects; each "
        "iteration is a small (~4K-row) merge+MA5 chain. Batching into one "
        "wide op would build the full subjects x benchmarks x dates cross "
        "product (~56M rows) to prune ~8x afterwards — transfer churn "
        "exceeds the loop cost. Small-N per-subject work is the sanctioned "
        "CPU/host route (gpu-df-compute playbook §8.5); the GPU wins live "
        "in grouped_rolling_agg (MA5) and the corr tensor kernel.",
    ),
    (
        "fetch_shared_weights O(N^2) zero-fill",
        "fetch.py zero-fills (0,0) for every compositioned code pair with "
        "no overlap so disjoint pairs stay explicit — a python dict loop "
        "over all_codes^2. One-time per run (~10^6 dict writes, ~1s); "
        "vectorizing needs a cross-join frame that costs more than the "
        "loop.",
    ),
    (
        "industry grain liquidity EXISTS probe",
        "the shared_trading CTE probes stock_basic_stats for a non-NULL "
        "close per (stock, date) row to keep exact parity with the former "
        "attributions SUM semantics. A semijoin is plan-cheap; removing "
        "the probe would change parity.",
    ),
)


def print_declared_blockers() -> None:
    """Emit the declared-blocker register (visible in every run log)."""
    print("    [perf] declared structural blockers (accepted):", flush=True)
    for name, why in DECLARED_BLOCKERS:
        print(f"      - {name}: {why}", flush=True)
