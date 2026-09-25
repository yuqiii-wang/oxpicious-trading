"""Base-rate annual-snapshot aggregation (analysis_forecasts) — the metric file.

Per stat month's trailing 5-year window and horizon n, the UNCONDITIONAL
forward stats over ALL of each live code's window days with a valid
n-day forward change (vs the buckets' trigger-day subsets): valid-day
count and mean change — the unconditional reference the bucket
ave_change is read against (lift). (The reversal base probabilities
base_down_prob / base_up_prob + their threshold bar were REMOVED
2026-09-25 with the bucket reverse_prob — see config/horizons.py.)

Per horizon the writer emits the 'next'/'5d'/'20d' rows, then ONE
weight-blended 'mixed' row per code (the FIXED-weight blend of the three
horizon rates — weights renormalized over the horizons with valid
stats; base_count the MIN valid leg count) — the unconditional
reference of the bucket rows' blended period='mixed' profile, so the
analysis_signals gate reads bucket and base on the same blended row.

Written to analysis_forecasts.base_rates (PK: sec_type, code,
stat_date, period; NOT registered in forecast_identities — no
forecast_id). Yields (stat_date, rows) snapshot-major.
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
    PERIOD_FOR_HORIZON,
    PERIOD_MIXED,
)

_PERIODS = [PERIOD_FOR_HORIZON[n] for n in FORWARD_HORIZONS] + [PERIOD_MIXED]


class _BaseRateEngine(WideDfEngine):
    """Base rates — no buckets: ``month_rows`` is overridden directly
    (the emit_signals hook stays unused)."""

    def emit_signals(self, win: pd.DataFrame) -> Iterator[pd.DataFrame]:
        return iter(())   # unused — the base-rate family has no buckets

    def month_rows(self, win: pd.DataFrame, spec) -> list[dict]:
        w = win

        # Per-horizon window-day helpers (vectorized; the invalid days
        # contribute exact zeros — the NC0 semantics).
        agg: dict = {}
        for n in FORWARD_HORIZONS:
            col = w[f"next_change_{n}d"]
            fin = _finite_mask(col)
            w[f"_fin{n}"] = fin
            w[f"_c{n}"] = col.where(fin, 0.0)
            agg[f"cnt_{n}"] = (f"_fin{n}", "sum")
            agg[f"s_{n}"] = (f"_c{n}", "sum")
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
            leg_frames.append(leg)

        ave = np.stack([_leg(lf["base_ave_change"]) for lf in leg_frames], 1)
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

        out = pd.concat(leg_frames + [mixed], ignore_index=True)
        out = out[out["base_count"] > 0]
        if out.empty:
            return []
        # The constant scalars ride the COPY boundary as shared keys —
        # assigning a python-date column into the frame would trip a
        # cudf normalize fallback per partition.
        return _to_records(out, shared={
            "sec_type": self.sec_type,
            "stat_date": spec.stat_date,
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
    """Yield (stat_date, base-rate rows) per stat date — one row per
    (code, period) where the count > 0, snapshot-major.

    Args:
        df: the joined long input frame (with forward-change / path
            columns added).
        first_dates: per-code TRUE first data date — the live gate
            (a code joins a snapshot from its own first-data date;
            partial windows contribute their actual days).
        regimes: the daily market-regime states (unused by the base
            rates — the unconditional reference carries no hype split;
            consumed for engine-contract parity).
        codes: the sorted active-code universe.
        sec_type: emitted into every row.
        specs: the StatSpec list to compute.
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
