"""Base-rate monthly aggregation (analysis_forecasts) — the metric file.

Per stat month's trailing 5-year window and horizon n, the UNCONDITIONAL
forward stats over ALL of each live code's window days with a valid
n-day forward change (vs the buckets' trigger-day subsets): valid-day
count, mean change, and the SWING-AWARE reversal probabilities —
base_down_prob counts days whose forward window swung ≥ thr BELOW the
signal close (the top/upper-side event), base_up_prob days whose window
swung ≥ thr ABOVE it — at the same adaptive per-(code, horizon) bar the
bucket rows use (``_dfengine._reverse_thresholds``), so lift
(bucket prob − base prob) stays in one scale. The path-extreme event is
exactly what the bucket reverse_prob counts; at the next-day horizon
the path IS the endpoint change.

Per horizon the writer emits the 'next'/'5d'/'20d' rows, then ONE
weight-blended 'mixed' row per code (the FIXED-weight blend of the three
horizon rates — weights renormalized over the horizons with valid
stats; base_count the MIN valid leg count; threshold the full-weight
mean of the three bars) — the unconditional reference of the bucket
rows' blended period='mixed' profile, so the analysis_signals gate reads
bucket and base on the same blended row.

Written to analysis_forecasts.base_rates (PK: sec_type, code,
stat_month, period; NOT registered in forecast_identities — no
forecast_id). Yields (stat_month, rows) month-major.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pandas as pd

from _common.df_utils import host_array

from analyze.analysis_forecasts._dfengine import (
    WideDfEngine,
    _finite_mask,
    _to_records,
)
from analyze.analysis_forecasts.config import (
    FORWARD_HORIZONS,
    LOOKBACK_PERIOD,
    MIXED_HORIZON_WEIGHTS,
    MM_HORIZONS,
    PERIOD_FOR_HORIZON,
    PERIOD_MIXED,
    REVERSE_THRESHOLD,
)

_PERIODS = [PERIOD_FOR_HORIZON[n] for n in FORWARD_HORIZONS] + [PERIOD_MIXED]


class _BaseRateEngine(WideDfEngine):
    """Base rates — no buckets: ``month_rows`` is overridden directly
    (the emit_signals hook stays unused)."""

    def emit_signals(self, win: pd.DataFrame) -> Iterator[pd.DataFrame]:
        return iter(())   # unused — the base-rate family has no buckets

    def month_rows(self, win: pd.DataFrame, spec) -> list[dict]:
        thr_frame = self._reverse_thresholds(win)
        w = win
        if thr_frame is not None:
            w = w.merge(thr_frame, on="code", how="left").fillna(
                {f"thr_{n}": REVERSE_THRESHOLD for n in FORWARD_HORIZONS}
            )
        else:
            for n in FORWARD_HORIZONS:
                w[f"thr_{n}"] = REVERSE_THRESHOLD

        # Per-horizon window-day helpers (vectorized; the invalid days
        # contribute exact zeros — the NC0 semantics).
        agg: dict = {}
        for n in FORWARD_HORIZONS:
            col = w[f"next_change_{n}d"]
            fin = _finite_mask(col)
            w[f"_fin{n}"] = fin
            w[f"_c{n}"] = col.where(fin, 0.0)
            if n in MM_HORIZONS:
                # SWING-AWARE event: the forward window's adverse PATH
                # extreme beyond the bar (at the next-day horizon the
                # path IS the endpoint — no separate matrices).
                pl = w[f"path_low_{n}d"].where(fin, 0.0)
                ph = w[f"path_high_{n}d"].where(fin, 0.0)
                w[f"_d{n}"] = (fin & (pl < -w[f"thr_{n}"])).astype("int64")
                w[f"_u{n}"] = (fin & (ph > w[f"thr_{n}"])).astype("int64")
            else:
                w[f"_d{n}"] = (
                    fin & (col < -w[f"thr_{n}"]).fillna(False)
                ).astype("int64")
                w[f"_u{n}"] = (
                    fin & (col > w[f"thr_{n}"]).fillna(False)
                ).astype("int64")
            agg[f"cnt_{n}"] = (f"_fin{n}", "sum")
            agg[f"s_{n}"] = (f"_c{n}", "sum")
            agg[f"d_{n}"] = (f"_d{n}", "sum")
            agg[f"u_{n}"] = (f"_u{n}", "sum")
            agg[f"thr_{n}"] = (f"thr_{n}", "max")
        g = w.groupby("code", sort=False).agg(**agg).reset_index()

        # ---- per-horizon legs + the weight-blended mixed row (the SQL
        # 04 backfill's blend over the 6dp-rounded legs). The leg tables
        # unwrap to REAL host ndarrays once here (asarray strips the
        # cudf.pandas proxy subclass — a bare to_numpy() result would
        # dispatch np.stack into cupy and reject the host buffers); the
        # weight vector stays 1-D (column-vector broadcasts break the
        # interop — the _dfengine._mixed_blend note).
        import numpy as np

        def _leg(col, dtype="float64") -> np.ndarray:
            return np.asarray(
                host_array(col.to_numpy(dtype=dtype, na_value=np.nan)),
                dtype=dtype,
            )

        w_arr = np.array([MIXED_HORIZON_WEIGHTS[n] for n in FORWARD_HORIZONS])
        leg_frames = []
        for n in FORWARD_HORIZONS:
            leg = pd.DataFrame({"code": g["code"]})
            leg["period"] = PERIOD_FOR_HORIZON[n]
            leg["base_count"] = g[f"cnt_{n}"]
            pos = g[f"cnt_{n}"] > 0
            leg["base_ave_change"] = (
                g[f"s_{n}"] / g[f"cnt_{n}"]).where(pos).round(6)
            leg["base_down_prob"] = (
                g[f"d_{n}"] / g[f"cnt_{n}"]).where(pos).round(6)
            leg["base_up_prob"] = (
                g[f"u_{n}"] / g[f"cnt_{n}"]).where(pos).round(6)
            leg["threshold"] = g[f"thr_{n}"].round(6)
            leg_frames.append(leg)

        ave = np.stack([_leg(lf["base_ave_change"]) for lf in leg_frames], 1)
        down = np.stack([_leg(lf["base_down_prob"]) for lf in leg_frames], 1)
        up = np.stack([_leg(lf["base_up_prob"]) for lf in leg_frames], 1)
        thr = np.stack([_leg(lf["threshold"]) for lf in leg_frames], 1)
        cnt = np.stack([_leg(lf["base_count"], "int64")
                        for lf in leg_frames], 1)
        valid = (ave == ave)
        w_stat = (valid * w_arr).sum(axis=1)
        safe = np.where(w_stat > 0, w_stat, np.nan)
        pos_occ = cnt > 0
        mixed = pd.DataFrame({"code": g["code"]})
        mixed["period"] = PERIOD_MIXED
        mixed["base_count"] = np.where(
            pos_occ.any(axis=1),
            np.where(pos_occ, cnt, np.iinfo(np.int64).max).min(axis=1),
            0,
        ).astype("int64")
        mixed["base_ave_change"] = pd.Series(
            (np.where(valid, np.nan_to_num(ave), 0.0)
             * w_arr).sum(axis=1) / safe).round(6)
        mixed["base_down_prob"] = pd.Series(
            (np.where(valid, np.nan_to_num(down), 0.0)
             * w_arr).sum(axis=1) / safe).round(6)
        mixed["base_up_prob"] = pd.Series(
            (np.where(valid, np.nan_to_num(up), 0.0)
             * w_arr).sum(axis=1) / safe).round(6)
        mixed["threshold"] = pd.Series(
            (thr * w_arr).sum(axis=1)).round(6)

        out = pd.concat(leg_frames + [mixed], ignore_index=True)
        out = out[out["base_count"] > 0]
        if out.empty:
            return []
        # The constant scalars ride the COPY boundary as shared keys —
        # assigning a python-date column into the frame would trip a
        # cudf normalize fallback per partition.
        return _to_records(out, shared={
            "sec_type": self.sec_type,
            "stat_month": spec.stat_month,
            "lookback_period": LOOKBACK_PERIOD,
        })


def compute_base_rate_rows(
    *,
    df: pd.DataFrame,
    first_dates: dict[str, date],
    regimes: pd.DataFrame,
    codes: list[str],
    sec_type: str,
    specs: list,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, base-rate rows) per stat month — one row per
    (code, period) where the count > 0, month-major.

    Args:
        df: the joined long input frame (with forward-change / path
            columns added).
        first_dates: per-code TRUE first data date — the full-window
            live gate.
        regimes: the daily market-regime states (unused by the base
            rates — the unconditional reference carries no hype split;
            consumed for engine-contract parity).
        codes: the sorted active-code universe.
        sec_type: emitted into every row.
        specs: the MonthSpec list to compute.
    """
    engine = _BaseRateEngine(
        df=df,
        first_dates=first_dates,
        regimes=regimes,
        codes=codes,
        sec_type=sec_type,
        specs=specs,
    )
    return engine.run()
