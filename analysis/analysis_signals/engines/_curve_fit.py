"""The family-agnostic depth-response CURVE fit core
(analysis.analysis_signals.engines._curve_fit).

The host fit primitives every signal family's curve tier shares — moved
VERBATIM from mov_rsi/curve/_curves.py (the first curve family) when the
boll_std family joined (2026-10-07): the weighted Spearman +
cluster-adjusted t, the weighted isotonic (PAVA) fit, the pooled shape
resolver, the fit_curve JSON payload sampler and the empirical band
payload builder. Pure host numpy over materialized (s, y, w) points —
the documented per-group regression boundary (the per-cell accumulators
live in each family's curve module).

``deep_arm_iso`` (2026-10-11) adds the LOOSELY MONOTONE fit for
monotone-by-side populations whose measured response is not
rank-monotone (boll_std's BUY side: a shallow plateau with the rise in
the deep arm) — the strict increasing PAVA shape gated by a
pre-registered deep-arm contrast instead of the rank correlation.

The only generalizations over the moved originals: ``fit_payload`` takes
the s grid and the significance bar as arguments (each family owns its
grid — mov_rsi's (0.66, 2.62) covers its 25% band; boll_std's
(0.66, 3.10) extends past its 3σ bar), defaulting to the mov_rsi values;
``prefer_own_fit`` takes the pool fitter (default ``pooled_iso``).
"""
from __future__ import annotations

from collections.abc import Callable

import numpy as np
from scipy.stats import rankdata

from analysis.analysis_signals.config import (
    CURVE_FIT_S_GRID,
    CURVE_SLOPE_MIN_T,
)


def average_ranks(x: np.ndarray) -> np.ndarray:
    """Average ranks (ties share the mean rank) — rankdata's 'average'
    on host numpy."""
    return rankdata(x, method="average")


def weighted_spearman(
    s: np.ndarray, y: np.ndarray, w: np.ndarray,
) -> tuple[float, float]:
    """The weighted Spearman rho(s, y) — weighted Pearson of the
    average ranks — and its cluster-adjusted t:
    t = rho * sqrt((n_ep - 2) / (1 - rho^2)) with n_ep = sum(w) = the
    pool's EPISODE count (each episode's days carry total weight 1;
    within-episode days are correlated through overlapping forward
    windows, so the effective sample is episodes). (0, 0) when
    degenerate."""
    sw = float(w.sum())
    if len(s) < 3 or sw < 3.0:
        return 0.0, 0.0
    rs = average_ranks(s)
    ry = average_ranks(y)
    mu_s = float((w * rs).sum() / sw)
    mu_y = float((w * ry).sum() / sw)
    cov = float((w * (rs - mu_s) * (ry - mu_y)).sum())
    var = np.sqrt(
        max(float((w * (rs - mu_s) ** 2).sum()), 1e-12)
        * max(float((w * (ry - mu_y) ** 2).sum()), 1e-12),
    )
    rho = cov / var if var > 0 else 0.0
    n_ep = sw
    denom = 1.0 - rho * rho
    t = (
        rho * np.sqrt(max((n_ep - 2.0) / denom, 0.0))
        if n_ep > 2.0 and denom > 1e-12 else 0.0
    )
    return float(rho), float(t)


def pava(
    s: np.ndarray, y: np.ndarray, w: np.ndarray, increasing: bool,
) -> tuple[np.ndarray, np.ndarray]:
    """The weighted isotonic (PAVA) fit of y on s — nondecreasing when
    ``increasing``, nonincreasing otherwise — returned as (sorted s,
    fitted values): a step function over the pool's own s order.
    Empty-spread pools return the constant weighted mean."""
    order = np.argsort(s, kind="stable")
    s_s = s[order]
    y_s = y[order].copy()
    w_s = w[order]
    if not increasing:
        y_s = -y_s
    # block merge while any descent: (weight*y sum, weight sum, count)
    blocks: list[list[float]] = []
    for yi, wi in zip(y_s.tolist(), w_s.tolist()):
        blocks.append([yi * wi, wi, 1])
        while (
            len(blocks) >= 2
            and blocks[-2][0] / blocks[-2][1]
            > blocks[-1][0] / blocks[-1][1] + 1e-15
        ):
            b = blocks.pop()
            a = blocks.pop()
            blocks.append([a[0] + b[0], a[1] + b[1], a[2] + b[2]])
    fit = np.empty(len(y_s), dtype="float64")
    pos = 0
    for sy, sw_, cnt in blocks:
        fit[pos:pos + cnt] = sy / sw_
        pos += cnt
    if not increasing:
        fit = -fit
    return s_s, fit


def pooled_iso(
    points: list[tuple[float, float, float]], side: int,
) -> tuple[tuple[float, float], tuple[np.ndarray, np.ndarray] | None]:
    """The pool's (weighted Spearman, cluster t) + its episode-weighted
    isotonic shape as (sorted s, fitted y) — the shape's DIRECTION
    follows the rho sign (a negative pool draws a nonincreasing shape —
    the study's per-code reversals), the buy side keeping the retired
    linear fit's >= 0 guard: a NEGATIVE buy rho means the depth
    response runs against the side, and the shape is None (the caller
    stores the flat payload — the same outcome the old `slope < 0 → 0`
    guard produced; a forced-increasing PAVA on a decreasing cloud
    would dress the conflict up as structure). ((0, 0), None) also when
    the pool is too thin / degenerate."""
    from analysis.analysis_signals.config import SELL_SIDE_CODES

    if len(points) < 3:
        return (0.0, 0.0), None
    s = np.array([p[0] for p in points], dtype="float64")
    y = np.array([p[1] for p in points], dtype="float64")
    w = np.array([p[2] for p in points], dtype="float64")
    if float(np.ptp(s)) <= 1e-12:
        return (0.0, 0.0), None
    rho, t = weighted_spearman(s, y, w)
    if side not in SELL_SIDE_CODES and rho < 0.0:
        return (rho, t), None
    increasing = True if side not in SELL_SIDE_CODES else rho >= 0.0
    return (rho, t), pava(s, y, w, increasing)


def deep_arm_iso(
    points: list[tuple[float, float, float]],
    side: int,
    *,
    edge: float,
    min_t: float = CURVE_SLOPE_MIN_T,
    arm_min_days: int = 3,
    arm_min_weight: float = 1.0,
) -> tuple[tuple[float, float], tuple[np.ndarray, np.ndarray] | None]:
    """The LOOSELY MONOTONE fit for a monotone-by-side population whose
    measured response is not rank-monotone (boll_std's BUY side,
    2026-10-11): the depth response is a shallow plateau with the
    favorable rise concentrated in the DEEP arm, so the global rank
    correlation that gates ``pooled_iso`` reads ~0-to-negative on it
    (the mid-band mass dominates the ranks) and the strict gate stores
    flat everywhere.

    THE SHAPE is the strict increasing PAVA (``pava``) — plateau +
    deep staircase, nondecreasing by construction (the "loosely
    monotone" summary: the fit never draws a decline; a pool whose
    response genuinely falls with depth collapses to its weighted
    mean).

    THE GATE replaces the rank correlation with a PRE-REGISTERED
    two-arm contrast — no data selection: the deep arm is the fixed
    depth edge ``edge`` (boll_std passes its 2.5 sigma emitted bar),
    the rest of the pool is the other arm:

        t = (m_deep - m_rest) / sqrt(v_deep/d_deep + v_rest/d_rest)

    with m/v the arm's weighted mean / day-level variance and d the
    arm's DAY count — the day divisor, not the episode divisor: the
    two arms of one pool share the pool's regime/time structure, which
    largely cancels in the difference (permutation-null calibrated:
    ~5% false-draw rate at t >= 2 over the full index universe's buy
    class pools, vs 0% for the episode divisor at the cost of ~no
    power — the episode divisor over-penalizes by the within-episode
    overlap factor the difference cancels). The stored ``t`` (the
    gate's input, denormalized into fit_t) is this contrast; ``rho``
    stays the pool's weighted Spearman (descriptive — the UI and the
    flat payload gate on t only, via ``fit_payload``).

    Floors: the deep arm must carry >= ``arm_min_days`` days and >=
    ``arm_min_weight`` episode weight (the rest >= 3 — the
    ``weighted_spearman`` degenerate guard), else the gate fails.

    Returns ((rho, t), iso | None) — the ``pooled_iso`` contract: iso
    is None (the caller stores the flat payload) when the pool is
    degenerate, the floors fail, or t < min_t. The SELL side stays on
    ``pooled_iso`` (its measured response IS rank-monotone — the
    nonincreasing momentum shape — and its fits clear the rank gate)."""
    if len(points) < 3:
        return (0.0, 0.0), None
    s = np.array([p[0] for p in points], dtype="float64")
    y = np.array([p[1] for p in points], dtype="float64")
    w = np.array([p[2] for p in points], dtype="float64")
    if float(np.ptp(s)) <= 1e-12:
        return (0.0, 0.0), None
    rho, _t_rank = weighted_spearman(s, y, w)
    iso = pava(s, y, w, True)
    deep = s >= edge
    rest = ~deep
    d_deep = int(deep.sum())
    d_rest = int(rest.sum())
    n_deep = float(w[deep].sum())
    n_rest = float(w[rest].sum())
    if (
        d_deep < arm_min_days
        or n_deep < arm_min_weight
        or n_rest < 3.0
        or d_rest < arm_min_days
    ):
        return (float(rho), 0.0), None
    m_deep = float((w[deep] * y[deep]).sum() / n_deep)
    m_rest = float((w[rest] * y[rest]).sum() / n_rest)
    v_deep = float((w[deep] * (y[deep] - m_deep) ** 2).sum() / n_deep)
    v_rest = float((w[rest] * (y[rest] - m_rest) ** 2).sum() / n_rest)
    se = max(
        float(np.sqrt(v_deep / d_deep + v_rest / d_rest)), 1e-9,
    )
    t_arm = (m_deep - m_rest) / se
    if t_arm < min_t:
        return (float(rho), float(t_arm)), None
    return (float(rho), float(t_arm)), iso


def prefer_own_fit(
    own: list[tuple[float, float, float]],
    pooled: list[tuple[float, float, float]],
    side: int,
    *,
    min_own_episodes: float | None = None,
    min_t: float | None = None,
    fit_fn: "Callable[..., tuple[tuple[float, float], "
            "tuple[np.ndarray, np.ndarray] | None]]" = pooled_iso,
) -> tuple[tuple[float, float], tuple[np.ndarray, np.ndarray] | None]:
    """The TWO-TIER fit resolution (2026-10-07): the CODE's OWN pool
    when it carries enough episodes to check significance on its own,
    else the pooled (industry-class) pool.

    The class pooling exists ONLY to buy statistical significance for
    thin codes (the 2026-10 study found per-cell shapes too thin), so
    it stays OPTIONAL: a code whose own (windows x days) pool holds >=
    ``min_own_episodes`` episodes AND clears ``min_t`` fits its OWN
    shape and never borrows the class gradient — including the
    significant-NEGATIVE case (the buy-side guard inside pooled_iso
    stores the code's own flat outcome; borrowing the class shape
    would overwrite the code's own contrary evidence). Below the own
    floor the (industry_class, side, regime, pass) pool is the
    fallback — exactly the pre-2026-10-07 single-tier behavior, gated
    the same way.

    ``fit_fn`` swaps the pool fitter (default ``pooled_iso`` — the
    Spearman-gated isotonic); a family whose gate is not the global
    rank correlation passes its own resolver with the SAME
    ``((rho, t), iso | None)`` contract (boll_std's buy side fits
    ``deep_arm_iso`` — the pre-registered deep-arm gate). The stored
    fit_rho/fit_t always describe the shape actually stored."""
    from analysis.analysis_signals.config import (
        CURVE_OWN_POOL_MIN_EPISODES,
        CURVE_SLOPE_MIN_T,
    )

    floor_ep = (
        min_own_episodes if min_own_episodes is not None
        else CURVE_OWN_POOL_MIN_EPISODES
    )
    bar_t = min_t if min_t is not None else CURVE_SLOPE_MIN_T
    if own:
        own_n_ep = float(sum(w for _, _, w in own))
        stats, iso = fit_fn(own, side)
        if own_n_ep >= floor_ep and abs(stats[1]) >= bar_t:
            return stats, iso
    return fit_fn(pooled, side)


def fit_payload(
    iso: tuple[np.ndarray, np.ndarray] | None,
    s_center: float,
    t: float,
    *,
    grid: np.ndarray | None = None,
    min_t: float = CURVE_SLOPE_MIN_T,
) -> dict[str, float]:
    """The fit_curve JSON payload: the pooled isotonic shape sampled on
    the family's s grid (default mov_rsi's CURVE_FIT_S_GRID), offsets
    RELATIVE to the shape at s_center (curve(s) = mu_center +
    interp(payload, s) passes exactly through mu_center at s_center).
    Grid keys OUTSIDE the pool's measured range are omitted (no shape
    beyond the data); the GATED payload (|t| < min_t or a degenerate
    pool) is the flat two-point zero line — the cell draws at its
    mu_center."""
    if iso is None or abs(t) < min_t:
        return {"0.00": 0.0, "3.00": 0.0}
    g_grid = grid if grid is not None else np.round(np.arange(
        CURVE_FIT_S_GRID[0],
        CURVE_FIT_S_GRID[1] + CURVE_FIT_S_GRID[2] / 2,
        CURVE_FIT_S_GRID[2],
    ), 6)
    s_s, fit = iso
    center_y = float(np.interp(s_center, s_s, fit))
    inside = (s_s[0] <= g_grid) & (g_grid <= s_s[-1])
    return {
        f"{g:.2f}": round(float(np.interp(g, s_s, fit)) - center_y, 8)
        for g in g_grid[inside]
    }


def band_dict(
    acc: dict[int | float, list[float]],
) -> dict[str, float]:
    """The cell's EMPIRICAL band payload from its accumulator
    {key: [w*y_sum, w_sum]} — {key: round(weighted mean y, 8)} for the
    populated bands (the key's vocabulary is the family's: mov_rsi's
    1..25 percentile-band deeper edges, boll_std's 0.75..3.5 quarter-σ
    deeper edges; absent bands omitted)."""
    return {
        str(k): round(v[0] / v[1], 8)
        for k, v in sorted(acc.items()) if v[1] > 1e-12
    }
