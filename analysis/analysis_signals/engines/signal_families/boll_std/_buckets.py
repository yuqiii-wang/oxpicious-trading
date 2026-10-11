"""In-engine Bollinger σ-depth bucket computation
(analysis.analysis_signals.engines.signal_families.boll_std._buckets).

The boll_std family's threshold-tier machinery — the mov_rsi family's
shared BucketContext engine (mov_rsi/_buckets.py) with the family's own
detection semantics substituted: the value is the Bollinger σ-depth z
(computed in-engine on the fetched price frame by boll_std/_compute —
(price − SMA_W)/σ_W) and the bars are the FIXED
σ multipliers k ∈ {2, 2.5, 3} in the z space (top bar = +k — the upper
band; bottom bar = −k — the lower band), NOT per-(code, window)
percentile quantiles. A band breach IS |z| >= k and the breach excess
depth is |z| − k, in σ units — the same quantity the curve tier reads
as its x axis (s = |z|).

Per (sec_type, snapshot), entirely on cudf.pandas frames:

  ``rung``   one row per (code, boll_window, std, side, regime_state,
             delay) — delay ALWAYS 0: qualifying days of one EPISODE
             streak-merge into ONE first-day trigger (the shared
             _streak_merge over each code's OWN traded-day ordinals,
             the analysis layer's K = RSI_REENTRY_EXIT_DAYS re-entry
             exit absorbed — the same episode boundary the boll
             analysis layer's flags segment on, so occurrence_count is
             the episode count the curve tier's mu is measured over).
  ``cells``  the buckets' trigger rows (bucket keys + delay(=0) + date +
             excess + regime + the day-0 ohlcv-boost axes) — the
             snapshot-owned history events' source + the value points.

The POPULATION is the family's tail-episode days — every
in-band day of the fixed 0.6745σ band (ep_size_{w}days non-NULL:
the episode's first touch, its re-entries AND the gap-1 continuations,
which the is_typical flags attribute to their episode pass) — the same
episode-population rule as mov_rsi: the deep days the fixed bars select
are exactly the continuations. Membership is monotone in k per side
(the 2.5σ qualifying set is a subset of the 2σ set is a subset of
nothing wider), so the nested per-side tests carve widest-first
(smallest k first) exactly like mov_rsi's nested pcts.

Everything else is inherited machinery: the memory-aware code
partitioning, the frame prep (calendar ordinals, regime labels,
first-date gate, forward changes), the blended mixed-row stats
(_mixed_blend) and the typed empty frames.

COMPUTE LAYOUT: whole-frame vectorized cudf.pandas (melt / boolean
algebra / grouped ops — no per-code loops); the only host boundary is
the inherited (R × n_horizons) mixed-blend leg table.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.labels import SIDE_CODE

from analysis.analysis_signals.config import (
    BOLL_SIDES,
    BOLL_STDS_EMIT,
    BOLL_WINDOWS,
    FORWARD_HORIZONS,
    RSI_REENTRY_EXIT_DAYS,
)
from analysis.analysis_signals.engines.signal_families.mov_rsi._buckets import (
    Buckets,
    BucketContext,
    _finite_mask,
    _rung_frame,
    _streak_merge,
)


# the shared bucket-keys layout (the rung/cells frames' axis columns —
# the family's own identity; `std` is the σ multiplier as float64)
_BOLL_RUNG_DTYPES = {
    "code": "object", "boll_window": "int64", "std": "float64",
    "side": "int16", "regime_state": "int16", "delay": "int64",
    "ave_change": "float64", "occurrence_count": "int64",
    "trig_date": "datetime64[us]", "trig_excess": "float64",
    "is_ohlcv_boosted": "int64",
}
_BOLL_CELLS_DTYPES = {
    "code": "object", "boll_window": "int64", "std": "float64",
    "side": "int16", "regime_state": "int16", "delay": "int64",
    "trig_date": "datetime64[us]", "trig_excess": "float64",
    "_t": "int64",
    "pop_flag": "float64",
    "pattern": "int8", "vol_state": "int8", "px_speed": "int8",
    "boost_score": "float64", "boost_weight": "float64",
    "boost_flag": "int64",
}
_BOLL_RUNG_KEYS = [
    "code", "boll_window", "std", "side", "regime_state", "delay",
]


def _empty_boll_rung() -> pd.DataFrame:
    """The rung frame's typed empty shape (native dtypes so the
    machinery's numeric ops never hit object columns)."""
    return pd.DataFrame({
        k: pd.Series(dtype=v) for k, v in _BOLL_RUNG_DTYPES.items()
    })


def _empty_boll_cells() -> pd.DataFrame:
    """The cells frame's typed empty shape."""
    return pd.DataFrame({
        k: pd.Series(dtype=v) for k, v in _BOLL_CELLS_DTYPES.items()
    })


class BollBucketContext(BucketContext):
    """The boll_std family's bucket context — the mov_rsi BucketContext
    engine over the boll value columns, with the FIXED-σ detection
    substituted for the percentile bars (see the module docstring).
    Inherits the frame prep, forward changes, code partitioning and the
    value-point machinery; overrides the column vocabulary, the
    per-snapshot detection and the value melt."""

    def _columns(self) -> list[str]:
        cols = ["code", "date", "_t", "regime", "first_date", "price",
                "pattern", "vol_state", "px_speed"]
        cols += [f"boll_z_{w}days" for w in self._boll_windows()]
        cols += [f"is_typical_{w}days" for w in self._boll_windows()]
        cols += [f"ep_size_{w}days" for w in self._boll_windows()]
        cols += [f"next_change_{n}d" for n in FORWARD_HORIZONS]
        return cols

    def _boll_windows(self) -> tuple:
        return BOLL_WINDOWS

    def _rsi_windows(self) -> tuple:
        # the inherited _compute_code_chunk routes through _columns()
        # (overridden above); the mov_rsi vocabulary hook is unused.
        return BOLL_WINDOWS

    def _window_cols(self) -> list[str]:
        return self._columns()

    # ---- per-snapshot computation ----------------------------------------

    def buckets(
        self,
        spec,
        *,
        boll_windows: tuple = BOLL_WINDOWS,
        stds: tuple = BOLL_STDS_EMIT,
        sides: tuple = BOLL_SIDES,
    ) -> Buckets:
        """One snapshot's ``rung`` + ``cells`` frames (see the module
        docstring): per (snapshot × code-chunk) partitions over the
        fixed-σ band-breach tests, concatenated into the snapshot's
        frames. Returns the mov_rsi-shaped Buckets namedtuple (rung,
        cells) with the family's axis columns."""
        df = self._df
        cols = self._window_cols()
        lower = np.datetime64(spec.lower)
        upper = np.datetime64(spec.upper)
        rung_parts: list[pd.DataFrame] = []
        cells_parts: list[pd.DataFrame] = []
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
            cells = _detect_boll_cells(win, boll_windows, stds, sides)
            if cells.empty:
                continue
            cells = cells.rename(
                columns={"date": "trig_date", "excess": "trig_excess"},
            )
            rung_parts.append(_rung_frame(cells, keys=_BOLL_RUNG_KEYS))
            cells_parts.append(cells)
        if not rung_parts:
            return Buckets(
                rung=_empty_boll_rung(), cells=_empty_boll_cells(),
            )
        return Buckets(
            rung=pd.concat(rung_parts, ignore_index=True),
            cells=pd.concat(cells_parts, ignore_index=True),
        )

    # ---- value points ----------------------------------------------------

    def values_long(
        self, codes: list[str], dates: list,
    ) -> pd.DataFrame:
        """The melted (code, date, window, value) σ-depth frame at
        exactly the given (code, date) points — the values the strategy
        bar recovery and the history rows read (the same
        (code, date, window, value) contract the generic build phases
        merge on)."""
        if not codes or not dates:
            return pd.DataFrame(
                columns=["code", "date", "window", "value"],
            )
        codes_df = pd.DataFrame({"code": sorted(codes)}).assign(_k=1)
        dates_df = pd.DataFrame({
            "date": pd.to_datetime([d.isoformat() for d in dates]),
        }).assign(_k=1)
        pts = codes_df.merge(dates_df, on="_k").drop(columns="_k")
        rows = pts.merge(
            self._df[["code", "date"] + [
                f"boll_z_{w}days" for w in BOLL_WINDOWS
            ]],
            on=["code", "date"], how="inner",
        )
        parts = [
            rows[["code", "date"]].assign(window=w, value=rows[col])
            for w, col in (
                (w, f"boll_z_{w}days") for w in BOLL_WINDOWS
            )
        ]
        long = pd.concat(parts, ignore_index=True)
        return long.dropna(subset=["value"])


def _detect_boll_cells(
    win: pd.DataFrame,
    boll_windows: tuple,
    stds: tuple,
    sides: tuple,
) -> pd.DataFrame:
    """One window slice's qualifying anchor rows: the episode-population
    days whose z breaches the side's FIXED ±kσ bar, streak-merged into
    delay-0 episode-first triggers. Output columns: code / date / _t /
    _tc / regime_state / boll_window / std / side / excess /
    next_change_{n}d / delay / pop_flag (+ the boost axes). Anchors
    carry only rows whose next-day forward change is valid (the shared
    trigger-array semantics)."""
    id_vars = ["code", "date", "_t", "regime_state",
               "pattern", "vol_state", "px_speed"]
    fwd = [f"next_change_{n}d" for n in FORWARD_HORIZONS]
    size_cols = [f"ep_size_{w}days" for w in boll_windows]
    flag_cols = [f"is_typical_{w}days" for w in boll_windows]
    win = win.rename(columns={"regime": "regime_state"})
    # the streak-merge continuity axis: each code's OWN traded-day
    # ordinal within the window (the 2026-10-07 tier alignment — the
    # union calendar's other-code holiday dates must not split runs)
    _ord = (
        win[["code", "date"]]
        .drop_duplicates()
        .sort_values(["code", "date"])
        .reset_index(drop=True)
    )
    _ord["_tc"] = _ord.groupby("code", sort=False).cumcount()
    _ord["_tc"] = _ord["_tc"].astype("int64")
    win = win.merge(_ord, on=["code", "date"], how="left")
    id_vars.insert(3, "_tc")
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
        return long.drop(columns=["_wcol"])

    # the day's episode-PASS attribution (the boll analysis layer's
    # side-mirrored is_typical flag at the day's own window): 1.0 the
    # typical pass, 0.0 the re-entry pass — the history rows'
    # is_typical attribute rides it
    _flg = pd.Series(np.nan, index=long.index, dtype="float64")
    for w in boll_windows:
        _flg = _flg.mask(
            long["boll_window"] == w,
            long[f"is_typical_{w}days"],
        )
    long = long.assign(pop_flag=_flg).drop(columns=flag_cols)

    # the POPULATION (the boll analysis layer's tail episodes at the
    # fixed 0.6745σ band): EVERY in-band day of the day's own window —
    # the episode's first day, its re-entries AND the gap-1
    # continuations — marked by the EPISODE-SIZE column (non-NULL
    # in-band, NULL out-of-band). The deep days the fixed bars select
    # are exactly the continuations (a code breaches the band shallow
    # and burrows deep over the following days).
    _eps = pd.Series(np.nan, index=long.index, dtype="float64")
    for w in boll_windows:
        _eps = _eps.mask(
            long["boll_window"] == w,
            long[f"ep_size_{w}days"],
        )
    long = long[_eps.notna()].drop(columns=size_cols)
    if long.empty:
        return long.drop(columns=["_wcol"])

    # bucket tests, nested widest-qualifying-set-first per side
    # (membership is monotone in k: the deeper bar's qualifying set is
    # a subset of the shallower one's — each level carved from the
    # previous's survivors; the bar is the FIXED ±k constant, no
    # quantile merge)
    keep = id_vars + [
        "boll_window", "side", "std", "excess", "pop_flag",
    ] + fwd
    cells_parts: list[pd.DataFrame] = []
    for side in sides:
        sc = np.int16(SIDE_CODE[side])
        cur = long
        for k in sorted(stds):
            bar = float(k) if side == "top" else -float(k)
            cur = cur[
                (cur["value"] >= k) if side == "top"
                else (cur["value"] <= -k)
            ]
            hit = cur.assign(side=sc, std=float(k))
            hit["excess"] = hit["value"] - bar
            cells_parts.append(hit[keep])
    cells = pd.concat(cells_parts, ignore_index=True)
    if cells.empty:
        return cells
    # anchors = rows with a valid NEXT-day forward change (the shared
    # trigger-array population)
    cells = cells[_finite_mask(cells["next_change_1d"])]
    if cells.empty:
        return cells
    return _streak_merge(
        cells,
        group_cols=["boll_window", "side", "std", "regime_state", "code"],
        reentry_exit_days=RSI_REENTRY_EXIT_DAYS,
        t_col="_tc",
    )


__all__ = [
    "BollBucketContext", "_detect_boll_cells", "_empty_boll_cells",
]
