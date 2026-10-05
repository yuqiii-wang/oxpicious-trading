"""FrameMachinery — the bucket-key-independent frame machinery mixin
of SignalEngine (analyze.analysis_signals.engines._frame).

detect / snapshot_triggers / value_points: the gate-passing strategy
set, the snapshot-owned history trigger days and the bounded value
point set — identical for every family, parameterized only by the
engine's ``bucket_keys``. THE gate itself (the plain mixed-row rule
over the engine's computed delay-0 stats) is _gate_mask. (The delay
ladder's optimal-entry argmax + the ladder-stability floor were
REMOVED 2026-10-05 — a strategy IS its delay-0 profile. The trigger
days' regime labels ride the CELLS frame from the in-engine bucket
computation.)

All frames are cudf.pandas and stay NATIVE-DTYPE ONLY — see _base for
the full contract.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from _common.df_utils import host_array

from analyze.analysis_signals.config import (
    BUY_SAMPLE_FLOOR,
    GATE_DIR_AVE_MIN,
    SELL_SAMPLE_FLOOR,
)
from analyze.analysis_signals.engines._primitives import SELL_SIDE_CODES


class FrameMachinery:
    """detect / snapshot_triggers / value_points — the strategy-set,
    history-set and value-point machinery shared by every family (see
    module docstring). Requires the engine's ``bucket_keys`` identity
    attr."""

    bucket_keys: tuple[str, ...]

    def detect(self, buckets: pd.DataFrame) -> pd.DataFrame:
        """The gate-passing bucket rows (one row per bucket, delay 0):
        the plain gate at the per-side SAMPLE FLOOR. A bucket's
        blended mixed-row dir_ave must clear GATE_DIR_AVE_MIN on at
        least BUY_SAMPLE_FLOOR (buy) / SELL_SAMPLE_FLOOR (sell) of the
        window's qualifying streaks — below it the dir_ave measures
        selection noise, not edge. No indicator values are needed (the
        gate reads the blended stats alone)."""
        if buckets.empty:
            return buckets
        sell = buckets["side"].isin(SELL_SIDE_CODES)
        sample_floor = pd.Series(BUY_SAMPLE_FLOOR, index=buckets.index)
        sample_floor = sample_floor.mask(sell, SELL_SAMPLE_FLOOR)
        keep = self._gate_mask(buckets) & buckets["trig_date"].notna()
        keep = keep & (
            buckets["occurrence_count"].astype("float64") >= sample_floor
        )
        return buckets[keep].reset_index(drop=True)

    def snapshot_triggers(
        self,
        buckets: pd.DataFrame,
        passing: pd.DataFrame,
        stat_date: date,
    ) -> pd.DataFrame:
        """The strategies' trigger days INSIDE the snapshot's OWNERSHIP
        INTERVAL — its CALENDAR YEAR (the 1-year-stride rule: a year-end
        snapshot owns year Y; the ROLLING LATEST snapshot owns [Jan 1,
        its key] — the key IS the latest data date, so no trigger can
        exist beyond it, and year equality picks exactly the owned days
        for both key kinds; one snapshot owns each date → no
        cross-snapshot PK conflicts).
        Inner join on the bucket keys + the ``delay`` column — a bucket
        without a strategy has no events (ave_change rides along from
        the strategy row: the history record's confidence denormalizes
        it). Deduplicated on (bucket keys, trig_date): the trigger
        population can repeat a date within one bucket, and the
        history PK is exactly (code, sub_type, date, time)."""
        keys = list(self.bucket_keys)
        rows = buckets.merge(
            passing[keys + ["delay", "ave_change"]],
            on=keys + ["delay"], how="inner",
        )
        if rows.empty:
            # A snapshot with no strategy material stops here: cudf
            # degrades an empty frame's datetime columns to object,
            # where _in_snapshot_year's .dt accessors raise.
            return rows.reset_index(drop=True)
        rows = rows[self._in_snapshot_year(rows["trig_date"], stat_date)]
        rows = rows.dropna(subset=["trig_date"])
        rows = rows.drop_duplicates(subset=keys + ["trig_date"])
        return rows.reset_index(drop=True)

    def value_points(
        self, passing: pd.DataFrame, snapshot_trig: pd.DataFrame,
    ) -> tuple[list[str], list[date]]:
        """The distinct (codes, dates) the emits need values at: the
        strategies' window-end triggers (the bars) + the snapshot-owned
        trigger days (the history signals) — a bounded point set
        (SANCTIONED scalar materialization: the SQL layer takes python
        lists; dates cross as ISO strings via the native strftime)."""
        frames = [f for f in (passing, snapshot_trig) if not f.empty]
        if not frames:
            return [], []
        # host_array unwraps the cudf.pandas proxy ONCE at the
        # pandas→numpy boundary — `.tolist()` on the PROXIED object-dtype
        # ndarray (string codes / strftime output) raises
        # "Unsupported dtype object" and falls back to the slow path per
        # snapshot; the real host ndarray's tolist is instant.
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

    # ---- private helpers --------------------------------------------------------

    def _gate_mask(self, bucket: pd.DataFrame) -> pd.Series:
        """THE gate (the plain mixed-row rule) on the bucket-level
        frame: the sign-aligned blended mean forward change (dir_ave —
        top negated, bottom as-is) > GATE_DIR_AVE_MIN. NaN comparisons
        stay False, so buckets without stats never pass."""
        dir_sign = pd.Series(1.0, index=bucket.index)
        dir_sign = dir_sign.mask(bucket["side"].isin(SELL_SIDE_CODES), -1.0)
        dir_ave = dir_sign * bucket["ave_change"]
        return dir_ave > GATE_DIR_AVE_MIN

    def _in_snapshot_year(self, dates: pd.Series, stat_date: date) -> pd.Series:
        """The snapshot-year membership mask (native .dt accessors —
        never pd.Timestamp comparisons): the trigger day falls in the
        snapshot key's CALENDAR YEAR (the 1-year-stride ownership rule
        — see snapshot_triggers). NaT dates compare False."""
        return dates.dt.year == stat_date.year
