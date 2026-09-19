"""cudf-native wide engine base (analyze.analysis_forecasts._dfengine).

``WideDfEngine`` — the DataFrame engine behind the forecast bucket
families (compute_rsi / compute_base today; one metric one file). The
numpy wide machinery in ``wide/`` + ``_engine.py`` remains the
exactness reference; the FORECAST path runs entirely on cudf.pandas
DataFrames:

  - the (code chunk × stat month) PARTITION lives here — the base owns
    the month loop and the chunking that bounds every frame's device
    footprint; subclasses never loop,
  - ``month_rows(win, spec)`` — the default implementation runs the
    bucket pipeline over ``emit_signals(win)``; bucket-free families
    (base_rates) override it directly,
  - ``emit_signals(win)`` — THE subclass hook: consumes one month
    window's long frame and yields long-format TRIGGER-CELL frames
    (one row per qualifying signal: code / date / _t / is_hyped / side
    / excess / run_len / streak spans + the family's BUCKET_COLS).
    Metric files hold ONLY their own detection logic (compute_rsi:
    melt + quantile bars + top/bottom tests),
  - streak-merge (gaps-and-islands over the union trading-day
    calendar), forward-change aggregation, per-horizon reversal bars,
    the weight-blended mixed row and the row emission are shared and
    vectorized: groupby / merge / boolean algebra — no numpy tensor
    stack, no per-code / per-config Python loops. The only scalar
    materializations are the asyncpg COPY boundary (the final row
    dicts) and the per-bucket ragged date/excess lists the DB array
    columns require (host-numpy slicing — DB arrays are Python objects
    by nature).

Semantics are the historic numpy pipeline's: consecutive qualifying
UNION-CALENDAR days merge into ONE mid-anchored signal (a suspended
day's missing row breaks the run), the full-window live gate is
date-space (first data strictly precedes the window start), the
reversal event is the forward window's adverse PATH extreme beyond the
bar, and the mixed row blends the four horizons at MIXED_HORIZON_WEIGHTS
renormalized over the legs with stats (the SQL 01 backfill's blend,
over 6dp-rounded legs).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from datetime import date

import numpy as np
import pandas as pd

from _common.df_utils import host_array

from analyze.analysis_forecasts.config import (
    FORWARD_HORIZONS,
    LOOKBACK_PERIOD,
    MIXED_HORIZON_WEIGHTS,
    MM_HORIZONS,
    REVERSE_THRESHOLD,
    REVERSE_THRESHOLD_MODE,
    REVERSE_THRESHOLD_STD_K,
    REVERSE_THRESHOLD_STD_MIN_DAYS,
)

# Live codes per partition: a (~1,220 × 2,048) window keeps every
# intermediate (the melted long values, the ×8 bar join) well inside
# the device budget regardless of the universe size.
CODE_CHUNK = 2048

_PERIOD_NAME = {1: "next", 5: "5d", 20: "20d", 60: "60d"}


def _finite_mask(s: pd.Series) -> pd.Series:
    """True where the float series holds a finite value (cudf has no
    np.isfinite fast path; NaN fails the self-equality, NULLs fill
    False)."""
    return (
        (s == s) & (s != float("inf")) & (s != float("-inf"))
    ).fillna(False)


def _band_ordinal(
    z: pd.Series,
    thresholds: tuple[float, ...],
) -> pd.Series:
    """Ordered band ordinal of one standardized series: the COUNT of
    thresholds the value passes (0 = below the lowest bar, …,
    len(thresholds) = above the highest — the vlow→vhigh state ladder);
    -1 where the value is NULL/NaN (no state). Pure boolean-mask
    addition — no cudf-host round trip."""
    has = _finite_mask(z)
    ord_ = None
    for t in thresholds:
        m = ((z > t).fillna(False) & has).astype("int8")
        ord_ = m if ord_ is None else ord_ + m
    return pd.Series(
        np.where(has, ord_, -1), index=z.index, dtype="int8",
    )


def _py_series(s: pd.Series) -> np.ndarray:
    """Series → REAL host object ndarray of python `date`s (the
    vectorized datetime64→date materialization for the DB arrays;
    asarray strips the cudf.pandas proxy subclass)."""
    return np.asarray(
        host_array(s.to_numpy()).astype("datetime64[D]").astype(object)
    )


class WideDfEngine(ABC):
    """The (code chunk × stat month) partitioned DataFrame engine."""

    # Family bucket-key columns riding on every trigger cell (besides
    # the standard cell columns). ``side`` is standard; is_market_hyped
    # is the cells' is_hyped flag.
    BUCKET_COLS: tuple[str, ...] = ()

    # Consecutive qualifying days merge into ONE mid-anchored signal
    # (the 2026-09 streak semantics); False = every qualifying day is
    # its own 1-day signal (the cross-event / state families).
    MERGE: bool = True

    def __init__(
        self,
        *,
        df: pd.DataFrame,
        first_dates: dict[str, date],
        episodes: pd.DataFrame,
        codes: list[str],
        sec_type: str,
        specs: list,
    ) -> None:
        self.df = df
        self.first_dates = first_dates
        self.episodes = episodes
        self.codes = codes
        self.sec_type = sec_type
        self.specs = specs
        self._cal: pd.DataFrame | None = None
        self._prepared: pd.DataFrame | None = None
        self._codes_frame_cache: pd.DataFrame | None = None

    # ------------------------------------------------------------------
    #  Partition machinery (the base owns the loops)
    # ------------------------------------------------------------------

    def run(self) -> Iterator[tuple[date, list[dict]]]:
        """Yield (stat_month, bucket rows) per stat month — month-major,
        so __main__ writes one atomic transaction per month."""
        self._prepare()
        for spec in self.specs:
            rows: list[dict] = []
            for win in self._month_chunks(spec):
                rows.extend(self.month_rows(win, spec))
            if rows:
                yield spec.stat_month, rows

    def month_rows(self, win: pd.DataFrame, spec) -> list[dict]:
        """One (month × code chunk) partition — the bucket pipeline:
        detect (emit_signals) → aggregate → rows. Bucket-free families
        override this directly."""
        rows: list[dict] = []
        for cells in self.emit_signals(win):
            rows.extend(self._cells_to_rows(cells, win, spec))
        return rows

    @abstractmethod
    def emit_signals(self, win: pd.DataFrame) -> Iterable[pd.DataFrame]:
        """The family's detection logic on one month-window frame —
        yields long-format trigger-cell frames (see the module
        docstring). Implementations hold ONLY their own metric logic."""

    def _prepare(self) -> None:
        """One-time frame prep: the union trading-day calendar (date →
        ordinal _t — the streak continuity axis) and the market-hype
        flags, both vectorized joins onto the fetched frame."""
        cal = (
            self.df[["date"]]
            .drop_duplicates()
            .sort_values("date")
            .reset_index(drop=True)
        )
        cal["_t"] = cal.index.astype("int64")
        self._cal = cal

        df = self.df.merge(cal, on="date", how="left")
        df["_row"] = np.arange(len(df), dtype="int64")
        # Hype flags: one vectorized interval join — (row ↔ episodes of
        # the same code), keep the pairs whose range covers the row's
        # date, deduplicate to a row flag.
        if not self.episodes.empty:
            j = df[["_row", "code", "date"]].merge(
                self.episodes, on="code", how="inner",
            )
            j = j[(j["date"] >= j["start_date"])
                  & (j["date"] <= j["end_date"])]
            hyp = j[["_row"]].drop_duplicates()
            hyp["is_hyped"] = True
            df = df.merge(hyp, on="_row", how="left")
        else:
            df["is_hyped"] = False
        df["is_hyped"] = df["is_hyped"].fillna(False).astype(bool)
        df = df.merge(
            self._codes_frame()[["code", "_pc", "first_date"]].rename(
                columns={"first_date": "_fd"}),
            on="code", how="left",
        )
        df = df.drop(columns=["_row"]).sort_values(
            ["code", "date"]).reset_index(drop=True)
        self._prepared = df

    def _month_chunks(self, spec) -> Iterator[pd.DataFrame]:
        """Yield the (live code chunk × window dates) slices of the
        prepared frame. The live gate is DATE-space (a code joins only
        once its first data strictly precedes the window start); codes
        are the sorted universe, so each chunk is one contiguous string
        range — the slice is two vectorized compares, no isin hash."""
        df = self._prepared
        cols = self._window_cols()
        codes_frame = self._codes_frame()
        # np.datetime64 scalars — the compare every backend accepts
        # (datetime64 columns vs python `date` objects raise).
        # the month's partitions from the PRE-PARTITIONED ids; the live
        # gate is a per-row compare against each code's own first-data
        # date (merged once in _prepare) — non-live codes sorting inside
        # a partition stay out, no per-month isin hash joins
        lower, upper = np.datetime64(spec.lower), np.datetime64(spec.upper)
        fd_cut = np.datetime64(spec.lower)
        live_pcs = codes_frame.loc[
            codes_frame["first_date"] < fd_cut, "_pc"
        ].drop_duplicates()
        for pc in sorted(live_pcs.tolist()):
            mask = (
                (df["_pc"] == pc)
                & (df["_fd"] < fd_cut)
                & (df["date"] >= lower)
                & (df["date"] <= upper)
            )
            win = df.loc[mask, cols]
            if not win.empty:
                yield win

    def _window_cols(self) -> list[str]:
        """The prepared frame's columns one month window needs: cell
        identity + the family's extra columns + forward changes."""
        cols = ["code", "date", "_t", "is_hyped"] + self._extra_window_cols()
        for n in FORWARD_HORIZONS:
            cols.append(f"next_change_{n}d")
        for n in MM_HORIZONS:
            cols.extend((f"path_high_{n}d", f"path_low_{n}d"))
        return cols

    def _extra_window_cols(self) -> list[str]:
        """Family-specific source columns emit_signals melts (compute_rsi:
        the rsi_{W}days columns)."""
        return []

    def _codes_frame(self) -> pd.DataFrame:
        """(code, first_date) — the universe with its true first-data
        dates (datetime64; the full-window live gate's input)."""
        if self._codes_frame_cache is None:
            cf = pd.DataFrame({
                "code": list(self.first_dates.keys()),
                "first_date": pd.to_datetime(
                    list(self.first_dates.values())
                ),
            })
            # PRE-PARTITION: the sorted universe's code-partition id —
            # assigned once here, merged onto the frame once in
            # _prepare; _month_chunks never slices code lists inline.
            cf["_pc"] = (
                np.arange(len(cf), dtype="int64") // CODE_CHUNK
            )
            self._codes_frame_cache = cf
        return self._codes_frame_cache

    # ------------------------------------------------------------------
    #  Detection helpers (shared, vectorized)
    # ------------------------------------------------------------------

    def _streak_merge(
        self,
        cells: pd.DataFrame,
        group_cols: list[str],
    ) -> pd.DataFrame:
        """Gaps-and-islands over the union calendar: consecutive
        qualifying days (_t increments by exactly 1 within the group)
        of each (group, code) run collapse to ONE row at the run's MID
        day (the ((L-1)//2 + 1)-th day); the run's day count and its
        [start, end] calendar dates ride along. MERGE=False keeps every
        qualifying day as a 1-day signal."""
        if not self.MERGE:
            cells = cells.copy()
            cells["run_len"] = 1
            cells["streak_start"] = cells["date"]
            cells["streak_end"] = cells["date"]
            return cells
        c = cells.sort_values(group_cols + ["_t"])
        prev = c.groupby(group_cols, sort=False)["_t"].shift(1)
        new_run = (prev.isna() | ((c["_t"] - prev) != 1)).fillna(True)
        c["_run"] = new_run.cumsum()
        runs = c.groupby("_run", sort=False).agg(
            _t0=("_t", "min"), run_len=("_t", "size"),
        )
        runs["_mid"] = runs["_t0"] + (runs["run_len"] - 1) // 2
        out = c.merge(runs, on="_run", how="left")
        out = out[out["_t"] == out["_mid"]]
        for col, before_mid in (("streak_start", True), ("streak_end", False)):
            if before_mid:
                out["_pos"] = out["_t"] - (out["run_len"] - 1) // 2
            else:
                out["_pos"] = out["_t"] + out["run_len"] // 2
            lookup = self._cal.rename(columns={"date": col})
            out = out.merge(
                lookup, left_on="_pos", right_on="_t",
                how="left", suffixes=("", "_cal"),
            ).drop(columns=["_pos", "_t_cal"])
        return out

    def _quantile_bars(
        self,
        values: pd.DataFrame,
        group_cols: list[str],
        q_spec: pd.DataFrame,
    ) -> pd.DataFrame:
        """Per-(group, q) linearly-interpolated quantile bars — the
        cudf-native form of _engine.quantile_threshold
        (pos = q·(valid_n−1)): one sort + cumcount rank, one
        (group × q) position table, two keyed gathers. ``values`` holds
        the non-null observations (group_cols + "value"); ``q_spec``
        holds group_cols + "q" — one row per (group, config)."""
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

    # ------------------------------------------------------------------
    #  Aggregation + row building
    # ------------------------------------------------------------------

    def _reverse_thresholds(self, win: pd.DataFrame) -> pd.DataFrame | None:
        """Per-(code, horizon) reversal bars of one window frame.
        "fixed" mode → None (the constant REVERSE_THRESHOLD applies);
        "std" mode → k_n · σ of the code's window forward changes
        (population σ over ALL window days — no look-ahead), with the
        fixed bar where σ is degenerate / under-sampled."""
        if REVERSE_THRESHOLD_MODE != "std":
            return None
        w = win
        helper: dict = {}
        for n in FORWARD_HORIZONS:
            col = w[f"next_change_{n}d"]
            fin = _finite_mask(col)
            w = w.assign(**{
                f"_fin{n}": fin,
                f"_w{n}": col.where(fin, 0.0),
                f"_w2{n}": col.where(fin, 0.0) ** 2,
            })
            helper[f"_cnt{n}"] = (f"_fin{n}", "sum")
            helper[f"_s{n}"] = (f"_w{n}", "sum")
            helper[f"_s2{n}"] = (f"_w2{n}", "sum")
        g = w.groupby("code", sort=False).agg(**helper)
        out = pd.DataFrame(index=g.index)
        for n in FORWARD_HORIZONS:
            cnt = g[f"_cnt{n}"]
            mean = g[f"_s{n}"] / cnt
            sig = np.sqrt((g[f"_s2{n}"] / cnt - mean ** 2).clip(lower=0.0))
            ok = (
                _finite_mask(sig)
                & (sig > 0)
                & (cnt >= REVERSE_THRESHOLD_STD_MIN_DAYS)
            )
            out[f"thr_{n}"] = np.where(
                ok, REVERSE_THRESHOLD_STD_K[n] * sig, REVERSE_THRESHOLD,
            )
        return out.reset_index()

    def _cells_to_rows(
        self,
        cells: pd.DataFrame,
        win: pd.DataFrame,
        spec,
    ) -> list[dict]:
        """One trigger-cell frame → the (5·R,) result row dicts:
        forward-change join, per-horizon groupby aggregation, the
        ragged date/excess arrays, the weight-blended mixed row and the
        motivation fan-out."""
        keys = ["code", "is_hyped", "side"] + list(self.BUCKET_COLS)
        thr_frame = self._reverse_thresholds(win)

        # Forward changes ride each trigger's own row — one keyed join.
        # (Engines whose cells are a subset of the window frame —
        # high_low_streaks — already carry them; skip the join, a
        # duplicate column would suffix-split the frame.)
        fwd_cols = self._window_cols()[4 + len(self._extra_window_cols()):]
        missing = [c for c in fwd_cols if c not in cells.columns]
        if missing:
            c = cells.merge(
                win[["code", "date"] + missing], on=["code", "date"],
                how="left",
            )
        else:
            c = cells.copy()
        if thr_frame is not None:
            c = c.merge(thr_frame, on="code", how="left").fillna(
                {f"thr_{n}": REVERSE_THRESHOLD for n in FORWARD_HORIZONS}
            )
        else:
            for n in FORWARD_HORIZONS:
                c[f"thr_{n}"] = REVERSE_THRESHOLD

        # Per-horizon helper columns (vectorized; invalid cells
        # contribute exact zeros so the all-cell sums are the valid-day
        # sums — the NC0 semantics of the numpy pipeline).
        top = c["side"] == "top"
        agg: dict = {}
        for n in FORWARD_HORIZONS:
            col = c[f"next_change_{n}d"]
            fin = _finite_mask(col)
            c[f"_fin{n}"] = fin
            c[f"_c{n}"] = col.where(fin, 0.0)
            c[f"_c2_{n}"] = c[f"_c{n}"] ** 2
            # SWING-AWARE reversal: the period's adverse PATH extreme
            # beyond the code's bar against the side — at the next-day
            # horizon the path IS the endpoint change.
            if n in MM_HORIZONS:
                adv_pos = c[f"path_low_{n}d"]
                adv_neg = c[f"path_high_{n}d"]
            else:
                adv_pos = col
                adv_neg = col
            c[f"_r{n}"] = (
                fin & ((top & (adv_pos < -c[f"thr_{n}"]))
                       | (~top & (adv_neg > c[f"thr_{n}"])))
            ).astype("int64")
            agg[f"cnt_{n}"] = (f"_fin{n}", "sum")
            agg[f"s_{n}"] = (f"_c{n}", "sum")
            agg[f"s2_{n}"] = (f"_c2_{n}", "sum")
            agg[f"thr_{n}"] = (f"thr_{n}", "max")
            agg[f"rev_{n}"] = (f"_r{n}", "sum")
            if n in MM_HORIZONS:
                c[f"_hx{n}"] = col.where(fin, -np.inf)
                c[f"_ln{n}"] = col.where(fin, np.inf)
                agg[f"max_{n}"] = (f"_hx{n}", "max")
                agg[f"min_{n}"] = (f"_ln{n}", "min")
        a = c.groupby(keys, sort=False).agg(**agg).reset_index()
        # 'flat' buckets (px_vol's flat speed, valuation mid states)
        # make no directional claim — their reverse counts are junk by
        # construction (no top/bottom test applies); null them.
        flat = a["side"] == "flat"
        if flat.any():
            for n in FORWARD_HORIZONS:
                a[f"rev_{n}"] = a[f"rev_{n}"].where(~flat)
        extras = self.bucket_extras(c, keys)
        if extras is not None:
            a = a.merge(extras, on=keys, how="left")
        has_config = "bucket_config" in a.columns
        a = a.merge(self._ragged_arrays(c, keys), on=keys, how="left")

        # ---- period rows (next/5d/20d/60d) + the blended mixed row ----
        legs = {
            n: {
                "ave": (a[f"s_{n}"] / a[f"cnt_{n}"]).where(a[f"cnt_{n}"] > 0),
                "std": _std_col(a[f"s2_{n}"], a[f"s_{n}"], a[f"cnt_{n}"]),
                "occ": a[f"cnt_{n}"],
                "rev": (a[f"rev_{n}"] / a[f"cnt_{n}"]).where(
                    a[f"cnt_{n}"] > 0),
                "thr": a[f"thr_{n}"],
                "max": a[f"max_{n}"] if n in MM_HORIZONS else None,
                "min": a[f"min_{n}"] if n in MM_HORIZONS else None,
            }
            for n in FORWARD_HORIZONS
        }

        period_kwargs = dict(config=(
            a["bucket_config"] if has_config else self._config_jsonb()))
        periods = [
            _period_frame(a, keys, legs[n], _PERIOD_NAME[n], n,
                          **period_kwargs)
            for n in FORWARD_HORIZONS
        ]
        periods.append(
            _period_frame(a, keys, _mixed_blend(a, legs), "mixed", None,
                          **period_kwargs)
        )
        out = pd.concat(periods, ignore_index=True)
        out["_b"] = np.tile(np.arange(len(a)), len(periods))
        out = out.sort_values("_b", kind="stable").drop(columns=["_b"])
        out = out.round(6)
        out["is_market_hyped"] = out["is_hyped"].astype(bool)

        # Motivation fan-out: the bucket identity + the MEAN streak
        # length per bucket (one groupby over the kept cells).
        streak = (
            cells.groupby(keys, sort=False)["run_len"].mean().round(2)
            .rename("streak_signal_days").reset_index()
        )
        out = out.merge(streak, on=keys, how="left")
        # The constant scalars ride the COPY boundary as shared keys —
        # assigning a python-date/object column into the frame would
        # trip a cudf normalize fallback per partition.
        return _to_records(
            out.drop(columns=["is_hyped"]),
            shared={
                "sec_type": self.sec_type,
                "stat_month": spec.stat_month,
                "lookback_period": LOOKBACK_PERIOD,
                **self.family_constants(),
            },
        )

    def _config_jsonb(self):
        """The family's constant config JSONB payload (None = NULL)."""
        return None

    def family_constants(self) -> dict:
        """Constant columns stamped onto every row of this family (the
        recorded build parameters the mov tables carry — e.g. px_vol's
        sigma_window / k bars)."""
        return {}

    def bucket_extras(
        self,
        cells: pd.DataFrame,
        keys: list[str],
    ) -> pd.DataFrame | None:
        """Optional per-bucket aggregates beyond the standard legs —
        e.g. px_vol's config JSONB {mean_t, mean_z} or high_low_streaks'
        {mean,min,max}_day_count. Returns a frame keyed by ``keys`` with
        a ``bucket_config`` column (the JSONB string); None = none."""
        return None

    def _ragged_arrays(self, c: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
        """Per-bucket trigger_dates / trigger_excess / streak-span lists
        over the VALID cells of each horizon, parallel per horizon and
        per bucket (the forecast_results array columns). The list
        assembly is the asyncpg boundary — one host-numpy slicing pass
        over the group-ascending cells."""
        c = c.sort_values(keys + ["date"])
        # Group-boundary detection over STRING-cast keys: the shifted
        # first row would carry nulls (a null-propagating compare would
        # break cudf's row-wise any) — the string frame fills "" and
        # the first row simply differs from it.
        k = c[keys].astype(str)
        new_grp = (k.shift().fillna("") != k).any(axis=1)
        gid = np.asarray(new_grp.cumsum().to_numpy(), dtype="int64") - 1
        key_rows = c.loc[new_grp, keys].reset_index(drop=True)

        dates = _py_series(c["date"])
        ss = _py_series(c["streak_start"])
        se = _py_series(c["streak_end"])
        ex = np.asarray(np.round(
            np.asarray(host_array(c["excess"].to_numpy()), dtype="float64"),
            6,
        ))
        rl = np.asarray(
            host_array(c["run_len"].to_numpy()), dtype="int64",
        )

        out = key_rows
        for n in FORWARD_HORIZONS:
            fin = np.asarray(c[f"_fin{n}"].to_numpy(), dtype=bool)
            gv = gid[fin]
            u = np.unique(gv)
            lo = np.searchsorted(gv, u, side="left")
            hi = np.searchsorted(gv, u, side="right")
            sel = list(zip(lo.tolist(), hi.tolist()))
            # one WHOLESALE numpy→python conversion per column (the
            # masks stay vectorized numpy; the per-bucket slices are
            # plain C-speed list slices — no per-slice numpy indexing).
            d_v = dates[fin].tolist()
            ss_v = ss[fin].tolist()
            se_v = se[fin].tolist()
            ex_v = [None if x != x else x for x in ex[fin].tolist()]
            rl_v = rl[fin].tolist()
            part = key_rows.take(u).reset_index(drop=True)  # vectorized gather
            part[f"trigger_dates_{n}"] = [d_v[a:b] for a, b in sel]
            part[f"trigger_excess_{n}"] = [ex_v[a:b] for a, b in sel]
            part[f"streak_starts_{n}"] = [ss_v[a:b] for a, b in sel]
            part[f"streak_ends_{n}"] = [se_v[a:b] for a, b in sel]
            part[f"streak_days_{n}"] = [rl_v[a:b] for a, b in sel]
            out = out.merge(part, on=keys, how="left")
        return out


def _std_col(s2, s, cnt):
    """Population std sqrt(E[x²] − E[x]²) over the valid days, floored
    at 0 (the rounding guard); NULL where the count is 0."""
    mean = s / cnt
    var = s2 / cnt - mean ** 2
    return np.sqrt(var.clip(lower=0.0)).where(cnt > 0)


def _mixed_blend(a, legs: dict[int, dict]):
    """The FIXED-weight blended mixed row (the SQL 01 backfill's blend
    over the 6dp-rounded legs): ave / reverse_prob renormalized over
    the horizons with stats, std the mixture dispersion
    sqrt(Σw·E[x²] / Σw − mean²) — NOT the mean of the stds —
    occurrence_count the MIN positive leg count, threshold the
    full-weight mean of the four bars, extrema + arrays NULL. A small
    (R × 4) leg-table algebra — one vectorized pass per column."""
    # NOTE: the weight vector stays 1-D — cudf.pandas' numpy interop
    # breaks column-vector (4,1) broadcasts against (R, 4) tables (the
    # probe in temp_scripts), while last-axis (4,) broadcasts are fine.
    w = np.array([MIXED_HORIZON_WEIGHTS[n] for n in FORWARD_HORIZONS])

    def _leg(key: str, dtype, ns=tuple(FORWARD_HORIZONS)) -> np.ndarray:
        """(R, len(ns)) host table of one leg stat — the proxies are
        unwrapped ONCE here (to_numpy + host_array + asarray — asarray
        strips the cudf.pandas proxy subclass so the blend is plain
        host numpy), the blend itself is host algebra over the small
        (R × 4) table."""
        return np.stack(
            [np.asarray(
                host_array(legs[n][key].to_numpy(dtype=dtype,
                                               na_value=np.nan)),
                dtype=dtype,
            ) for n in ns],
            axis=1,
        )

    ave = _leg("ave", "float64").round(6)
    std = _leg("std", "float64").round(6)
    rev = np.nan_to_num(_leg("rev", "float64").round(6))
    thr = np.nan_to_num(_leg("thr", "float64").round(6))
    occ = _leg("occ", "int64")

    valid = (ave == ave)                      # NaN = leg without stats
    w_stat = (valid * w).sum(axis=1)
    safe = np.where(w_stat > 0, w_stat, np.nan)
    ave_m = (np.where(valid, ave, 0.0) * w).sum(axis=1) / safe
    ex2 = (np.where(valid, np.nan_to_num(std ** 2 + ave ** 2), 0.0)
           * w).sum(axis=1) / safe
    std_m = np.sqrt(np.maximum(ex2 - ave_m ** 2, 0.0))
    rev_m = (rev * w).sum(axis=1) / safe
    pos = occ > 0
    occ_m = np.where(
        pos.any(axis=1),
        np.where(pos, occ, np.iinfo(np.int64).max).min(axis=1),
        0,
    )
    thr_m = (thr * w).sum(axis=1)
    idx = a.index
    return {
        "ave": pd.Series(ave_m, index=idx),
        "std": pd.Series(std_m, index=idx),
        "occ": pd.Series(occ_m, index=idx),
        "rev": pd.Series(rev_m, index=idx),
        "thr": pd.Series(thr_m, index=idx),
        "max": None,
        "min": None,
    }


def _period_frame(a, keys, payload, period, n, config=None):
    """One period's row frame — the motivation key columns (the caller's
    ``keys``: code / is_hyped / side / family bucket cols) + the
    consolidated forecast_results fields of the payload. Numeric NULLs
    are NaN placeholders (uniform float dtypes through the concat; the
    asyncpg boundary maps NaN → None). ``config`` is the bucket's JSONB
    payload — a constant scalar or a per-bucket Series aligned on ``a``."""
    out = a[keys].copy()
    out["period"] = period
    out["config"] = config
    out["ave_change"] = payload["ave"]
    out["std_change"] = payload["std"]
    out["max_change"] = (payload["max"] if payload["max"] is not None
                         else np.nan)
    out["min_change"] = (payload["min"] if payload["min"] is not None
                         else np.nan)
    out["occurrence_count"] = payload["occ"].astype("int64")
    out["reverse_prob"] = payload["rev"]
    out["threshold"] = payload["thr"]
    if n is None:
        for col in ("trigger_dates", "streak_starts", "streak_ends",
                    "streak_days", "trigger_excess"):
            out[col] = None
    else:
        out["trigger_dates"] = a[f"trigger_dates_{n}"]
        out["streak_starts"] = a[f"streak_starts_{n}"]
        out["streak_ends"] = a[f"streak_ends_{n}"]
        out["streak_days"] = a[f"streak_days_{n}"]
        out["trigger_excess"] = a[f"trigger_excess_{n}"]
    return out


def _to_records(out: pd.DataFrame, shared: dict | None = None) -> list[dict]:
    """The asyncpg COPY boundary: frame → row dicts with plain Python
    scalars (np.int64 / np.bool_ are not encodable; NaN and the ±inf
    aggregation sentinels → None — asyncpg cannot encode them into
    NUMERIC columns). ``shared`` constants are stamped onto every row
    here instead of on the frame (a python-date column would trip a
    cudf normalize fallback)."""
    records = out.to_dict("records")
    if shared:
        records = [{**shared, **r} for r in records]
    for r in records:
        for k, v in r.items():
            if v is not None and hasattr(v, "item"):
                r[k] = v.item()
            elif isinstance(v, float) and (v != v or v in _NONFINITE):
                r[k] = None
            elif isinstance(v, list):
                r[k] = [
                    None if (isinstance(x, float) and (x != x
                                                       or x in _NONFINITE))
                    else (x.item() if hasattr(x, "item") else x)
                    for x in v
                ]
    return records


_NONFINITE = (float("inf"), float("-inf"))
