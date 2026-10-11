"""boll_std CURVE construction — the continuous σ-depth response rows
(analysis.analysis_signals.engines.signal_families.boll_std.curve.
_curves).

THE formulation — the mov_rsi depth-response model transplanted onto
the Bollinger σ-depth (see engines/_curve_fit.py for the shared fit
core and mov_rsi/curve/_curves.py for the RSI original):

    s      = |z|                     the day's σ-depth — which IS the
                                     Gaussian depth score: the tail
                                     probability of |z| under Φ is
                                     q = 1 - Φ(|z|), so
                                     Φ^-1(1-q) == |z| exactly. The
                                     fixed bars 2 / 2.5 / 3σ are points
                                     ON this axis (one-sided Gaussian
                                     tail masses 2.28% / 0.62% /
                                     0.13%); the curve spans the whole
                                     episode band |z| >= 0.6745 (the
                                     25% tail edge).

    curve(s) = mu_center + interp(fit_curve, s)

  per (code, boll_window, side, regime_state, snapshot, POPULATION):
    every cell emits TWO is_typical population variants x TWO boost
    variants — the SAME population split as mov_rsi (the boll analysis
    layer's side-mirrored pass flags attribute every in-band day to its
    pass), each day weighted 1/ep_size so the response counts EPISODES,
    not days; a pass-population cell below CURVE_MIN_TRIGGERS episodes
    doesn't emit:
    mu_center  the cell's EPISODE-WEIGHTED sign-aligned blended mean
               forward change (the shared 0.10/0.15/0.50/0.15/0.10 mix
               renormalized over valid legs — the threshold tier's
               dir_ave unit)
    s_center   the cell's own weighted mean σ-depth
    fit_curve  the WEIGHTED ISOTONIC shape per (side, regime, pass),
               TWO-TIER (2026-10-07) and SIDE-AWARE (2026-10-11):
               the CODE's OWN pool (its windows x days) when it
               carries >= CURVE_OWN_POOL_MIN_EPISODES episodes AND
               clears the side's gate on its own (the class pooling
               exists only to buy significance for thin codes, so it
               is OPTIONAL), else the pooled (index_class, side,
               regime, pass) pool. SELL gates on the weighted
               Spearman's cluster t (engines/_curve_fit.pooled_iso —
               the measured sell response IS rank-monotone: the
               deeper overbought breach expects the smaller decline);
               BUY gates on the PRE-REGISTERED deep-arm contrast
               (engines/_curve_fit.deep_arm_iso at
               BOLL_CURVE_DEEP_ARM_S = 2.5σ — the measured buy
               response is a shallow plateau with the favorable rise
               concentrated past ~2.5σ, which the rank correlation
               reads ~0-to-negative, so the rank gate stored the flat
               payload everywhere; the arm contrast restores the
               loosely-monotone plateau → deep-rise shape).
               significance-gated |fit_t| >= CURVE_SLOPE_MIN_T on the
               SELECTED pool (fit_t = the rank t for sells, the arm
               contrast t for buys), sampled on the family grid
               BOLL_CURVE_FIT_S_GRID (0.66..3.10 — past the 3σ bar, so
               the deepest emitted threshold sits INSIDE the drawn
               fit)
    s_lo/s_hi  the cell's MEASURED |z| range (draw the fit only inside)
    mu_{n}d    the per-horizon episode-weighted sign-aligned means
    band_curve the EMPIRICAL response at the QUARTER-σ stride over the
               episode band ({key: episode-weighted mean y of the days
               with σ-depth s in (key−0.25, key]}, key = 0.75, 1.0,
               ..., 3.5 — the deeper edge, the same edge-key convention
               as mov_rsi's percentile bands) — NO percentile logic
               here: the family's x axis is the EXCESS σ-DEPTH over the
               bands, and the 2 / 2.5 / 3σ thresholds are edges of this
               grid (every other edge), so the empirical dots read
               directly against the emitted bars
    tail_bars  the family's OWN bars on the s axis — the emitted σ
               thresholds themselves ({"2": 2.0, "2p5": 2.5, "3": 3.0},
               keyed by the sub_type's std literal): in the σ-depth
               space the bar at kσ IS k; no percentile mapping exists.

COMPUTE LAYOUT (GPU-first): the frame phases (melt / finite filter /
population masks / side / s = |z| / the quarter-σ band key / the
per-row blend) are whole-frame cudf.pandas — s and the band key are
ELEMENTWISE (s = |z|; key = 0.25·ceil(s / 0.25) clamped to the
[0.75, 3.5] grid — no scipy, no percentile ranks), chunked by the
prepared frame's code partitions exactly like the threshold tier. The
ONE host boundary: the per-snapshot trigger frame materializes once
through engine.frame_records and the per-cell accumulator + the shared
fit core (engines/_curve_fit — weighted Spearman + PAVA) run on plain
host numpy — the documented per-group regression core, identical to
mov_rsi's.
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd

from _common.labels import REGIME_CODE, SEC_TYPE_CODE, SIDE_CODE, SIGNAL_TYPE_CODE

# the POOLED all-regime population's regime_state code (stats.label_dim
# 'regime' 4 = 'all' — the per-key curve fitted over ALL regimes)
REGIME_ALL = REGIME_CODE["all"]

from analysis.analysis_signals.config import (
    BOLL_CURVE_DEEP_ARM_S,
    BOLL_CURVE_FIT_S_GRID,
    BOLL_STDS_EMIT,
    CURVE_MIN_TRIGGERS,
    FORWARD_HORIZONS,
    SELL_SIDE_CODES,
    UNCLASSIFIED_CLASS,
    format_std,
    window_lower,
)
from analysis.analysis_signals.engines._base import SignalEngine
from analysis.analysis_signals.engines._curve_fit import (
    band_dict as _band_dict,
    deep_arm_iso as _deep_arm_iso,
    fit_payload as _fit_payload,
    prefer_own_fit as _prefer_own_fit,
)
from analysis.analysis_signals.engines.ohlcv_boost import (
    anchor_scores,
    side_configs,
)
from analysis.analysis_signals.engines.signal_families.mov_rsi._buckets import (
    BucketContext,
    _finite_mask,
)
from analysis.analysis_signals.engines.signal_families.mov_rsi.curve._curves import (
    _blend_per_row,
)

logger = logging.getLogger(__name__)

# the stored fit_curve's s grid (the pooled step function sampled for
# the JSON payload; consumers interpolate offsets linearly) — the
# FAMILY grid, past the 3σ bar
_BOLL_FIT_GRID = np.round(np.arange(
    BOLL_CURVE_FIT_S_GRID[0],
    BOLL_CURVE_FIT_S_GRID[1] + BOLL_CURVE_FIT_S_GRID[2] / 2,
    BOLL_CURVE_FIT_S_GRID[2],
), 6)

# the band_curve's QUARTER-σ stride grid (deeper-edge keys): the days
# with σ-depth s in (key−0.25, key], key = 0.75, 1.0, ..., 3.5 — the
# emitted 2 / 2.5 / 3σ thresholds are edges of this grid (every other
# edge), so the empirical dots read directly against the bars. The
# quarter multiples are exact binary fractions — the float keys are
# hash-stable. NO percentile logic anywhere in this family: the x axis
# is the excess σ-depth over the bands.
_BAND_STRIDE = 0.25
_BAND_M_MIN = 3     # key 0.75 (covers the episode-band edge 0.6745)
_BAND_M_MAX = 14    # key 3.50 (past the deepest 3σ bar)

# the tail_bars JSON payload: the family's OWN bars on the s axis —
# the emitted σ thresholds themselves, keyed by the sub_type's std
# literal ({"2": 2.0, "2p5": 2.5, "3": 3.0}). In the σ-depth space the
# bar at kσ IS k — no percentile mapping exists to denormalize.
_Z_TAIL_BARS = {format_std(k): float(k) for k in BOLL_STDS_EMIT}

_CURVE_CELL_COLS = [
    "code", "boll_window", "side", "regime_state",
    "s", "band_k", "y_dir", "ep_w", "pop_flag",
] + [f"y_leg_{n}" for n in FORWARD_HORIZONS] + ["boost_weight"]

# the boost weights' z-score group (the curve cell — the raw σ-depth
# z-scored within it is shift-invariant, so the weight needs no bar)
_BOOST_IDS = ["code", "boll_window", "side", "regime_state"]


def _buy_arm_fit(
    points: list[tuple[float, float, float]], side: int,
) -> tuple[tuple[float, float], tuple[np.ndarray, np.ndarray] | None]:
    """The BUY pool's loosely-monotone fit: ``deep_arm_iso`` at the
    family's fixed 2.5σ arm edge (the pre-registered deep arm — the
    rank-correlation gate read ~0-to-negative on the measured U-shaped
    response and stored the flat payload everywhere; see
    engines/_curve_fit.deep_arm_iso). The ``side`` argument completes
    the ``prefer_own_fit`` fitter contract; the buy shape is always
    the increasing PAVA."""
    return _deep_arm_iso(points, side, edge=BOLL_CURVE_DEEP_ARM_S)


def _resolve_pool_fit(
    own: list[tuple[float, float, float]],
    pooled: list[tuple[float, float, float]],
    side: int,
) -> tuple[tuple[float, float], tuple[np.ndarray, np.ndarray] | None]:
    """The TWO-TIER pool fit, side-aware: the SELL side keeps the
    rank-gated isotonic (its measured response IS rank-monotone — the
    deeper overbought breach expects the smaller decline), the BUY
    side fits the loose deep-arm gate (2026-10-11) — both through the
    same two-tier own-pool-first resolution."""
    if side in SELL_SIDE_CODES:
        return _prefer_own_fit(own, pooled, side)
    return _prefer_own_fit(own, pooled, side, fit_fn=_buy_arm_fit)


def curve_records(
    engine: SignalEngine,
    ctx: BucketContext,
    spec,
    sec_type: str,
    boll_windows: tuple,
) -> list[dict]:
    """One snapshot's signal_curves rows — the fitted σ-depth response
    per (code, boll_window, side, regime_state) cell over the snapshot's
    trailing window, TWO episode-pass populations x TWO boost variants
    per cell (is_typical TRUE = the typical pass's days, FALSE = the
    re-entry pass's days via the day's pop_flag — the same population
    split as mov_rsi; see the module docstring)."""
    df = ctx._df
    cols = [
        "code", "date", "_t", "regime", "first_date",
        "pattern", "vol_state", "px_speed",
    ] + [f"boll_z_{w}days" for w in boll_windows] \
      + [f"ep_size_{w}days" for w in boll_windows] \
      + [f"is_typical_{w}days" for w in boll_windows] \
      + [f"next_change_{n}d" for n in FORWARD_HORIZONS]
    lower = np.datetime64(spec.lower)
    upper = np.datetime64(spec.upper)
    trig_parts: list[pd.DataFrame] = []
    for pc in sorted(df["_pc"].drop_duplicates().to_numpy().tolist()):
        mask = (
            (df["_pc"] == pc)
            & (df["first_date"] <= upper)
            & (df["date"] >= lower)
            & (df["date"] <= upper)
        )
        win = df.loc[mask, cols]
        if win.empty:
            continue
        cells = _curve_cells(win, boll_windows)
        if not cells.empty:
            trig_parts.append(cells)
    if not trig_parts:
        return []

    # ---- the ONE host boundary: materialize once, aggregate on host ----
    trig = pd.concat(trig_parts, ignore_index=True)
    # the per-day boost trust weight (engines/ohlcv_boost — the day-0
    # axes ride the window slice; the depth axis z-scores the raw σ
    # depth within (code, window, side, regime) — shift-invariant, no
    # bar needed). No measured config yet → weight 1 everywhere (the
    # boost twin equals the plain row until one is calibrated).
    trig = anchor_scores(
        trig, _BOOST_IDS, side_configs(engine.signal_type, sec_type),
        excess_col="value",
    )
    records = engine.frame_records(trig[_CURVE_CELL_COLS])
    # per-cell accumulator, BOTH population variants (plain weights =
    # the episode weights pw = 1/ep_size; boost weights bw = pw x the
    # day's ohlcv trust weight):
    #   [n, pw*y_sum, pw*s_sum, pw_sum, bw*y_sum, bw*s_sum, bw_sum]
    #   then per horizon leg (pw sum, pw count, bw sum, bw count) —
    # the pw slots feed the is_ohlcv_boosted FALSE row, the bw slots
    # the boost twin (same days, weighted).
    leg_names = [f"y_leg_{n}" for n in FORWARD_HORIZONS]
    cells: dict[tuple, list[float]] = {}
    bands: dict[tuple, dict[float, list[float]]] = {}
    boost_bands: dict[tuple, dict[float, list[float]]] = {}
    pooled: dict[tuple, list[tuple[float, float, float]]] = {}
    boost_pooled: dict[tuple, list[tuple[float, float, float]]] = {}
    # the per-CODE pools (the 2026-10-07 two-tier fit's first tier:
    # keyed (code, side, regime, pass) — the code's OWN windows x days
    # points; a code with >= CURVE_OWN_POOL_MIN_EPISODES episodes and a
    # significant own |t| fits its own shape and never borrows the
    # class gradient)
    own_pooled: dict[tuple, list[tuple[float, float, float]]] = {}
    own_boost_pooled: dict[tuple, list[tuple[float, float, float]]] = {}
    cell_s: dict[tuple, list[float]] = {}
    for rec in records:
        s = float(rec["s"])
        y = float(rec["y_dir"])
        pw = float(rec["ep_w"])
        bw = pw * float(rec["boost_weight"])
        flag = rec["pop_flag"]
        pops = ([True] if flag == 1.0 else []) \
             + ([False] if flag == 0.0 else [])
        key = (
            rec["code"], int(rec["boll_window"]),
            int(rec["side"]), int(rec["regime_state"]),
        )
        # every day feeds its own regime's cells and the POOLED
        # all-regime cells (regime_state = REGIME_ALL) — each in the
        # day's own pass population
        for rkey in (int(rec["regime_state"]), REGIME_ALL):
            for is_typ in pops:
                ckey = key[:3] + (rkey, is_typ)
                acc = cells.setdefault(
                    ckey,
                    [0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
                    + [0.0, 0.0, 0.0, 0.0] * len(FORWARD_HORIZONS),
                )
                acc[0] += 1
                acc[1] += pw * y
                acc[2] += pw * s
                acc[3] += pw
                acc[4] += bw * y
                acc[5] += bw * s
                acc[6] += bw
                k = float(rec["band_k"])
                pa = bands.setdefault(ckey, {}).setdefault(k, [0.0, 0.0])
                pa[0] += pw * y
                pa[1] += pw
                ba = boost_bands.setdefault(
                    ckey, {},
                ).setdefault(k, [0.0, 0.0])
                ba[0] += bw * y
                ba[1] += bw
                for i, ln in enumerate(leg_names):
                    v = rec[ln]
                    if v is not None and np.isfinite(v):
                        b = 7 + 4 * i
                        acc[b] += pw * float(v)
                        acc[b + 1] += pw
                        acc[b + 2] += bw * float(v)
                        acc[b + 3] += bw
                cell_s.setdefault(ckey, []).append(s)
        pcls = engine.code_class.get(rec["code"], UNCLASSIFIED_CLASS)
        for rkey in (int(rec["regime_state"]), REGIME_ALL):
            for is_typ in pops:
                pkey = (pcls, int(rec["side"]), rkey, is_typ)
                pooled.setdefault(pkey, []).append((s, y, pw))
                boost_pooled.setdefault(pkey, []).append((s, y, bw))
                okey = (rec["code"], int(rec["side"]), rkey, is_typ)
                own_pooled.setdefault(okey, []).append((s, y, pw))
                own_boost_pooled.setdefault(okey, []).append((s, y, bw))
    out: list[dict] = []
    for (code, boll_window, side, regime, is_typ), acc in cells.items():
        n = acc[0]
        n_episodes = int(round(acc[3]))
        # the effective-sample emit floor: a cell must carry at least
        # CURVE_MIN_TRIGGERS EPISODES
        if n_episodes < CURVE_MIN_TRIGGERS:
            continue
        cls = engine.code_class.get(code, UNCLASSIFIED_CLASS)
        # the TWO-TIER fit, SIDE-AWARE (2026-10-11): the code's OWN
        # pool first when it is decisive on its own (>=
        # CURVE_OWN_POOL_MIN_EPISODES episodes and clears the gate),
        # else the (industry-class, side, regime, pass) pool — the
        # SELL side gates on the weighted Spearman (its response is
        # rank-monotone), the BUY side on the pre-registered 2.5σ
        # deep-arm contrast (deep_arm_iso — the rank gate read
        # ~0-to-negative on the measured U-shaped response and stored
        # the flat payload everywhere). The stored fit_rho/fit_t
        # describe whichever shape is stored (fit_t = the gate's own
        # contrast stat per side).
        (rho, t), iso = _resolve_pool_fit(
            own_pooled.get((code, side, regime, is_typ), []),
            pooled.get((cls, side, regime, is_typ), []),
            side,
        )
        (w_rho, w_t), w_iso = _resolve_pool_fit(
            own_boost_pooled.get((code, side, regime, is_typ), []),
            boost_pooled.get((cls, side, regime, is_typ), []),
            side,
        )
        ww = acc[3]
        bww = acc[6]
        s_center = acc[2] / ww if ww > 1e-12 else 0.0
        bs_center = acc[5] / bww if bww > 1e-12 else s_center
        s_range = cell_s.get(
            (code, boll_window, side, regime, is_typ), [0.0],
        )
        shared = {
            "code": code,
            "sec_type": SEC_TYPE_CODE[sec_type],
            "signal_type": SIGNAL_TYPE_CODE[engine.signal_type],
            # the DB's generic window axis (signal_curves.audit_window
            # carries the family's own window — boll's W here)
            "audit_window": boll_window,
            "side": side,
            "regime_state": regime,
            "is_typical": is_typ,
            "start_date": window_lower(spec.stat_date),
            "end_date": spec.stat_date,
            "n": n,
            "n_episodes": n_episodes,
            # the measured σ-depth range + the bars are population
            # facts — shared verbatim
            "s_lo": round(min(s_range), 6),
            "s_hi": round(max(s_range), 6),
            "tail_bars": json.dumps(_Z_TAIL_BARS),
        }
        ckey = (code, boll_window, side, regime, is_typ)
        plain = {
            **shared,
            "is_ohlcv_boosted": False,
            "mu_center": round(acc[1] / ww, 8) if ww > 1e-12 else 0.0,
            "s_center": round(s_center, 6),
            "fit_curve": json.dumps(_fit_payload(
                iso, s_center, t, grid=_BOLL_FIT_GRID,
            )),
            "fit_rho": round(rho, 6),
            "fit_t": round(t, 4),
            "band_curve": json.dumps(_band_dict(bands.get(ckey, {}))),
        }
        boost = {
            **shared,
            # the BOOST twin: the same days, each contribution
            # weight-blended by its episode x ohlcv trust weight
            "is_ohlcv_boosted": True,
            "mu_center": round(acc[4] / bww, 8) if bww > 1e-12
            else plain["mu_center"],
            "s_center": round(bs_center, 6),
            "fit_curve": json.dumps(_fit_payload(
                w_iso, bs_center, w_t, grid=_BOLL_FIT_GRID,
            )),
            "fit_rho": round(w_rho, 6),
            "fit_t": round(w_t, 4),
            "band_curve": json.dumps(
                _band_dict(boost_bands.get(ckey, {})),
            ),
        }
        for i, n_h in enumerate(FORWARD_HORIZONS):
            b = 7 + 4 * i
            pw_leg = acc[b + 1]
            bw_leg = acc[b + 3]
            plain[f"mu_{n_h}d"] = (
                round(acc[b] / pw_leg, 8) if pw_leg > 1e-12 else None
            )
            boost[f"mu_{n_h}d"] = (
                round(acc[b + 2] / bw_leg, 8) if bw_leg > 1e-12 else None
            )
        out.append(plain)
        out.append(boost)
    return out


# ---------------------------------------------------------------------------
#  Frame phases (whole-frame cudf — elementwise σ-depth, no ranks)
# ---------------------------------------------------------------------------

def _curve_cells(
    win: pd.DataFrame, boll_windows: tuple,
) -> pd.DataFrame:
    """One window slice's in-band days with their σ-depth s = |z|, the
    1%-Gaussian-tail band key, the per-row blended sign-aligned forward
    change y_dir and the episode weight ep_w = 1/ep_size. The
    POPULATION is the boll analysis layer's episode sizes
    (typical_ep_size_{W}days — non-NULL on EVERY in-band day of the
    fixed 0.6745σ band: registrations AND the gap-1 continuations); the
    side is the sign of z (the nearer band)."""
    id_vars = ["code", "date", "_t", "regime_state",
               "pattern", "vol_state", "px_speed"]
    fwd = [f"next_change_{n}d" for n in FORWARD_HORIZONS]
    size_cols = [f"ep_size_{w}days" for w in boll_windows]
    flag_cols = [f"is_typical_{w}days" for w in boll_windows]
    win = win.rename(columns={"regime": "regime_state"})
    long = win.melt(
        id_vars=id_vars + fwd + size_cols + flag_cols,
        value_vars=[f"boll_z_{w}days" for w in boll_windows],
        var_name="_wcol", value_name="value",
    )
    long["boll_window"] = long["_wcol"].map(
        {f"boll_z_{w}days": w for w in boll_windows}
    )
    long = long[_finite_mask(long["value"])]
    if long.empty:
        return long

    # the shared anchor rule: a valid next-day forward change
    # (guarantees the blend's denominator never vanishes)
    long = long[_finite_mask(long["next_change_1d"])]
    if long.empty:
        return long
    # the side: the sign of z (the breached band); s = |z| — the
    # σ-depth IS the Gaussian depth score (see the module docstring)
    top = long["value"] > 0.0
    long["side"] = np.where(
        top, np.int16(SIDE_CODE["top"]), np.int16(SIDE_CODE["bottom"]),
    ).astype("int16")
    long["s"] = long["value"].abs()
    # the QUARTER-σ band key: the band's DEEPER edge
    # 0.25·ceil(s/0.25) clamped to the [0.75, 3.5] grid — elementwise
    # (np.ceil ufunc on the frame column; the 2 / 2.5 / 3σ thresholds
    # are edges of this grid), no percentile logic
    m = np.ceil(long["s"] / _BAND_STRIDE)
    long["band_k"] = (
        m.clip(float(_BAND_M_MIN), float(_BAND_M_MAX)) * _BAND_STRIDE
    ).round(2)
    long = _blend_per_row(long)

    # the POPULATION + WEIGHT: the window's episode-size column (NULL
    # in-band-interior, the episode's trading-day count on every
    # in-band day) — keep the sized days; each day's weight = 1/size so
    # one episode contributes exactly 1. pop_flag rides beside: the
    # day's episode-PASS flag (1.0 typical / 0.0 re-entry — the
    # side-mirrored pass attribution; the gap-1 continuations inherit
    # their pass's flag) — the curve rows' population split.
    _eps = pd.Series(np.nan, index=long.index, dtype="float64")
    _flg = pd.Series(np.nan, index=long.index, dtype="float64")
    for w in boll_windows:
        in_w = long["boll_window"] == w
        _eps = _eps.mask(in_w, long[f"ep_size_{w}days"])
        _flg = _flg.mask(in_w, long[f"is_typical_{w}days"])
    keep = _eps.notna()
    long = long[keep]
    if long.empty:
        return long
    long["ep_w"] = 1.0 / _eps[keep]
    long["pop_flag"] = _flg[keep]
    return long.drop(columns=size_cols + flag_cols + ["_wcol"])
