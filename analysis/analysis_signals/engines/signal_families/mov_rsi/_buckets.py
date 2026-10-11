"""In-engine RSI percentile (rsi pct) bucket computation
(analysis.analysis_signals.engines.signal_families.mov_rsi._buckets).

THE signals tier computes its OWN buckets off the raw data since the
forecast layer was removed (2026-10-05) — no analysis_forecasts tables
anymore: per (sec_type, snapshot) this module derives, entirely on
cudf.pandas frames, exactly the inputs the gate machinery consumes:

  ``rung``   one row per (code, rsi_window, pct, side, regime_state,
             delay) — delay is ALWAYS 0 since the delay ladder's
             removal (2026-10-05): each qualifying streak contributes
             its FIRST day as the trigger. The row carries the
             weight-blended mean forward change (ave_change, the
             FIXED 5d 0.65 / next 0.25 / 20d 0.10 mix over the
             triggers' valid anchors, renormalized over the horizons
             with stats), the blended occurrence count (the MIN
             positive leg count = the number of the window's
             qualifying streaks) and the bucket's WINDOW-END trigger
             (trig_date / trig_excess — the last trigger by date;
             the bar-recovery anchor the strategy row reads).
  ``cells``  the buckets' trigger rows (bucket keys + delay(=0) +
             date + excess + regime + the day-0 ohlcv-boost axes) —
             the snapshot-owned history events' source
             (snapshot_triggers filters them to the snapshot's
             calendar year) and the value points. The engine scores
             the boost axes (engines/ohlcv_boost) onto the returned
             frames: cells gain boost_score / boost_flag per anchor,
             the rung gains is_ohlcv_boosted per bucket.

The detection semantics are the retired forecast engine's, with TWO
changes since the 2026-10-05 port:

  - the ANCHOR POPULATION is the rsi analysis layer's tail-episode
    days — EVERY in-band day of the day's own window
    (typical_ep_size_{W}days non-NULL: the episode's first touch, its
    re-entries AND the gap-1 continuations, which the is_typical
    flags attribute to their episode pass). The deep days the
    percentile bars select are exactly the continuations (a code
    enters its tail band shallow and burrows deep over the following
    days), so a first-touch-only population sits at the band edge by
    construction and left the tier untriggerable — the same defect
    the curve tier's 2026-10-06 episode population fixed; the
    threshold tier follows 2026-10-07 (the streak-merge below
    collapses each qualifying EPISODE into ONE first-day trigger —
    the analysis layer's K=5 re-entry exit absorbed on each code's
    OWN traded-day sequence — so the trigger count stays the episode
    count the curve tier's mu is measured over);
  - the per-side bars are the RAW per-side tail quantiles
    (_tail_quantile_bars: bottom = the window's q(p/100), top =
    q(1−p/100) — the classic percentile construction the sub_type's
    {pct}pct name and the live tier's stored-percentile-threshold
    rule promise). (The 2026-10-05 symmetric-in-space interlude —
    median ± half the two-tail band — was RETIRED 2026-10-07: on a
    bounded, high-variance indicator like RSI it lands one or both
    bars outside the distribution's reach (an RSI(3) top bar past
    100), collapsing bucket occurrence below the sample floors — the
    funnel study, temp_scripts/study_bar_decomp.py. Side fairness
    lives at the gate layer now — the per-side gate bars + sample
    floors.)

Otherwise verbatim: per (code, rsi_window, pct) the
linearly-interpolated quantiles over the snapshot window's non-NULL
values (pos = q·(valid_n−1)); nested per-side tests widest tail first
(membership is monotone in pct per side); qualifying runs merge into
ONE signal triggered at its FIRST qualifying day (delay 0 — the 0..5
delay ladder went with the forecast-era entry-delay logic 2026-10-05;
the 2026-10-07 tier alignment runs the merge on each code's OWN
traded-day ordinals with the analysis layer's K=5 re-entry exit
absorbed — the frame-wide union calendar carried other codes'
holiday dates, split runs at every closure the code itself did not
trade and manufactured phantom re-opening triggers); buckets split by the
trigger days' market-regime label (stats.market_regimes; no 'all'
span — signal_thresholds stays 4-state); triggers are the run-start
days whose NEXT-day forward change is valid (the retired fetch's
period='next' trigger-array semantics).

All ops are whole-frame vectorized cudf.pandas (melt / merge /
groupby / boolean algebra — no per-code loops); the only host
boundary is the (R × n_horizons) mixed-blend leg table (host_array
unwrap).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from _common.df_utils import host_array
from _common.labels import SIDE_CODE

from analysis.analysis_signals.config import (
    FORWARD_HORIZONS,
    MIXED_HORIZON_WEIGHTS,
    RSI_REENTRY_EXIT_DAYS,
    RSI_SIDES,
)

# ---- Memory-aware partition budgets (the retired forecast engine's) ---------
# One (snapshot × code-chunk) partition's working-set budget in float64
# CELLS of the window slice; the melted long frames hold a small
# multiple of the slice. GPU budgets tighter (device also holds the
# full prepared universe).
GPU_PARTITION_CELL_BUDGET = 40_000_000
CPU_PARTITION_CELL_BUDGET = 80_000_000
MIN_CODE_CHUNK = 128
MAX_CODE_CHUNK = 2048

_WEIGHTS = np.array([MIXED_HORIZON_WEIGHTS[n] for n in FORWARD_HORIZONS])


def _finite_mask(s: pd.Series) -> pd.Series:
    """True where the float series holds a finite value (cudf has no
    np.isfinite fast path; NaN fails the self-equality, NULLs fill
    False)."""
    return (
        (s == s) & (s != float("inf")) & (s != float("-inf"))
    ).fillna(False)


@dataclass
class Buckets:
    """One snapshot's computed bucket frames (see module docstring)."""
    rung: pd.DataFrame
    cells: pd.DataFrame


class BucketContext:
    """The per-sec_type prepared frame + the per-snapshot bucket
    computation. Built once per run (``prepare``) and consumed per
    target snapshot (``buckets``) / per value-point set
    (``values_long``)."""

    def __init__(
        self,
        df: pd.DataFrame,
        specs: list,
        sec_type: str,
    ) -> None:
        self._df = df              # the prepared long frame (sorted code/date)
        self.specs = specs
        self.sec_type = sec_type
        self._code_chunk = self._compute_code_chunk()
        # PRE-PARTITION: the sorted universe's code-partition id,
        # merged once — the per-snapshot slices are two vectorized
        # compares, no per-chunk isin hash (the retired engine's
        # _snapshot_chunks shape).
        part = self._df[["code"]].drop_duplicates().sort_values("code")
        part["_pc"] = (
            np.arange(len(part), dtype="int64") // self._code_chunk
        )
        self._df = self._df.merge(part, on="code", how="left")

    # ---- preparation ---------------------------------------------------

    @staticmethod
    def prepare_frame(
        df: pd.DataFrame,
        first_dates: dict[str, date],
        regimes: pd.DataFrame,
    ) -> pd.DataFrame:
        """The one-time frame prep: the union trading-day calendar
        ordinal (_t — the streak continuity axis), the market-regime
        day codes (int16, 0 = calm default), the per-code first-data
        date (the live gate) and the forward changes."""
        cal = (
            df[["date"]].drop_duplicates().sort_values("date")
            .reset_index(drop=True)
        )
        cal["_t"] = cal.index.astype("int64")
        out = df.merge(cal, on="date", how="left")
        if not regimes.empty:
            out = out.merge(
                regimes[["code", "date", "regime"]],
                on=["code", "date"], how="left",
            )
            out["regime"] = out["regime"].fillna(0).astype("int16")
        else:
            out["regime"] = np.int16(0)
        first = pd.DataFrame({
            "code": list(first_dates.keys()),
            "first_date": pd.to_datetime(list(first_dates.values())),
        })
        out = out.merge(first, on="code", how="left")
        out = out.sort_values(["code", "date"]).reset_index(drop=True)
        return out

    @staticmethod
    def add_forward_changes(df: pd.DataFrame) -> pd.DataFrame:
        """next_change_{n}d = (price[t+n] - price[t]) / price[t], per
        code on its OWN trading-day sequence (grouped_shift —
        cuDF-accelerated; calendar gaps are not rows). NULL when the
        forward price is missing, the base price is ~0, or the ratio
        is non-finite."""
        from _common.df_utils import grouped_shift

        for n in FORWARD_HORIZONS:
            col = f"next_close_{n}"
            grouped_shift(df, ["code"], "price", out_names=col,
                          periods=-n, sort=False)
            prev = df["price"]
            nxt = df[col]
            out = (nxt - prev) / prev
            mask = nxt.isna() | (prev.fillna(0.0).abs() < 1e-12) \
                | ~np.isfinite(out)
            df[f"next_change_{n}d"] = out.where(~mask)
        drop = [f"next_close_{n}" for n in FORWARD_HORIZONS]
        return df.drop(columns=drop)

    # ---- partitioning ---------------------------------------------------

    def _compute_code_chunk(self) -> int:
        """The memory-aware codes-per-partition size: the frame's own
        mean rows-per-code and window width against the GPU/CPU cell
        budget (the retired forecast engine's formula)."""
        from _common.df_utils._detector import is_gpu_available

        n_codes = max(1, self._df["code"].nunique())
        rows_per_code = max(1.0, len(self._df) / n_codes)
        width = max(1, len(self._columns()))
        gpu = is_gpu_available()
        budget = (GPU_PARTITION_CELL_BUDGET if gpu
                  else CPU_PARTITION_CELL_BUDGET)
        chunk = int(budget / max(1.0, rows_per_code * width))
        chunk = max(MIN_CODE_CHUNK, min(MAX_CODE_CHUNK, chunk))
        return chunk

    def _columns(self) -> list[str]:
        cols = ["code", "date", "_t", "regime", "first_date", "price",
                "pattern", "vol_state", "px_speed"]
        cols += [f"rsi_{w}days" for w in self._rsi_windows()]
        # the rsi analysis layer's day-level tail-episode flags (1.0
        # first-touch pass / 0.0 re-entry pass / NaN out-of-band — the
        # gap-1 continuation days inherit their pass's flag) + the
        # EPISODE SIZES (every in-band day — start, re-entry AND the
        # gap-1 continuations — carries its episode's trading-day
        # count): BOTH signal tiers' population rides the sizes
        # (non-NULL = in-band; the deep days the bars select are the
        # gap-1 continuations) — the flags stay the analysis layer's
        # pass markers (the curve tier's 1/size episode weights read
        # the sizes too)
        cols += [f"is_typical_{w}days" for w in self._rsi_windows()]
        cols += [f"ep_size_{w}days" for w in self._rsi_windows()]
        cols += [f"next_change_{n}d" for n in FORWARD_HORIZONS]
        return cols

    def _rsi_windows(self) -> tuple:
        from analysis.analysis_signals.config import RSI_WINDOWS
        return RSI_WINDOWS

    def _window_cols(self) -> list[str]:
        return self._columns()

    # ---- per-snapshot computation ----------------------------------------

    def buckets(
        self,
        spec,
        *,
        rsi_windows: tuple,
        pcts: tuple,
        sides: tuple = RSI_SIDES,
    ) -> Buckets:
        """One snapshot's ``rung`` + ``cells`` frames (see module
        docstring). The (snapshot × code-chunk) partitions bound every
        frame's device footprint; the chunks concatenate into the
        snapshot's frames."""
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
            cells = _detect_cells(win, rsi_windows, pcts, sides)
            if cells.empty:
                continue
            cells = cells.rename(columns={"date": "trig_date", "excess": "trig_excess"})
            rung_parts.append(_rung_frame(cells))
            cells_parts.append(cells)
        if not rung_parts:
            return Buckets(rung=_empty_rung(), cells=_empty_cells())
        return Buckets(
            rung=pd.concat(rung_parts, ignore_index=True),
            cells=pd.concat(cells_parts, ignore_index=True),
        )

    # ---- value points ----------------------------------------------------

    def values_long(
        self, codes: list[str], dates: list[date],
    ) -> pd.DataFrame:
        """The melted (code, date, window, value) RSI frame at exactly
        the given (code, date) points — the values the strategy bar
        recovery and the history rows read. Bounded point read off the
        prepared frame (never the whole history)."""
        from analysis.analysis_signals.config import RSI_WINDOWS

        if not codes or not dates:
            return pd.DataFrame(columns=["code", "date", "window", "value"])
        codes_df = pd.DataFrame({"code": sorted(codes)}).assign(_k=1)
        dates_df = pd.DataFrame({
            "date": pd.to_datetime([d.isoformat() for d in dates]),
        }).assign(_k=1)
        pts = codes_df.merge(dates_df, on="_k").drop(columns="_k")
        rows = pts.merge(
            self._df[["code", "date"] + [
                f"rsi_{w}days" for w in RSI_WINDOWS
            ]],
            on=["code", "date"], how="inner",
        )
        parts = [
            rows[["code", "date"]].assign(window=w, value=rows[col])
            for w, col in ((w, f"rsi_{w}days") for w in RSI_WINDOWS)
        ]
        long = pd.concat(parts, ignore_index=True)
        return long.dropna(subset=["value"])


# ---------------------------------------------------------------------------
#  Detection (the retired forecast PercentileEngine's semantics)
# ---------------------------------------------------------------------------

_RUNG_DTYPES = {
    "code": "object", "rsi_window": "int64", "pct": "int64",
    "side": "int16", "regime_state": "int16", "delay": "int64",
    "ave_change": "float64", "occurrence_count": "int64",
    "trig_date": "datetime64[us]", "trig_excess": "float64",
    # the engine's ohlcv-boost bucket verdict (engines/ohlcv_boost —
    # stamped after _rung_frame; 0 on the empty shape)
    "is_ohlcv_boosted": "int64",
}
_CELLS_DTYPES = {
    "code": "object", "rsi_window": "int64", "pct": "int64",
    "side": "int16", "regime_state": "int16", "delay": "int64",
    "trig_date": "datetime64[us]", "trig_excess": "float64",
    "_t": "int64",
    # the day's episode-PASS attribution (1.0 typical / 0.0 re-entry —
    # the history rows' is_typical attribute rides it)
    "pop_flag": "float64",
    # the day-0 boost axes (int8 codes, AXIS_MISSING = -1) + the
    # engine's per-anchor boost score/weight/flag (stamped after
    # detection — the weight feeds the history tier's boost twins)
    "pattern": "int8", "vol_state": "int8", "px_speed": "int8",
    "boost_score": "float64", "boost_weight": "float64",
    "boost_flag": "int64",
}


def _empty_rung() -> pd.DataFrame:
    """The rung frame's typed empty shape (a snapshot whose windows
    hold no qualifying anchors) — native dtypes so the machinery's
    numeric ops never hit object columns."""
    return pd.DataFrame({
        k: pd.Series(dtype=v) for k, v in _RUNG_DTYPES.items()
    })


def _empty_cells() -> pd.DataFrame:
    """The cells frame's typed empty shape."""
    return pd.DataFrame({
        k: pd.Series(dtype=v) for k, v in _CELLS_DTYPES.items()
    })

def _detect_cells(
    win: pd.DataFrame,
    rsi_windows: tuple,
    pcts: tuple,
    sides: tuple,
) -> pd.DataFrame:
    """One window slice's qualifying anchor rows: per-side percentile
    bars per (rsi_window, code, pct) over the window's non-NULL
    values (see _tail_quantile_bars) → nested per-side tail tests
    (widest
    first) → streak-merge (delay-0 run-start triggers over each
    code's OWN traded-day ordinals, the analysis layer's K=5
    re-entry exit absorbed). Output
    columns: code / date / _t / _tc / regime / rsi_window / pct / side /
    excess / next_change_{n}d / delay. Anchors carry only rows whose
    next-day forward change is valid (the retired period='next'
    trigger-array semantics)."""
    id_vars = ["code", "date", "_t", "regime_state",
               "pattern", "vol_state", "px_speed"]
    value_cols = [f"rsi_{w}days" for w in rsi_windows]
    size_cols = [f"ep_size_{w}days" for w in rsi_windows]
    flag_cols = [f"is_typical_{w}days" for w in rsi_windows]
    win = win.rename(columns={"regime": "regime_state"})
    # the streak-merge CONTINUITY AXIS: each code's OWN traded-day
    # ordinal within the window (consecutive rows of one code are
    # consecutive traded days of that code — the frame-wide union
    # calendar `_t` also carries the OTHER codes' dates, so a code's
    # run split at every market closure it did not trade and the
    # re-opening day triggered as a fresh signal; the episode layer
    # has always run on each code's own series). Merged before the
    # melt so every melted row carries its (code, date)'s ordinal.
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
        id_vars=id_vars + [f"next_change_{n}d" for n in FORWARD_HORIZONS]
        + size_cols + flag_cols,
        value_vars=value_cols,
        var_name="_wcol", value_name="value",
    )
    long["rsi_window"] = long["_wcol"].map(
        {f"rsi_{w}days": w for w in rsi_windows}
    )
    long = long[_finite_mask(long["value"])]
    if long.empty:
        return long.drop(columns=["_wcol"])

    # the day's episode-PASS attribution (the analysis layer's
    # side-mirrored is_typical flag at the day's own window): 1.0 =
    # the typical pass (buy: the isolated first touch; sell: the
    # re-test), 0.0 = the re-entry pass (flicker re-crossings within
    # the K=5 exit window / continuations / isolated breakouts — the
    # noisy population). One episode's days share its pass, so the
    # run-start trigger carries its episode's pass — the history
    # rows' is_typical attribute (the UI history pass toggle).
    _flg = pd.Series(np.nan, index=long.index, dtype="float64")
    for w in rsi_windows:
        _flg = _flg.mask(
            long["rsi_window"] == w,
            long[f"is_typical_{w}days"],
        )
    long = long.assign(pop_flag=_flg).drop(columns=flag_cols)

    # the per-config bars: the side's OWN tail quantile (bottom =
    # the window's q(p/100), top = q(1−p/100) — see
    # _tail_quantile_bars), all windows × pcts in
    # ONE pass, over ALL the window's days (the
    # bars are distribution quantiles, not triggers).
    bars = _tail_quantile_bars(
        long[["rsi_window", "code", "value"]],
        group_cols=["rsi_window", "code"],
        pcts=pcts, sides=sides,
    )
    if bars.empty:
        return long.drop(columns=["_wcol"])

    # the POPULATION (the rsi analysis layer's tail episodes): EVERY
    # in-band day of the day's own window — the episode's first day
    # (is_typical TRUE), its re-entries (FALSE) AND the gap-1
    # continuations (which the flags attribute to their episode pass)
    # — marked by the EPISODE-SIZE column (non-NULL on every in-band
    # day, NULL out-of-band). The deep days the percentile bars select
    # are exactly the continuations (a code enters its tail band at a
    # SHALLOW value and burrows deep over the following days), so a
    # first-touch-only population sits at the band edge by
    # construction and left the tier untriggerable — the same defect
    # the curve tier's 2026-10-06 episode population fixed; the
    # threshold tier follows 2026-10-07 (consecutive qualifying days
    # still collapse into ONE first-day trigger below, so the trigger
    # count stays the episode count). The day's own window's size
    # column wins (each melted row carries all windows').
    _eps = pd.Series(np.nan, index=long.index, dtype="float64")
    for w in rsi_windows:
        _eps = _eps.mask(
            long["rsi_window"] == w,
            long[f"ep_size_{w}days"],
        )
    long = long[_eps.notna()].drop(columns=size_cols)
    if long.empty:
        return long.drop(columns=["_wcol"])

    # bucket tests, nested largest-qualifying-set-first per side
    # (membership is monotone in pct: the smaller pct's bars sit
    # farther from the median, so its qualifying set is a subset of
    # the larger pct's on both sides — each narrower set is carved
    # from the previous level's survivors).
    fwd = [f"next_change_{n}d" for n in FORWARD_HORIZONS]
    keep = id_vars + [
        "rsi_window", "side", "pct", "excess", "pop_flag",
    ] + fwd
    cells_parts: list[pd.DataFrame] = []
    for side in sides:
        sc = np.int16(SIDE_CODE[side])
        sb = bars[bars["side"] == sc][["rsi_window", "code", "pct", "bar"]]
        cur = long
        for p in sorted(pcts, reverse=True):
            cur = cur.merge(
                sb[sb["pct"] == p][["rsi_window", "code", "bar"]]
                .rename(columns={"bar": "_bar"}),
                on=["rsi_window", "code"], how="inner",
            )
            cur = cur[
                (cur["value"] >= cur["_bar"]) if side == "top"
                else (cur["value"] <= cur["_bar"])
            ]
            hit = cur.assign(side=np.int16(SIDE_CODE[side]), pct=p)
            hit["excess"] = hit["value"] - hit["_bar"]
            cells_parts.append(hit[keep])
            cur = cur.drop(columns=["_bar"])
    cells = pd.concat(cells_parts, ignore_index=True)
    if cells.empty:
        return cells
    # anchors = rows with a valid NEXT-day forward change (the retired
    # fetch's period='next' trigger-array population)
    cells = cells[_finite_mask(cells["next_change_1d"])]
    if cells.empty:
        return cells
    return _streak_merge(
        cells,
        group_cols=["rsi_window", "side", "pct", "regime_state", "code"],
        reentry_exit_days=RSI_REENTRY_EXIT_DAYS,
        t_col="_tc",
    )


def _quantile_bars(
    values: pd.DataFrame,
    group_cols: list[str],
    q_spec: pd.DataFrame,
) -> pd.DataFrame:
    """Per-(group, q) linearly-interpolated quantile bars (pos =
    q·(valid_n−1)): one sort + cumcount rank, one (group × q) position
    table, two keyed gathers."""
    v = values.sort_values(group_cols + ["value"]).reset_index(drop=True)
    g = v.groupby(group_cols, sort=False)
    v["_r"] = g.cumcount()
    ranks = v[group_cols + ["_r", "value"]]
    n_tbl = g.size().rename("_n").reset_index()
    pos = q_spec.merge(n_tbl, on=group_cols, how="inner")
    n = pos["_n"]
    p = pos["q"] * (n - 1)
    pos["_i0"] = np.floor(p).astype("int64")
    pos["_i1"] = np.minimum(pos["_i0"] + 1, n - 1).astype("int64")
    pos["_frac"] = p - pos["_i0"]
    b = pos.merge(
        ranks, left_on=group_cols + ["_i0"],
        right_on=group_cols + ["_r"], how="left",
    ).rename(columns={"value": "_v0"}).drop(columns=["_r"])
    b = b.merge(
        ranks, left_on=group_cols + ["_i1"],
        right_on=group_cols + ["_r"], how="left",
    ).rename(columns={"value": "_v1"}).drop(columns=["_r"])
    b["bar"] = b["_v0"] + b["_frac"] * (b["_v1"] - b["_v0"])
    return b.drop(columns=["_i0", "_i1", "_frac", "_n", "_v0", "_v1"])


def _tail_quantile_bars(
    values: pd.DataFrame,
    group_cols: list[str],
    pcts: tuple,
    sides: tuple,
) -> pd.DataFrame:
    """Per-(group, pct, side) bars at the side's OWN tail quantile —
    the classic percentile construction (bottom bar = the window's
    q(p/100), top bar = q(1−p/100); the 2026-10-07 restoration of the
    retired forecast engine's semantics — see the module docstring's
    symmetric-interlude block for why the 2026-10-05 median-centered
    band was retired). The sub_type's {pct}pct name names the tail
    MASS each bar isolates, membership stays monotone in pct per side
    (the pct-1 set is a subset of the pct-2 set is a subset of the
    pct-3 set), so the nested widest-qualifying-set-first carving
    (largest pct first) stays valid. Output columns:
    group_cols + pct + side + bar, side as its int16 label code.
    Built from the TWO quantile legs (bottom/top) per pct in ONE
    _quantile_bars pass."""
    core = group_cols + ["pct"]
    groups = values[group_cols].drop_duplicates().assign(_k=1)
    legs = pd.DataFrame({
        "pct": [p for p in pcts for _ in sides],
        "leg": [i for _ in pcts for i in range(len(sides))],
        "q": [
            p / 100.0 if s == "bottom" else 1.0 - p / 100.0
            for p in pcts for s in sides
        ],
    }).assign(_k=1)
    bars = _quantile_bars(
        values, group_cols,
        q_spec=groups.merge(legs, on="_k").drop(columns=["_k"]),
    )
    parts: list[pd.DataFrame] = []
    for i, s in enumerate(sides):
        parts.append(bars[bars["leg"] == i][core + ["bar"]].assign(
            side=np.int16(SIDE_CODE[s]),
        ))
    out = pd.concat(parts, ignore_index=True)
    return out[out["bar"].notna()]


def _streak_merge(
    cells: pd.DataFrame,
    group_cols: list[str],
    reentry_exit_days: int = 0,
    t_col: str = "_t",
) -> pd.DataFrame:
    """Gaps-and-islands over the continuity axis ``t_col``: qualifying
    days of each (group, code) whose axis gap is <= 1 +
    ``reentry_exit_days`` collapse into ONE signal whose trigger is
    the run's FIRST qualifying day (delay 0 — the moment the signal
    becomes observable; the 0..5 delay ladder was REMOVED 2026-10-05 —
    a strategy IS its delay-0 profile). The threshold tier passes the
    rsi analysis layer's K = RSI_REENTRY_EXIT_DAYS on the per-code
    traded-day ordinals ``_tc`` (the 2026-10-07 tier alignment: the
    run boundary is the EPISODE boundary, so occurrence_count is the
    episode count the curve tier's mu is measured over, and the
    frame-wide union calendar's other-code holiday dates no longer
    split runs).

    ``reentry_exit_days`` DEBOUNCES THE EXIT: a run does not end until
    the day has spent > reentry_exit_days consecutive trading days out
    of the tail (gap between consecutive tail days > 1 +
    reentry_exit_days). A re-entry within that window is the SAME
    overbought episode continuing — it is absorbed into the prior run
    (invalidated, never a fresh trigger). 0 = today's historical
    behaviour (any single day out breaks the run). The K now lives at
    the rsi analysis layer, steps/rsi_typical.py
    RSI_REENTRY_EXIT_DAYS: the 2026-10-06 study
    (temp_scripts/study_reentry_gap.py) found the top-1% tail band's
    ladder dominated by re-entries — 18.1% of inter-day gaps were
    4-trading-day market-holiday closures and short flickers, and
    absorbing exits <= 5 days inverts the band's mean from +1.47% to
    -1.39% (the deep-tail reversion claim was a re-entry artifact)."""
    c = cells.sort_values(group_cols + [t_col])
    prev = c.groupby(group_cols, sort=False)[t_col].shift(1)
    new_run = (
        prev.isna()
        | ((c[t_col] - prev) > reentry_exit_days + 1)
    ).fillna(True)
    c["_run"] = new_run.cumsum()
    c["_t0"] = c.groupby("_run", sort=False)[t_col].transform("min")
    c["delay"] = c[t_col] - c["_t0"]
    c = c[c["delay"] == 0]
    return c.drop(columns=["_run", "_t0"])


def _rung_frame(
    cells: pd.DataFrame, keys: list[str] | None = None,
) -> pd.DataFrame:
    """The per-bucket mixed-row stats (delay is always 0): the blended mean
    forward change over the rung's anchors (the FIXED per-horizon
    weights renormalized over the horizons with stats), the blended
    occurrence count (MIN positive leg count) and the rung's
    WINDOW-END trigger (the last anchor by date). ``keys`` overrides the
    default mov_rsi bucket keys (the shared machinery the boll_std
    family reuses with its own axis columns)."""
    if keys is None:
        keys = ["code", "rsi_window", "pct", "side", "regime_state",
                "delay"]
    c = cells.sort_values(keys + ["trig_date"])
    # masked per-horizon helpers: _v{n} carries the change only on the
    # valid anchors (NaN elsewhere — the group sum skips them, so the
    # sums are the valid-day sums), _fin{n} the validity as 0/1.
    agg: dict = {}
    for n in FORWARD_HORIZONS:
        col = f"next_change_{n}d"
        fin = _finite_mask(c[col])
        c[f"_fin{n}"] = fin
        c[f"_v{n}"] = c[col].where(fin)
        agg[f"s_{n}"] = (f"_v{n}", "sum")
        agg[f"cnt_{n}"] = (f"_fin{n}", "sum")
    g = c.groupby(keys, sort=False).agg(**agg).reset_index()
    # the window-end anchor (last by date) — trig_date / trig_excess
    last = c.drop_duplicates(subset=keys, keep="last")[
        keys + ["trig_date", "trig_excess"]
    ]
    g = g.merge(last, on=keys, how="inner")
    g["ave_change"], g["occurrence_count"] = _mixed_blend(g)
    drop = (
        [f"s_{n}" for n in FORWARD_HORIZONS]
        + [f"cnt_{n}" for n in FORWARD_HORIZONS]
    )
    return g.drop(columns=drop)


def _mixed_blend(g: pd.DataFrame) -> tuple:
    """The FIXED-weight blended mixed stats over the per-horizon means
    — one (R × len(FORWARD_HORIZONS)) host leg-table pass (host_array
    unwrap; plain numpy broadcasts on the last axis only). ave
    renormalized over the horizons with stats; occurrence = the MIN
    positive leg count (0 when no leg has stats)."""
    ave = np.stack(
        [np.asarray(
            host_array(
                (g[f"s_{n}"] / g[f"cnt_{n}"].replace(0, np.nan))
                .to_numpy(dtype="float64", na_value=np.nan)
            ), dtype="float64",
        ) for n in FORWARD_HORIZONS],
        axis=1,
    ).round(6)
    cnt = np.stack(
        [np.asarray(
            host_array(g[f"cnt_{n}"].to_numpy(dtype="float64")),
            dtype="float64",
        ) for n in FORWARD_HORIZONS],
        axis=1,
    )
    valid = ave == ave                      # NaN = leg without stats
    w_stat = (valid * _WEIGHTS).sum(axis=1)
    safe = np.where(w_stat > 0, w_stat, np.nan)
    ave_m = (np.where(valid, ave, 0.0) * _WEIGHTS).sum(axis=1) / safe
    pos = cnt > 0
    occ_m = np.where(
        pos.any(axis=1),
        np.where(pos, cnt, np.iinfo(np.int64).max).min(axis=1),
        0,
    )
    idx = g.index
    return (
        pd.Series(ave_m, index=idx),
        pd.Series(occ_m, index=idx).astype("int64"),
    )
