"""mov_rsi CURVE construction — the continuous depth-response rows
(analysis.analysis_signals.engines.signal_families.mov_rsi.curve.
_curves).

THE formulation (the 2026-10 RSI depth-response study + the
2026-10-06 tails follow-up, 10 study indices; see
database/sql/analysis/analysis_signals/01_signals.sql's signal_curves
block for the SQL-side contract):

    s      = Phi^-1(1 - q)         the day's Gaussian depth score; q =
                                   its tail probability = the
                                   interpolated plotting-position
                                   percentile rank (rank + 0.5)/n of
                                   the day's RSI within the snapshot
                                   window (bottom q = p_hat, top
                                   q = 1 - p_hat)

    curve(s) = mu_center + interp(fit_curve, s)

  per (code, rsi_window, side, regime_state, snapshot, POPULATION):
    every cell emits TWO is_typical population variants x TWO boost
    variants —
    population TRUE = the TYPICAL pass's days (SIDE-MIRRORED — the
    amplified tail event differs by side: the BUY side's isolated
    first touches, an episode starting > RSI_TYPICAL_ISOLATED_DAYS
    trading days after the previous same-side band day — the
    capitulation; the SELL side's re-tests, a fresh episode whose
    previous overbought episode ended within the ISOLATED window —
    the double top), FALSE = the re-entry passes' days (flicker
    re-qualifications within the K=5 exit window, buy-side
    continuation starts — falling knives — and sell-side isolated
    breakouts, momentum ignitions after calm — the noisy population;
    the day's pop_flag = its is_typical episode-pass attribution —
    each pass INCLUDES its gap-1 continuation days, so the 2026-10-06
    registration-only band-edge defect does not apply; the boolean
    replaces the 0/1/2 population smallint the same day — the
    recovered pre-2026-10-06 column shape); a pass-population cell
    below the CURVE_MIN_TRIGGERS floor doesn't emit:
    mu_center  the cell's EPISODE-WEIGHTED sign-aligned blended mean
               forward change (the 0.10/0.15/0.50/0.15/0.10 next/3d/
               5d/10d/20d mix renormalized over valid legs — the
               threshold tier's dir_ave unit)
    s_center   the cell's own weighted mean depth score (computed on
               demand — NEVER the theoretical uniform-tail constant)
    fit_curve  the OLS slope's REPLACEMENT (2026-10-06): the WEIGHTED
               ISOTONIC shape per (side, regime, pass) — TWO-TIER
               (2026-10-07): the CODE's OWN pool (its windows x days)
               when it carries >= CURVE_OWN_POOL_MIN_EPISODES episodes
               AND clears CURVE_SLOPE_MIN_T on its own (the class
               pooling exists only to buy significance for thin codes,
               so it is OPTIONAL), else the pooled
               (index_class, side, regime) pool across the snapshot's
               codes x windows (per-cell shapes are too thin; the study
               found the gradient regime-dependent, window/code
               roughly invariant — and the 2026-10-06 per-index-class
               refinement: unlike families must not borrow each
               other's gradient, so the pool is the code's OWN
               stats.sec_classification industry_id — 2026-10-07: the
               L2 industry_id, not the L1 sector_id). The study showed
               the response is a
               deep-tail reversion STEP at q <= 1-2% over a shallow
               plateau — a probit-linear fit smears the step and
               extrapolates the unmeasured deep half, so the shape is
               the episode-weighted PAVA (isotonic regression),
               direction following the pool's weighted Spearman sign,
               the buy side forced nondecreasing (the retired linear
               fit's >= 0 guard, subsumed — a code's own significant
               NEGATIVE buy rho stores its own flat outcome, never the
               class shape). SIGNIFICANCE-GATED: stored
               only when the selected pool's |fit_t| >= CURVE_SLOPE_MIN_T — the
               cluster-adjusted t = rho * sqrt((n_ep - 2)/(1 - rho^2))
               with n_ep = the pool's EPISODE count (within-episode
               days are correlated through overlapping forward
               windows; the effective sample is episodes, not days) —
               else all-zero offsets (the cell draws flat at its
               mu_center)
    s_lo/s_hi  the cell's MEASURED s range (min/max of its in-band
               days) — consumers draw the fitted shape only inside it;
               no extrapolation beyond the data
    mu_{n}d    the cell's episode-weighted sign-aligned mean forward
               change at horizon n ∈ {1, 3, 5, 10, 20} (finite legs
               only; NULL when no day carries the leg) — the horizon
               PATH beside the blend: mu_center is the per-row
               RENORMALIZED 0.10/0.15/0.50/0.15/0.10 mix, not the
               legs' weighted mean (the two coincide only when every
               leg is finite on every day)
    fit_t      the pooled fit's cluster-adjusted t — the gate's input,
               denormalized so each row explains its own flat outcome
    band_curve the cell's EMPIRICAL depth response at 1% stride over
               the whole tail ({k: episode-weighted mean y of q in
               ((k-1)%, k%]}, k = 1..25) — the measured shape beside
               the isotonic fit
    tail_bars  the (code, window, side)'s per-side percentile RSI bars
               (bottom = the window's q(p/100), top = q(1−p/100) —
               the threshold tier's construction, so the two tiers'
               bars agree at the shared pcts) at CURVE_TAIL_PCTS
               (1/2/3/5/10/15/20/25 — mirroring the fit population) —
               the denormalized s -> RSI mapping

POPULATION (the 2026-10-06 tails change; the 2026-10-07 is_typical
recovery): EVERY tail-episode day of the snapshot window belongs to
ONE pass — mov_ave_rsi.typical_ep_size_{W}days non-NULL, the pass
picked by the is_typical flag: first-touch + re-entry registrations
AND the gap-1 continuation days (which the flags attribute to their
episode pass; the threshold tier joined this episode population
2026-10-07). The tails study found registration-only populations sit
at the band edge by construction (both TRUE and FALSE are entry
events — mean depth ~19%, the q <= 10% tail unmeasured) while the
deep days are exactly the continuations. Each day is
weighted 1/ep_size — each episode contributes total weight exactly 1,
so the response counts EPISODES, not days (an episode that burrows
deep would otherwise dominate the fit by its length); n_episodes =
round(sum of the weights) is the cell's effective sample (the emit
floor), and n the raw day count.

Study findings this encodes: the rebound is exponential-in-percentile
== linear-in-probit IN THE SHALLOW TAIL but steps up at q <= 1-2%
(the 2026-10-06 1-6% ladder study: +4% at (0,1%] vs a +0.1..1.2%
plateau at (1,12%]) — hence isotonic, not linear; and the depth
response is real only where it is monotonic (sell x big-index classes
flat-to-reversed — hence the significance gate + the rho-sign
direction rule + the per-index-class pool).

THE BOOST VARIANT (2026-10-06): every cell emits TWO signal_curves
rows (the population-variant PK) — is_ohlcv_boosted FALSE (the plain
episode-weighted fit above) and TRUE (the BOOST twin): the SAME
in-band days with every statistic weight-blended by the day's episode
weight TIMES its ohlcv trust weight (engines/ohlcv_boost
anchor_scores on the cell's own trigger frame — the axes ride the
window slice; the depth axis z-scores the raw RSI value within
(code, window, side, regime), which is shift-invariant so no bar is
needed). n, n_episodes, s_lo/s_hi and tail_bars are shared; mu_center
/ s_center / the mu_{n}d legs / the pooled shape + gate stats /
band_curve are the weighted counterparts, each variant gated on its
OWN fit_t. The UI's OHLCV-boost toggle picks the variant.

COMPUTE LAYOUT: the heavy frame phases (melt / rank / masks / forward
changes) are whole-frame cudf.pandas, chunked by the prepared frame's
code partitions exactly like BucketContext.buckets. The ONE host
boundary: the per-snapshot trigger + bar frames (a few thousand rows)
materialize once via engine.frame_records (the sanctioned COPY-boundary
scalar materialization); s = ndtri(1 - q), the per-cell aggregates,
the weighted Spearman + PAVA and the per-cell accumulators run on
plain host numpy — the per-group regression core, documented here.
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd
from scipy.special import ndtri

from _common.labels import REGIME_CODE, SEC_TYPE_CODE, SIDE_CODE, SIGNAL_TYPE_CODE

# the POOLED all-regime population's regime_state code (stats.label_dim
# 'regime' 4 = 'all' — the per-key curve fitted over ALL regimes)
REGIME_ALL = REGIME_CODE["all"]

from analysis.analysis_signals.config import (
    CURVE_MIN_TRIGGERS,
    CURVE_TAIL_PCTS,
    FORWARD_HORIZONS,
    MIXED_HORIZON_WEIGHTS,
    RSI_SIDES,
    UNCLASSIFIED_CLASS,
    window_lower,
)
from analysis.analysis_signals.engines._base import SignalEngine
from analysis.analysis_signals.engines._curve_fit import (
    band_dict as _band_dict,
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
    _tail_quantile_bars,
)

logger = logging.getLogger(__name__)

_WEIGHTS = {n: MIXED_HORIZON_WEIGHTS[n] for n in FORWARD_HORIZONS}

_CURVE_CELL_COLS = [
    "code", "rsi_window", "side", "regime_state",
    "q", "y_dir", "ep_w", "pop_flag",
] + [f"y_leg_{n}" for n in FORWARD_HORIZONS] + ["boost_weight"]

# the boost weights' z-score group (the curve cell — the raw RSI value
# z-scored within it is shift-invariant, so the weight needs no bar)
_BOOST_IDS = ["code", "rsi_window", "side", "regime_state"]

# the stored fit_curve's s grid (the pooled step function sampled for
# the JSON payload; consumers interpolate offsets linearly) — built
# inside engines/_curve_fit.fit_payload from CURVE_FIT_S_GRID (the
# family default) when no explicit grid is passed.


def curve_records(
    engine: SignalEngine,
    ctx: BucketContext,
    spec,
    sec_type: str,
    rsi_windows: tuple,
) -> list[dict]:
    """One snapshot's signal_curves rows — the fitted depth-response
    shape per (code, rsi_window, side, regime_state) cell over the
    snapshot's trailing window, TWO episode-pass populations x TWO
    boost variants per cell (is_typical TRUE = the first-touch passes'
    days, FALSE = the re-entry passes' days via the day's pop_flag —
    see the module docstring's population block)."""
    df = ctx._df
    cols = [
        "code", "date", "_t", "regime", "first_date",
        "pattern", "vol_state", "px_speed",
    ] + [f"rsi_{w}days" for w in rsi_windows] \
      + [f"ep_size_{w}days" for w in rsi_windows] \
      + [f"is_typical_{w}days" for w in rsi_windows] \
      + [f"next_change_{n}d" for n in FORWARD_HORIZONS]
    lower = np.datetime64(spec.lower)
    upper = np.datetime64(spec.upper)
    trig_parts: list[pd.DataFrame] = []
    bar_parts: list[pd.DataFrame] = []
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
        cells = _curve_cells(win, rsi_windows)
        if not cells.empty:
            trig_parts.append(cells)
        bars = _tail_bars(win, rsi_windows)
        if not bars.empty:
            bar_parts.append(bars)
    if not trig_parts:
        return []

    # ---- the ONE host boundary: materialize once, aggregate on host ----
    trig = pd.concat(trig_parts, ignore_index=True)
    # the per-day boost trust weight (engines/ohlcv_boost — the day-0
    # axes ride the window slice; the depth z within
    # (code, window, side, regime) needs no bar: shift-invariant in
    # the value offset)
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
    # the pw slots feed the FALSE row, the bw slots the boost twin
    # (same days, weighted).
    leg_names = [f"y_leg_{n}" for n in FORWARD_HORIZONS]
    # per-cell accumulator keyed (code, window, side, regime, IS_TYPICAL):
    # True = the first-touch passes' days, False = the re-entry passes'
    # days (the day's pop_flag ride; 2026-10-07 pass attribution; the
    # boolean replaces the 0/1/2 population smallint the same day —
    # the recovered pre-2026-10-06 column shape, the all-pass union
    # population 0 dropped: it is exactly the two passes' union). BOTH
    # boost variants per population:
    #   [n, pw*y_sum, pw*s_sum, pw_sum, bw*y_sum, bw*s_sum, bw_sum]
    #   then per horizon leg (pw sum, pw count, bw sum, bw count) —
    # the pw slots feed the is_ohlcv_boosted FALSE row, the bw slots
    # the boost twin (same days, weighted).
    cells: dict[tuple, list[float]] = {}
    # per-cell EMPIRICAL band accumulators, keyed like `cells`:
    #   plain {k: [pw*y_sum, pw_sum]} / boost {k: [bw*y_sum, bw_sum]}
    bands: dict[tuple, dict[int, list[float]]] = {}
    boost_bands: dict[tuple, dict[int, list[float]]] = {}
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
        s = float(ndtri(1.0 - rec["q"]))
        y = float(rec["y_dir"])
        pw = float(rec["ep_w"])
        bw = pw * float(rec["boost_weight"])
        flag = rec["pop_flag"]
        pops = ([True] if flag == 1.0 else []) \
             + ([False] if flag == 0.0 else [])
        key = (
            rec["code"], int(rec["rsi_window"]),
            int(rec["side"]), int(rec["regime_state"]),
        )
        # every day feeds its own regime's cells and the POOLED
        # all-regime cells (regime_state = REGIME_ALL) — the
        # user-facing All curve beside the four regime curves — each
        # in the day's own pass population (its pop_flag's boolean)
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
                k = min(25, max(1, int(np.ceil(rec["q"] * 100))))
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
    bars = _bar_map(engine, bar_parts)

    out: list[dict] = []
    for (code, rsi_window, side, regime, is_typ), acc in cells.items():
        n = acc[0]
        n_episodes = int(round(acc[3]))
        # the effective-sample emit floor: a cell must carry at least
        # CURVE_MIN_TRIGGERS EPISODES (a 40-day single episode is one
        # observation, not a curve)
        if n_episodes < CURVE_MIN_TRIGGERS:
            continue
        cls = engine.code_class.get(code, UNCLASSIFIED_CLASS)
        # the TWO-TIER fit (2026-10-07): the code's OWN pool first when
        # it is decisive on its own (>= CURVE_OWN_POOL_MIN_EPISODES
        # episodes and |t| >= CURVE_SLOPE_MIN_T), else the
        # (industry-class, side, regime, pass) pool — the stored
        # fit_rho/fit_t describe whichever shape is stored
        (rho, t), iso = _prefer_own_fit(
            own_pooled.get((code, side, regime, is_typ), []),
            pooled.get((cls, side, regime, is_typ), []),
            side,
        )
        (w_rho, w_t), w_iso = _prefer_own_fit(
            own_boost_pooled.get((code, side, regime, is_typ), []),
            boost_pooled.get((cls, side, regime, is_typ), []),
            side,
        )
        ww = acc[3]
        bww = acc[6]
        s_center = acc[2] / ww if ww > 1e-12 else 0.0
        bs_center = acc[5] / bww if bww > 1e-12 else s_center
        s_range = cell_s.get(
            (code, rsi_window, side, regime, is_typ), [0.0],
        )
        shared = {
            "code": code,
            "sec_type": SEC_TYPE_CODE[sec_type],
            "signal_type": SIGNAL_TYPE_CODE[engine.signal_type],
            "audit_window": rsi_window,
            "side": side,
            "regime_state": regime,
            "is_typical": is_typ,
            "start_date": window_lower(spec.stat_date),
            "end_date": spec.stat_date,
            "n": n,
            "n_episodes": n_episodes,
            # the measured depth range + the bars are population
            # facts — shared verbatim
            "s_lo": round(min(s_range), 6),
            "s_hi": round(max(s_range), 6),
            "tail_bars": json.dumps(bars.get(
                (code, rsi_window, side), {},
            )),
        }
        ckey = (code, rsi_window, side, regime, is_typ)
        plain = {
            **shared,
            "is_ohlcv_boosted": False,
            "mu_center": round(acc[1] / ww, 8) if ww > 1e-12 else 0.0,
            "s_center": round(s_center, 6),
            "fit_curve": json.dumps(_fit_payload(iso, s_center, t)),
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
            "fit_curve": json.dumps(_fit_payload(w_iso, bs_center, w_t)),
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
#  Frame phases (whole-frame cudf — the threshold tier's patterns)
# ---------------------------------------------------------------------------

def _curve_cells(
    win: pd.DataFrame, rsi_windows: tuple,
) -> pd.DataFrame:
    """One window slice's in-band days with their tail probability q,
    the per-row blended sign-aligned forward change y_dir and the
    episode weight ep_w = 1/ep_size. The POPULATION is the rsi
    analysis layer's episode sizes (typical_ep_size_{W}days — non-NULL
    on EVERY in-band day: registrations AND the gap-1 continuations;
    the 2026-10-06 tails change); the snapshot-window plotting
    position stays the depth axis (q), and the side is the day's
    nearer extreme."""
    id_vars = ["code", "date", "_t", "regime_state",
               "pattern", "vol_state", "px_speed"]
    fwd = [f"next_change_{n}d" for n in FORWARD_HORIZONS]
    size_cols = [f"ep_size_{w}days" for w in rsi_windows]
    flag_cols = [f"is_typical_{w}days" for w in rsi_windows]
    win = win.rename(columns={"regime": "regime_state"})
    long = win.melt(
        id_vars=id_vars + fwd + size_cols + flag_cols,
        value_vars=[f"rsi_{w}days" for w in rsi_windows],
        var_name="_wcol", value_name="value",
    )
    long["rsi_window"] = long["_wcol"].map(
        {f"rsi_{w}days": w for w in rsi_windows}
    )
    long = long[_finite_mask(long["value"])]
    if long.empty:
        return long

    # per-day percentile rank within (rsi_window, code) over the
    # window — the interpolated plotting position (rank + 0.5)/n,
    # never exactly 0/1 (an exact-0 rank-0 day would give
    # s = Phi^-1(1) = inf). The DEPTH AXIS only — population membership
    # is the stored episode-size column's job now.
    keys = ["rsi_window", "code"]
    v = long.sort_values(keys + ["value"]).reset_index(drop=True)
    g = v.groupby(keys, sort=False)
    v["_r"] = g.cumcount()
    n_tbl = g.size().rename("_n").reset_index()
    v = v.merge(n_tbl, on=keys, how="left")
    v["p_hat"] = (v["_r"].astype("float64") + 0.5) \
        / v["_n"].astype("float64")
    v = v.drop(columns=["_r", "_n"])

    # the threshold tier's anchor rule: a valid next-day forward
    # change (guarantees the blend's denominator never vanishes)
    v = v[_finite_mask(v["next_change_1d"])]
    if v.empty:
        return v
    # the side: the day's nearer extreme (band days whose snapshot
    # p_hat lands mid-band keep a determinate side); q follows the
    # engine's per-side convention (bottom q = p_hat, top q = 1 - p_hat)
    top = v["p_hat"] > 0.5
    v["side"] = np.where(top, np.int16(SIDE_CODE["top"]),
                         np.int16(SIDE_CODE["bottom"])).astype("int16")
    v["q"] = np.where(top, 1.0 - v["p_hat"], v["p_hat"])
    v = _blend_per_row(v)
    # the POPULATION + WEIGHT: the window's episode-size column (NULL
    # out-of-band, the episode's trading-day count on every in-band
    # day) — keep the sized days; each day's weight = 1/size so one
    # episode contributes exactly 1. pop_flag rides beside: the day's
    # episode-PASS flag (1.0 first-touch pass / 0.0 re-entry pass —
    # the 2026-10-07 pass attribution; the gap-1 continuations inherit
    # their pass's flag) — the curve rows' population split.
    _eps = pd.Series(np.nan, index=v.index, dtype="float64")
    _flg = pd.Series(np.nan, index=v.index, dtype="float64")
    for w in rsi_windows:
        in_w = v["rsi_window"] == w
        _eps = _eps.mask(in_w, v[f"ep_size_{w}days"])
        _flg = _flg.mask(in_w, v[f"is_typical_{w}days"])
    keep = _eps.notna()
    v = v[keep]
    if v.empty:
        return v
    v["ep_w"] = 1.0 / _eps[keep]
    v["pop_flag"] = _flg[keep]
    return v.drop(columns=size_cols + flag_cols)


def _blend_per_row(cells: pd.DataFrame) -> pd.DataFrame:
    """The engine's horizon blend applied per-row: y = Σ w_n·c_n /
    Σ_{valid legs} w_n (renormalized over the row's own valid legs —
    _mixed_blend's formula at row granularity), sign-aligned by the
    side code (bottom as-is, top negated). Each leg's own sign-aligned
    forward change rides beside the blend as y_leg_{n} (NaN where the
    leg is missing) — the horizon path the curve rows surface."""
    from analysis.analysis_signals.config import SELL_SIDE_CODES

    denom = None
    numer = None
    fins: dict[int, pd.Series] = {}
    for n, w in _WEIGHTS.items():
        col = f"next_change_{n}d"
        fin = fins[n] = _finite_mask(cells[col])
        term = (cells[col] * w).where(fin, 0.0)
        wf = fin.astype("float64") * w
        denom = wf if denom is None else denom + wf
        numer = term if numer is None else numer + term
    sign = pd.Series(1.0, index=cells.index, dtype="float64")
    sign = sign.mask(cells["side"].isin(SELL_SIDE_CODES), -1.0)
    cells["y_dir"] = (numer / denom) * sign
    for n in FORWARD_HORIZONS:
        cells[f"y_leg_{n}"] = cells[f"next_change_{n}d"].where(
            fins[n],
        ) * sign
    return cells


def _tail_bars(
    win: pd.DataFrame, rsi_windows: tuple,
) -> pd.DataFrame:
    """The (rsi_window, code, side, pct) RSI bars at CURVE_TAIL_PCTS
    over the window's values — the SAME _tail_quantile_bars
    construction the threshold tier's bars use (the side's own tail
    quantile), at the curve's wider pct grid, so the tail_bars JSON
    and the strategies' stored bars agree exactly at the shared pcts
    {1,2,3} (the denormalized s -> RSI mapping)."""
    id_vars = ["code", "date", "_t", "regime"]
    long = win.melt(
        id_vars=id_vars,
        value_vars=[f"rsi_{w}days" for w in rsi_windows],
        var_name="_wcol", value_name="value",
    )
    long["rsi_window"] = long["_wcol"].map(
        {f"rsi_{w}days": w for w in rsi_windows}
    )
    long = long[_finite_mask(long["value"])].drop(columns=["_wcol"])
    if long.empty:
        return pd.DataFrame()
    return _tail_quantile_bars(
        long[["rsi_window", "code", "value"]],
        group_cols=["rsi_window", "code"],
        pcts=CURVE_TAIL_PCTS, sides=RSI_SIDES,
    )


def _bar_map(
    engine: SignalEngine, bar_parts: list[pd.DataFrame],
) -> dict[tuple, dict[str, float]]:
    """(code, rsi_window, side) -> {"1": bar, "2": bar, ...} — the
    tail_bars JSON payload, materialized through the same sanctioned
    records boundary."""
    out: dict[tuple, dict[str, float]] = {}
    if not bar_parts:
        return out
    bars = pd.concat(bar_parts, ignore_index=True)
    for rec in engine.frame_records(
        bars[["code", "rsi_window", "side", "pct", "bar"]],
    ):
        key = (rec["code"], int(rec["rsi_window"]), int(rec["side"]))
        out.setdefault(key, {})[str(int(rec["pct"]))] = round(
            float(rec["bar"]), 6,
        )
    return out


# ---------------------------------------------------------------------------
#  Host fit core — MOVED 2026-10-07 to engines/_curve_fit.py (verbatim;
#  the family-agnostic primitives the boll_std curve tier shares). The
#  imports above alias them under their original private names; the
#  per-family accumulator + frame phases stay here.
# ---------------------------------------------------------------------------
