"""FrameMachinery — the bucket-key-independent frame machinery mixin
of SignalEngine (analyze.analysis_signals.engines._frame).

detect / month_triggers / value_points: the gate-passing strategy set,
the month-owned history trigger days and the bounded value point set —
identical for every family, parameterized only by the engine's
``bucket_keys``. THE gate itself (the plain forecast-results rule) is
_gate_mask.

All frames are cudf.pandas and stay NATIVE-DTYPE ONLY — see _base for
the full contract.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from _common.df_utils import host_array

from analyze.analysis_signals.config import GATE_DIR_AVE_MIN, GATE_REVERSE_PROB_MIN
from analyze.analysis_signals.engines._primitives import SELL_SIDES


class FrameMachinery:
    """detect / month_triggers / value_points — the strategy-set,
    history-set and value-point machinery shared by every family (see
    module docstring). Requires the engine's ``bucket_keys`` identity
    attr."""

    bucket_keys: tuple[str, ...]

    def detect(self, buckets: pd.DataFrame) -> pd.DataFrame:
        """One row per GATE-PASSING bucket: the mixed-row gate fields
        (+ every bucket-level config column — stat_month, the family's
        window/k/k_str keys) + the bucket's WINDOW-END trigger (the
        bar source — trigger arrays are ascending, so the last row IS
        the window-end). The keys of this frame DEFINE which buckets
        exist as strategies — history events only ever come from
        these. No indicator values yet (the gate reads the mixed row
        only)."""
        keys = list(self.bucket_keys)
        trig_cols = ["trig_date", "trig_excess"]
        # every non-key, non-trigger column is bucket-level (constant
        # within a bucket) → first() carries the config + gate fields
        first_cols = [
            c for c in buckets.columns
            if c not in keys and c not in trig_cols
        ]
        grouped = buckets.groupby(keys, sort=False)
        bucket = grouped.first()[first_cols].reset_index().merge(
            grouped.last()[trig_cols].reset_index(),
            on=keys,
            how="inner",
        )
        keep = self._gate_mask(bucket) & bucket["trig_date"].notna()
        return bucket[keep].reset_index(drop=True)

    def month_triggers(
        self,
        buckets: pd.DataFrame,
        passing: pd.DataFrame,
        month: date,
    ) -> pd.DataFrame:
        """The gate-passing strategies' trigger days INSIDE the
        snapshot month (inner join on the bucket keys — a bucket
        without a strategy has no events; one snapshot owns each
        date). Deduplicated on (bucket keys, trig_date): the upstream
        trigger arrays can repeat a date within one bucket, and the
        history PK is exactly (code, sub_type, date, time)."""
        keys = list(self.bucket_keys)
        rows = buckets.merge(passing[keys], on=keys, how="inner")
        if rows.empty:
            # A month with no strategy material (e.g. the snapshot's
            # forecast_results rows are not computed yet) stops here:
            # cudf degrades an empty frame's datetime columns to
            # object, where _in_month's .dt accessors raise.
            return rows.reset_index(drop=True)
        rows = rows[self._in_month(rows["trig_date"], month)]
        rows = rows.dropna(subset=["trig_date"])
        rows = rows.drop_duplicates(subset=keys + ["trig_date"])
        return rows.reset_index(drop=True)

    def value_points(
        self, passing: pd.DataFrame, month_trig: pd.DataFrame,
    ) -> tuple[list[str], list[date]]:
        """The distinct (codes, dates) the emits need values at: the
        strategies' window-end triggers (the bars) + the month-owned
        trigger days (the history signals) — a bounded point set
        (SANCTIONED scalar materialization: the SQL layer takes python
        lists; dates cross as ISO strings via the native strftime)."""
        frames = [f for f in (passing, month_trig) if not f.empty]
        if not frames:
            return [], []
        # host_array unwraps the cudf.pandas proxy ONCE at the
        # pandas→numpy boundary — `.tolist()` on the PROXIED object-dtype
        # ndarray (string codes / strftime output) raises
        # "Unsupported dtype object" and falls back to the slow path per
        # month; the real host ndarray's tolist is instant.
        codes = sorted(set(
            host_array(
                pd.concat([f["code"] for f in frames]).to_numpy()
            ).tolist(),
        ))
        iso = host_array(
            pd.concat(
                [f["trig_date"] for f in frames],
            ).dt.strftime("%Y-%m-%d").to_numpy()
        ).tolist()
        return codes, sorted({date.fromisoformat(str(v)) for v in iso})

    def regime_label(
        self, month_trig: pd.DataFrame, regimes: pd.DataFrame,
    ) -> pd.DataFrame:
        """The month-owned trigger days' market regimes: the
        stats.market_regimes DAY label (calm/hot/panic/quiet) joined by
        (code, date) — one plain merge (the daily states table replaces
        the retired episode spans; no interval join). Days with no
        state row default to 'calm' (the retired boolean's never-hyped
        semantics). Regime rows of codes not in the trigger set simply
        never join."""
        out = month_trig.copy()
        if out.empty or regimes.empty:
            out["regime"] = "calm"
            return out
        out = out.merge(
            regimes[["code", "date", "regime"]],
            left_on=["code", "trig_date"], right_on=["code", "date"],
            how="left", sort=False,
        ).drop(columns=["date"])
        out["regime"] = out["regime"].fillna("calm")
        return out.reset_index(drop=True)

    # ---- private helpers --------------------------------------------------------

    def _gate_mask(self, bucket: pd.DataFrame) -> pd.Series:
        """THE gate (the plain forecast-results rule) on the bucket-level
        frame: the sign-aligned blended mean forward change (dir_ave —
        top/upper negated, bottom/lower as-is) > GATE_DIR_AVE_MIN AND the
        blended reverse_prob > GATE_REVERSE_PROB_MIN. NaN comparisons stay
        False, so buckets without results never pass."""
        dir_sign = pd.Series(1.0, index=bucket.index)
        dir_sign = dir_sign.mask(bucket["side"].isin(SELL_SIDES), -1.0)
        dir_ave = dir_sign * bucket["ave_change"]
        return (dir_ave > GATE_DIR_AVE_MIN) & (
            bucket["reverse_prob"] > GATE_REVERSE_PROB_MIN
        )

    def _in_month(self, dates: pd.Series, month: date) -> pd.Series:
        """The calendar-month membership mask (native .dt accessors —
        never pd.Timestamp comparisons). NaT dates compare False."""
        return (dates.dt.year == month.year) & (dates.dt.month == month.month)
