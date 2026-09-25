"""FrameMachinery — the bucket-key-independent frame machinery mixin
of SignalEngine (analyze.analysis_signals.engines._frame).

detect / snapshot_triggers / value_points: the gate-passing strategy
set, the snapshot-owned history trigger days and the bounded value
point set —
identical for every family, parameterized only by the engine's
``bucket_keys``. THE gate itself (the plain forecast-results rule) is
_gate_mask; the DELAY-LADDER balance rule (the optimal entry-delay
selection over the gate-passing rungs) is the argmax inside detect.

All frames are cudf.pandas and stay NATIVE-DTYPE ONLY — see _base for
the full contract.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from _common.df_utils import host_array

from analyze.analysis_signals.config import GATE_DIR_AVE_MIN
from analyze.analysis_signals.engines._primitives import SELL_SIDES


class FrameMachinery:
    """detect / snapshot_triggers / value_points — the strategy-set,
    history-set and value-point machinery shared by every family (see
    module docstring). Requires the engine's ``bucket_keys`` identity
    attr."""

    bucket_keys: tuple[str, ...]

    def detect(self, buckets: pd.DataFrame) -> pd.DataFrame:
        """One row per gate-passing bucket, AT ITS OPTIMAL DELAY RUNG:
        the CHOSEN rung's mixed-row gate fields (+ every bucket-level
        config column — stat_date, the family's window/k/k_str keys) +
        that rung's WINDOW-END trigger (the bar source — trigger arrays
        are ascending, so the last row IS the rung's window-end) + the
        rung id as ``delay``.

        The ladder machinery: the bucket read fans the mixed ladder out
        per rung (bucket × delay × trigger day), so the (bucket, delay)
        groupby yields ONE RUNG ROW per (bucket, rung-with-anchors).
        Every rung row faces the plain gate; among the gate-passing
        rungs the OPTIMAL ENTRY DELAY is the balance rule's argmax

            score(d) = sign-aligned dir_ave(d) × occurrence_count(d)

        (the total blended forward move the entry rule captures over
        the window — the opportunity cost of waiting, occurrence_count
        decaying with the rung, balanced against the
        persistence-conditioned return deepening), ties → the smallest
        delay. Buckets with no gate-passing rung never register. No
        indicator values yet (the gate reads the mixed ladder only)."""
        keys = list(self.bucket_keys)
        trig_cols = ["trig_date", "trig_excess"]
        rung_keys = keys + ["delay"]
        # every non-key, non-trigger column is bucket/rung-level
        # (constant within a (bucket, rung)) → first() carries the
        # config + gate fields
        first_cols = [
            c for c in buckets.columns
            if c not in rung_keys and c not in trig_cols
        ]
        grouped = buckets.groupby(rung_keys, sort=False)
        rung = grouped.first()[first_cols].reset_index().merge(
            grouped.last()[trig_cols].reset_index(),
            on=rung_keys,
            how="inner",
        )
        # rung candidacy: THE gate (the rung's own mixed row) on a
        # rung that actually has anchors
        cand = self._gate_mask(rung) & rung["trig_date"].notna()
        # the balance score — NaN on non-candidates so an all-NaN
        # bucket (no gate-passing rung) drops out at the argmax
        dir_sign = pd.Series(1.0, index=rung.index)
        dir_sign = dir_sign.mask(rung["side"].isin(SELL_SIDES), -1.0)
        rung["_score"] = (
            dir_sign * rung["ave_change"]
            * rung["occurrence_count"].astype("float64")
        ).where(cand)
        # the argmax per bucket is VALUE-based (cudf frame order is
        # never trusted): keep the max-score rungs, then the smallest
        # delay among ties. NaN == NaN is False, so buckets whose
        # rungs all missed the gate vanish here.
        best = rung.merge(
            rung.groupby(keys, sort=False).agg(
                _best=("_score", "max"),
            ).reset_index(),
            on=keys, how="inner",
        )
        best = best[best["_score"] == best["_best"]]
        best = best.merge(
            best.groupby(keys, sort=False).agg(
                _first=("delay", "min"),
            ).reset_index(),
            on=keys, how="inner",
        )
        best = best[best["delay"] == best["_first"]]
        return best.drop(columns=["_score", "_best", "_first"]).reset_index(
            drop=True,
        )

    def snapshot_triggers(
        self,
        buckets: pd.DataFrame,
        passing: pd.DataFrame,
        stat_date: date,
    ) -> pd.DataFrame:
        """The strategies' CHOSEN-RUNG trigger days INSIDE the
        snapshot's OWNERSHIP INTERVAL — its CALENDAR YEAR (the
        1-year-stride rule: a year-end snapshot owns year Y; the
        ROLLING LATEST snapshot owns [Jan 1, its key] — the key IS the
        latest data date, so no trigger can exist beyond it, and year
        equality picks exactly the owned days for both key kinds; one
        snapshot owns each date → no cross-snapshot PK conflicts).
        Inner join on the bucket keys + the chosen ``delay`` rung — a
        bucket without a strategy has no events. Deduplicated on
        (bucket keys, trig_date): the upstream trigger arrays can
        repeat a date within one bucket, and the history PK is exactly
        (code, sub_type, date, time)."""
        keys = list(self.bucket_keys)
        rows = buckets.merge(
            passing[keys + ["delay"]], on=keys + ["delay"], how="inner",
        )
        if rows.empty:
            # A snapshot with no strategy material (e.g. its
            # forecast_results rows are not computed yet) stops here:
            # cudf degrades an empty frame's datetime columns to
            # object, where _in_snapshot_year's .dt accessors raise.
            return rows.reset_index(drop=True)
        rows = rows[self._in_snapshot_year(rows["trig_date"], stat_date)]
        rows = rows.dropna(subset=["trig_date"])
        rows = rows.drop_duplicates(subset=keys + ["trig_date"])
        return rows.reset_index(drop=True)

    def value_points(
        self, passing: pd.DataFrame, month_trig: pd.DataFrame,
    ) -> tuple[list[str], list[date]]:
        """The distinct (codes, dates) the emits need values at: the
        strategies' window-end triggers (the bars) + the snapshot-owned
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

    def regime_label(
        self, month_trig: pd.DataFrame, regimes: pd.DataFrame,
    ) -> pd.DataFrame:
        """The snapshot-owned trigger days' market regimes: the
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
        top/upper negated, bottom/lower as-is) > GATE_DIR_AVE_MIN. NaN
        comparisons stay False, so buckets without results never pass."""
        dir_sign = pd.Series(1.0, index=bucket.index)
        dir_sign = dir_sign.mask(bucket["side"].isin(SELL_SIDES), -1.0)
        dir_ave = dir_sign * bucket["ave_change"]
        return dir_ave > GATE_DIR_AVE_MIN

    def _in_snapshot_year(self, dates: pd.Series, stat_date: date) -> pd.Series:
        """The snapshot-year membership mask (native .dt accessors —
        never pd.Timestamp comparisons): the trigger day falls in the
        snapshot key's CALENDAR YEAR (the 1-year-stride ownership rule
        — see snapshot_triggers). NaT dates compare False."""
        return dates.dt.year == stat_date.year
