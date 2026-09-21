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
    (one row per qualifying signal: code / date / _t / regime / side
    / excess / run_len / streak spans + the family's BUCKET_COLS).
    Metric files hold ONLY their own detection logic (compute_rsi:
    melt + quantile bars + top/bottom tests),
  - streak-merge (gaps-and-islands over the union trading-day
    calendar), forward-change aggregation, per-horizon reversal bars,
    the weight-blended mixed row and the row emission are shared and
    vectorized: groupby / merge / boolean algebra — no numpy tensor
    stack, no per-code / per-config Python loops. The only scalar
    materialization is the per-bucket ragged date/excess array
    LITERALS the DB array columns require (host-numpy slicing +
    C-speed string joins — the CSV writer's wire format; the engine
    hands the writer result FRAMES, not row dicts).

Semantics are the historic numpy pipeline's: consecutive qualifying
UNION-CALENDAR days merge into ONE signal with incremental anchor
triggers at delays 0..TRIGGER_DELAY_MAX (a suspended
day's missing row breaks the run), the full-window live gate is
date-space (first data strictly precedes the window start), the
reversal event is the forward window's adverse PATH extreme beyond the
bar, and the mixed row blends the three horizons at MIXED_HORIZON_WEIGHTS
renormalized over the legs with stats (the SQL 01 backfill's blend,
over 6dp-rounded legs).
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from datetime import date

import numpy as np
import pandas as pd

from _common.df_utils import host_array
from _common.df_utils._detector import is_gpu_available

import logging
logger = logging.getLogger(__name__)

from analyze.analysis_forecasts.config import (
    FORWARD_HORIZONS,
    LOOKBACK_PERIOD,
    MIXED_HORIZON_WEIGHTS,
    MM_HORIZONS,
    REVERSE_THRESHOLD,
    REVERSE_THRESHOLD_MODE,
    REVERSE_THRESHOLD_STD_K,
    REVERSE_THRESHOLD_STD_MIN_DAYS,
    TRIGGER_DELAY_MAX,
)

# ---- Memory-aware partition budgets (2026-09 regime refactor) ---------------
#
# One (month × code-chunk) partition's working-set budget, expressed in
# float64 CELLS of the partition's primary window slice. The in-flight
# intermediates (the melted per-config long frames, the ×8 bar join,
# the ragged-array host passes) hold a small multiple of the slice, so
# a slice budget bounds every intermediate's footprint. GPU mode
# budgets TIGHTER than CPU: the device also holds the FULL prepared
# frame (the whole sec_type universe — the stock frame alone is
# ~6.5M rows) plus the cudf pool, while the host only mirrors slices.
#
# The chunk size derives from the frame's OWN shape — mean rows per
# code × the window width — so long histories / wide families get
# proportionally smaller chunks:
#
#   chunk = clamp(budget / (rows_per_code × width), MIN, MAX)
#
# The budget already covers the melted long frames of the wide
# families: a melt fans rows ×K while SHRINKING to ~5 narrow columns,
# so the melted frame's cell count stays the same ORDER as the primary
# slice — the working set is 2-4× the slice either way. Multiplying
# the width by the melt factor would double-count and over-partition
# small universes (index measured 3 partitions where the historic
# single partition was already well inside budget).
#
# Semantics are UNCHANGED — the partitioning only bounds memory; the
# month loop, live gate and aggregation are per-(chunk, month) worlds
# exactly as before (the historic flat CODE_CHUNK = MAX_CODE_CHUNK =
# 2048 remains the ceiling for small universes, which stay whole).
GPU_PARTITION_CELL_BUDGET = 40_000_000   # ~320 MiB slice → ~1.3 GiB device working set
CPU_PARTITION_CELL_BUDGET = 80_000_000   # ~640 MiB slice → ~2.6 GiB host working set
MIN_CODE_CHUNK = 128
MAX_CODE_CHUNK = 2048

_PERIOD_NAME = {1: "next", 5: "5d", 20: "20d"}


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


class WideDfEngine(ABC):
    """The (code chunk × stat month) partitioned DataFrame engine."""

    # Family bucket-key columns riding on every trigger cell (besides
    # the standard cell columns). ``side`` is standard; regime_state is
    # the cells' per-day market-regime label (stats.market_regimes).
    BUCKET_COLS: tuple[str, ...] = ()

    # Consecutive qualifying days merge into ONE signal emitting
    # incremental anchor triggers at delays 0..TRIGGER_DELAY_MAX (the
    # 2026-09-21 streak semantics); False = every qualifying day is
    # its own 1-day signal (the cross-event / state families).
    MERGE: bool = True

    def __init__(
        self,
        *,
        df: pd.DataFrame,
        first_dates: dict[str, date],
        regimes: pd.DataFrame,
        codes: list[str],
        sec_type: str,
        specs: list,
    ) -> None:
        self.df = df
        self.first_dates = first_dates
        self.regimes = regimes
        self.codes = codes
        self.sec_type = sec_type
        self.specs = specs
        self._cal: pd.DataFrame | None = None
        self._prepared: pd.DataFrame | None = None
        self._codes_frame_cache: pd.DataFrame | None = None
        self._code_chunk: int | None = None

    # ------------------------------------------------------------------
    #  Partition machinery (the base owns the loops)
    # ------------------------------------------------------------------

    def run(self) -> Iterator[tuple[date, object]]:
        """Yield (stat_month, payload) per stat month — month-major, so
        __main__ writes one atomic transaction per month. The payload is
        a bucket-major result FRAME for the bucket families (the
        writer's fast path: CSV render + frame-firsts mov/identity
        rows) or a list of row dicts for the families that override
        ``month_rows`` directly (base_rates). Frame payloads from the
        month's code chunks are concatenated with the ``_bkt`` writer
        tag offset so bucket groups stay unique across the month."""
        self._prepare()
        for spec in self.specs:
            items: list = []
            offset = 0
            for win in self._month_chunks(spec):
                chunk = self.month_rows(win, spec)
                if chunk and isinstance(chunk[0], pd.DataFrame):
                    for f in chunk:
                        f["_bkt"] = f["_bkt"] + offset
                        offset = int(f["_bkt"].max()) + 1
                items.extend(chunk)
            if not items:
                continue
            if isinstance(items[0], pd.DataFrame):
                yield (spec.stat_month,
                       items[0] if len(items) == 1
                       else pd.concat(items, ignore_index=True))
            else:
                yield spec.stat_month, items

    def month_rows(self, win: pd.DataFrame, spec) -> list:
        """One (month × code chunk) partition — the bucket pipeline:
        detect (emit_signals) → aggregate → result frames (bucket-major,
        ``_bkt``-tagged, shared constants stamped — the writer's fast
        path). Bucket-free families override this directly and return
        row dicts."""
        frames: list[pd.DataFrame] = []
        for cells in self.emit_signals(win):
            frames.append(self._cells_to_rows(cells, win, spec))
        return frames

    @abstractmethod
    def emit_signals(self, win: pd.DataFrame) -> Iterable[pd.DataFrame]:
        """The family's detection logic on one month-window frame —
        yields long-format trigger-cell frames (see the module
        docstring). Implementations hold ONLY their own metric logic."""

    def _prepare(self) -> None:
        """One-time frame prep: the union trading-day calendar (date →
        ordinal _t — the streak continuity axis) and the market-regime
        day labels, both vectorized joins onto the fetched frame."""
        cal = (
            self.df[["date"]]
            .drop_duplicates()
            .sort_values("date")
            .reset_index(drop=True)
        )
        cal["_t"] = cal.index.astype("int64")
        self._cal = cal

        df = self.df.merge(cal, on="date", how="left")
        # Regime labels: one plain (code, date) merge against the DAILY
        # states table (stats.market_regimes — every trading day of the
        # build's universe carries a row; 'calm' is the table's own
        # no-state label). Days outside the regimes build's universe
        # default to 'calm' (the retired boolean's never-hyped
        # semantics).
        if not self.regimes.empty:
            df = df.merge(
                self.regimes[["code", "date", "regime"]],
                on=["code", "date"], how="left",
            )
        else:
            df["regime"] = None
        df["regime"] = df["regime"].fillna("calm")
        df = df.merge(
            self._codes_frame()[["code", "_pc", "first_date"]].rename(
                columns={"first_date": "_fd"}),
            on="code", how="left",
        )
        df = df.sort_values(["code", "date"]).reset_index(drop=True)
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
        cols = ["code", "date", "_t", "regime"] + self._extra_window_cols()
        for n in FORWARD_HORIZONS:
            cols.append(f"next_change_{n}d")
        for n in MM_HORIZONS:
            cols.extend((f"path_high_{n}d", f"path_low_{n}d"))
        return cols

    def _extra_window_cols(self) -> list[str]:
        """Family-specific source columns emit_signals melts (compute_rsi:
        the rsi_{W}days columns)."""
        return []

    def _compute_code_chunk(self) -> int:
        """The memory-aware codes-per-partition size (see the budget
        block at the module head): the frame's own mean rows-per-code
        and window width against the GPU/CPU cell budget."""
        n_codes = max(1, len(self.codes))
        rows_per_code = max(1.0, len(self.df) / n_codes)
        width = max(1, len(self._window_cols()))
        gpu = is_gpu_available()
        budget = (GPU_PARTITION_CELL_BUDGET if gpu
                  else CPU_PARTITION_CELL_BUDGET)
        chunk = int(budget / max(1.0, rows_per_code * width))
        chunk = max(MIN_CODE_CHUNK, min(MAX_CODE_CHUNK, chunk))
        logger.info(
            "  memory-aware partitioning: %s codes/chunk "
            "(budget %s cells, %s mode, ~%.0f rows/code, width %d) "
            "-> %d partitions over %d codes",
            chunk, f"{budget:,}", "GPU" if gpu else "CPU",
            rows_per_code, width,
            -(-n_codes // chunk), n_codes,
        )
        return chunk

    def _codes_frame(self) -> pd.DataFrame:
        """(code, first_date) — the universe with its true first-data
        dates (datetime64; the full-window live gate's input)."""
        if self._codes_frame_cache is None:
            if self._code_chunk is None:
                self._code_chunk = self._compute_code_chunk()
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
                np.arange(len(cf), dtype="int64") // self._code_chunk
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
        of each (group, code) run emit ONE TRIGGER ANCHOR PER DAY
        0..TRIGGER_DELAY_MAX (delay = the day's offset within the run;
        0 is the run's first qualifying day, when the signal becomes
        observable) — each anchor row carries the run's day count and
        its [start, end] calendar dates. MERGE=False keeps every
        qualifying day as its own 1-day delay-0 signal."""
        if not self.MERGE:
            cells = cells.copy()
            cells["run_len"] = 1
            cells["delay"] = 0
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
        out = c.merge(runs, on="_run", how="left")
        # The incremental anchors: the run's first TRIGGER_DELAY_MAX + 1
        # days each carry the signal (the delay-d forecast is conditioned
        # on the run having lasted d + 1 days). Filter on the int64
        # offset BEFORE any narrowing cast — long runs would overflow a
        # small int and wrap into the kept range.
        out["delay"] = out["_t"] - out["_t0"]
        out = out[out["delay"] <= TRIGGER_DELAY_MAX]
        for col in ("streak_start", "streak_end"):
            if col == "streak_start":
                out["_pos"] = out["_t0"]
            else:
                out["_pos"] = out["_t0"] + out["run_len"] - 1
            lookup = self._cal.rename(columns={"date": col})
            out = out.merge(
                lookup, left_on="_pos", right_on="_t",
                how="left", suffixes=("", "_cal"),
            ).drop(columns=["_pos", "_t_cal"])
        return out.drop(columns=["_t0"])

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
    ) -> pd.DataFrame:
        """One trigger-cell frame → the (4·D·R,) result rows: forward-
        change join, per-(bucket, delay) groupby aggregation, the
        ragged date/excess array literals, the weight-blended mixed row
        and the motivation fan-out — as the bucket-major shared-stamp
        FRAME the writer's fast path consumes."""
        ids = ["code", "regime", "side"] + list(self.BUCKET_COLS)
        keys = ids + ["delay"]
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
        # Bucket-major order: bucket groups contiguous, each bucket's
        # delays consecutive — the writer's _bkt grouping assumes it.
        a = a.sort_values(keys, kind="stable")
        # The writer's bucket tag: ONE forecast_id per identity bucket,
        # shared across its delay rows — factorized via group-boundary
        # detection over the string-cast identity keys (the
        # _ragged_arrays trick; the shifted first row carries nulls, a
        # null-propagating compare would break cudf's row-wise any).
        kid = a[ids].astype(str)
        a["_bkt"] = (
            (kid.shift().fillna("") != kid).any(axis=1)
            .astype("int64").cumsum()
        )
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

        # ---- period rows (next/5d/20d) + the blended mixed row ----
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
        # Bucket-major row order for the writer: consecutive equal-_bkt
        # runs share one bucket; within a bucket the delays ascend and
        # the 4 period rows keep their concat order (stable sort).
        out["_bkt"] = np.tile(a["_bkt"].to_numpy(), len(periods))
        out = out.sort_values(["_bkt", "delay"], kind="stable")
        out = out.round(6)
        out["regime_state"] = out["regime"]

        # Motivation fan-out: the bucket identity + the MEAN streak
        # length per MERGED SIGNAL (one row per run PER BUCKET — runs
        # span regime changes, so a multi-regime run must count toward
        # every bucket that owns one of its anchors; dedupe on
        # (identity, run id) — the anchors otherwise multiply each run
        # by up to TRIGGER_DELAY_MAX + 1) and the MEAN ANCHOR DELAY
        # (the mean trading-day offset of the bucket's emitted triggers
        # within their runs; 0 = the streak's first qualifying day).
        # Both registry columns are INTEGER whole trading days; the
        # fractional means round HALF-UP ((x + 0.5) truncated matches
        # Postgres ROUND's half-away-from-zero on these non-negative
        # means, keeping the SQL 01 backfill digit-consistent). The
        # delay mean stays under TRIGGER_DELAY_MAX by construction.
        per_run = (
            cells if not self.MERGE
            else cells.drop_duplicates(subset=ids + ["_run"])
        )
        streak = per_run.groupby(ids, sort=False).agg(
            streak_signal_days=("run_len", "mean"),
        ).reset_index()
        anchor_delays = cells.groupby(ids, sort=False).agg(
            delayed_signal_days=("delay", "mean"),
        ).reset_index()
        streak = streak.merge(anchor_delays, on=ids, how="inner")
        for col in ("streak_signal_days", "delayed_signal_days"):
            streak[col] = (streak[col] + 0.5).astype("int64")
        streak["delayed_signal_days"] = streak["delayed_signal_days"].clip(
            upper=TRIGGER_DELAY_MAX)
        out = out.merge(streak, on=ids, how="left")
        return self._shared_frame(out.drop(columns=["regime"]), spec)

    def _shared_frame(self, out: pd.DataFrame, spec) -> pd.DataFrame:
        """Stamp the shared constants (identity + family parameters) as
        plain scalar columns so the FRAME alone reaches the writer — the
        per-row dict COPY boundary is gone for the df-engine families
        (the writer renders forecast_results via the CSV path and
        extracts the mov/identity rows from each bucket group's first
        row). stat_month lands as a datetime64 scalar (the CSV
        renderer's np.datetime_as_string path); no object-date
        columns."""
        for k, v in {
            "sec_type": self.sec_type,
            "stat_month": np.datetime64(spec.stat_month),
            "lookback_period": LOOKBACK_PERIOD,
            **self.family_constants(),
        }.items():
            out[k] = v
        return out

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
        """Per-bucket trigger_dates / trigger_excess / streak-span
        values, parallel per horizon and per bucket — as PostgreSQL
        array-literal TEXT (``{2025-01-02,...}``), the CSV writer's
        wire format. The assembly runs at host C speed: dates are
        pre-rendered once via ``np.datetime_as_string`` (no python
        ``date`` objects), each horizon's per-bucket slices join into
        their literal in one pass, and buckets fill by POSITION over
        the group-ascending key rows (``arr[u] = literals`` — no
        per-horizon merges). Keeping the arrays as plain object-string
        columns (never cudf list dtype) is the point: list columns
        poison every downstream frame op (round / sort / to_dict
        fallbacks) and the per-element host conversions dominated the
        old boundary.

        NULL semantics preserved: a bucket with no valid cell at a
        horizon gets None (NULL array); an empty slice keeps ``{}`` (an
        empty array, as before); NaN excess elements and NaT dates
        render as NULL elements."""
        c = c.sort_values(keys + ["date"])
        # Group-boundary detection over STRING-cast keys: the shifted
        # first row would carry nulls (a null-propagating compare would
        # break cudf's row-wise any) — the string frame fills "" and
        # the first row simply differs from it.
        k = c[keys].astype(str)
        new_grp = (k.shift().fillna("") != k).any(axis=1)
        gid = np.asarray(new_grp.cumsum().to_numpy(), dtype="int64") - 1
        key_rows = c.loc[new_grp, keys].reset_index(drop=True)
        G = len(key_rows)

        def day_str(col: str) -> np.ndarray:
            """Host datetime column → 'YYYY-MM-DD' object strings
            (C-speed); NaT → 'NULL' (a NULL array ELEMENT)."""
            arr = np.asarray(
                host_array(c[col].to_numpy())
            ).astype("datetime64[D]")
            s = np.datetime_as_string(arr, unit="D").astype(object)
            s[np.isnat(arr)] = "NULL"
            return s

        d_s = day_str("date")
        ss_s = day_str("streak_start")
        se_s = day_str("streak_end")
        ex = np.asarray(np.round(
            np.asarray(host_array(c["excess"].to_numpy()), dtype="float64"),
            6,
        ))
        rl = np.asarray(
            host_array(c["run_len"].to_numpy()), dtype="int64",
        )

        def ex_lit(v: float) -> str:
            # NaN excess element → NULL element (the old None mapping).
            return "NULL" if v != v else repr(v)

        out_cols: dict[str, np.ndarray] = {}
        for n in FORWARD_HORIZONS:
            fin = np.asarray(c[f"_fin{n}"].to_numpy(), dtype=bool)
            gv = gid[fin]
            u = np.unique(gv)
            lo = np.searchsorted(gv, u, side="left")
            hi = np.searchsorted(gv, u, side="right")
            sel = list(zip(lo.tolist(), hi.tolist()))
            # one WHOLESALE numpy→python conversion per column (the
            # masks stay vectorized numpy; the per-bucket literal joins
            # are plain C-speed str joins).
            d_v = d_s[fin].tolist()
            ss_v = ss_s[fin].tolist()
            se_v = se_s[fin].tolist()
            ex_v = [ex_lit(x) for x in ex[fin].tolist()]
            rl_v = rl[fin].tolist()

            def fill(make) -> np.ndarray:
                arr_: np.ndarray = np.full(G, None, dtype=object)
                arr_[u] = [make(a, b) for a, b in sel]
                return arr_

            out_cols[f"trigger_dates_{n}"] = fill(
                lambda a, b: "{" + ",".join(d_v[a:b]) + "}")
            out_cols[f"trigger_excess_{n}"] = fill(
                lambda a, b: "{" + ",".join(ex_v[a:b]) + "}")
            out_cols[f"streak_starts_{n}"] = fill(
                lambda a, b: "{" + ",".join(ss_v[a:b]) + "}")
            out_cols[f"streak_ends_{n}"] = fill(
                lambda a, b: "{" + ",".join(se_v[a:b]) + "}")
            out_cols[f"streak_days_{n}"] = fill(
                lambda a, b: "{" + ",".join(map(str, rl_v[a:b])) + "}")
        return key_rows.assign(**out_cols)


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
    full-weight mean of the three bars, extrema + arrays NULL. A small
    (R × 3) leg-table algebra — one vectorized pass per column."""
    # NOTE: the weight vector stays 1-D — cudf.pandas' numpy interop
    # breaks column-vector (3,1) broadcasts against (R, 3) tables (the
    # probe in temp_scripts), while last-axis (3,) broadcasts are fine.
    w = np.array([MIXED_HORIZON_WEIGHTS[n] for n in FORWARD_HORIZONS])

    def _leg(key: str, dtype, ns=tuple(FORWARD_HORIZONS)) -> np.ndarray:
        """(R, len(ns)) host table of one leg stat — the proxies are
        unwrapped ONCE here (to_numpy + host_array + asarray — asarray
        strips the cudf.pandas proxy subclass so the blend is plain
        host numpy), the blend itself is host algebra over the small
        (R × 3) table."""
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
    ``keys``: code / regime / side / family bucket cols) + the
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
