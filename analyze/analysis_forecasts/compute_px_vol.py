"""px_vol_state bucket monthly aggregation (analysis_forecasts) —
sparse tensor engine.

The recent-day price-change × trading-amount STATE buckets (see
database/sql/analysis/analysis_forecasts/05_px_vol_state.sql and the
2026-09 temp_scripts studies): per stat month's trailing 5-year window
[lo, hi) of the (T, C) wide grid, a (code, date) joins ONE of the
15 speed × volume cells when BOTH legs hold. The categories come from
the analysis.mov_ave_price_vs_amt REGISTRY (the px_vol family's
DATE-LEVEL source of truth, built by analyze.mov_ave_spread) —
scattered via wide.build_px_vol_state_matrices into:

  speed  — (T, C) int8 speed ordinal (PX_VOL_SPEEDS order; -1 = no
           valid state that day)
  vol    — (T, C) int8 vol ordinal (PX_VOL_VOL_STATES order; -1 = none)
  t / z  — (T, C) float64 of the recorded px_t / px_z (the bucket
           mean_t / mean_z config magnitudes)

  px_speed: sharp_up t>2.0 | slow_up 1.26<t<=2.0 | flat -1.29<=t<=1.26
            | slow_dn -2.0<=t<-1.29 | sharp_dn t<-2.0
  vol_state: heavy z>2.0 | normal | shrink z<-0.92

(Thresholding the raw t/z features was superseded by the registry
read — the engines AUDIT against the recorded categories; the
PX_VOL_* constants remain the recorded row parameters AND the audit
bar fetch.assert_price_vs_amt_params enforces before consuming.)

Like the mov_* EVENT buckets the state signals are STREAK-MERGED
(2026-09, the unified bucket-signal pipeline wide.iter_bucket_subsets):
consecutive grid rows holding the SAME (speed, vol) cell collapse into
ONE forecast signal at the run's MID row — the high_low_streaks
mean-mid anchor — the bucket's MEAN run length recorded on
forecast_identities.streak_signal_days, and the bucket split is by PK
member is_market_hyped only. Config axis: k = speed_idx * 3 + state_idx
with PX_VOL_SPEEDS × PX_VOL_VOL_STATES ordering.

Per (side, hype) subset the horizon aggregates reuse
wide.aggregate_horizons_sparse (bincount/reduceat over the sparse
trigger cells) against the code's ADAPTIVE reversal bar
(thresholds: k_n·σ of the window's n-day forward changes) —
top speeds reverse on change < -thr, bottom speeds on change > +thr;
flat rows carry side='flat' and get reverse_prob = NULL (no
directional claim). The config JSONB records the bucket's mean t /
mean z (motivation magnitude, like margin_ratio's mean ratio / mean z).

Yields (stat_month, rows) so __main__ can split each row into the
px_vol_state motivation dicts and the forecast_results result dicts
and write month-major.
"""


from __future__ import annotations

import json

from collections.abc import Iterator
from datetime import date

import numpy as np
import pandas as pd

from analyze.analysis_forecasts._dfengine import WideDfEngine
from analyze.analysis_forecasts.config import (
    PX_VOL_K_SHARP,
    PX_VOL_K_SLOW_DN,
    PX_VOL_K_SLOW_UP,
    PX_VOL_LB_WINDOW,
    PX_VOL_SIGMA_FLOOR,
    PX_VOL_SIGMA_MIN_DAYS,
    PX_VOL_SIGMA_WINDOW,
    PX_VOL_SPEED_SIDE,
    PX_VOL_Z_HEAVY,
    PX_VOL_Z_SHRINK,
)


class _PxVolEngine(WideDfEngine):
    """px_vol_state buckets — the registry AUDIT: the speed × volume
    cells come straight from analysis.mov_ave_price_vs_amt (the
    date-level source of truth, merged onto the window frame by
    (code, date)), never re-derived. Speed decides the side (flat
    carries side 'flat' and a NULL reverse_prob — no directional
    claim). Streak-merged since 2026-09: a state run of consecutive
    days is ONE mid-anchored signal. The bucket's config JSONB records
    the bucket mean px_t / px_z (the calibration evidence)."""

    BUCKET_COLS = ("px_speed", "vol_state")
    MERGE = True

    def __init__(self, *, states_df, **kwargs) -> None:
        super().__init__(**kwargs)
        self.states_df = states_df

    def _extra_window_cols(self) -> list[str]:
        return ["px_speed", "vol_state", "px_t", "px_z"]

    def _prepare(self) -> None:
        super()._prepare()
        # The registry's (code, date) state rows ride the prepared
        # frame; days without a registry row simply have no state (the
        # 5×3 bands are exhaustive over state-valid days).
        self._prepared = self._prepared.merge(
            self.states_df, on=["code", "date"], how="left",
        )

    def family_constants(self) -> dict:
        return {
            "sigma_window": PX_VOL_SIGMA_WINDOW,
            "lb_window": PX_VOL_LB_WINDOW,
            "k_slow_up": float(PX_VOL_K_SLOW_UP),
            "k_slow_dn": float(PX_VOL_K_SLOW_DN),
            "k_sharp": float(PX_VOL_K_SHARP),
            "z_heavy": float(PX_VOL_Z_HEAVY),
            "z_shrink": float(PX_VOL_Z_SHRINK),
            "sigma_floor": float(PX_VOL_SIGMA_FLOOR),
        }

    def emit_signals(self, win: pd.DataFrame) -> Iterator[pd.DataFrame]:
        has = win["px_speed"].notna() & win["vol_state"].notna()
        if not has.any():
            return
        cells = win[has].copy()
        cells["side"] = cells["px_speed"].map(PX_VOL_SPEED_SIDE)
        cells["excess"] = np.nan          # state cells — no scalar bar
        yield self._streak_merge(
            cells, group_cols=["px_speed", "vol_state", "side", "code"],
        )

    def bucket_extras(self, cells, keys):
        """The bucket's config JSONB: mean px_t / px_z of its cells
        (the SQL comment's {mean_t, mean_z})."""
        g = cells.groupby(keys, sort=False).agg(
            mean_t=("px_t", "mean"), mean_z=("px_z", "mean"),
        ).reset_index()
        g["bucket_config"] = [
            json.dumps({"mean_t": round(float(t), 6),
                        "mean_z": round(float(z), 6)})
            for t, z in zip(g["mean_t"].tolist(), g["mean_z"].tolist())
        ]
        return g.drop(columns=["mean_t", "mean_z"])


def compute_px_vol_results(
    *, df, first_dates, episodes, codes, sec_type, specs, states_df,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, px_vol_state bucket rows) per month."""
    engine = _PxVolEngine(
        df=df, first_dates=first_dates, episodes=episodes, codes=codes,
        sec_type=sec_type, specs=specs, states_df=states_df,
    )
    return engine.run()
