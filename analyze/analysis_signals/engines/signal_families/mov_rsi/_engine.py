"""MovRsiEngine — the mov_rsi signal-family engine
(analyze.analysis_signals.engines.signal_families.mov_rsi._engine).

THE rsi-pct family (the only one), TWO emission paths over the same
shared machinery (_buckets — percentile ranks / bars, streak-merged
delay-0 triggers, blended forward stats):

  threshold/  the DISCRETE path (the family's original emission):
    per snapshot the in-engine RSI percentile buckets at the
    top/bottom-{1,2,3}% tails, gated at the robustness floors → ONE
    signal_thresholds row per passing bucket over its window period +
    the snapshot-owned trigger days as history_signals rows.
  curve/      the CONTINUOUS path (2026-10-05): the depth-response
    line mu(s) = mu_center + slope * (s - s_center) per (code,
    rsi_window, side, regime_state) over the snapshot's q <= 10% tail
    triggers → analysis_signals.signal_curves rows (no gate — the
    confidence model, not a strategy; see curve/_curves.py).

Emission slice (threshold path): the top/bottom-{1,2,3}% buckets, BOTH
sides, every regime split (regime_state is a signal_thresholds PK
member — each split registers on its own gate pass). No derived
populations (the ohlcv boost/selection cohorts were RETIRED with the
forecast layer 2026-10-05).

Frames stay NATIVE-DTYPE only (see engines._base): the per-snapshot
constants (sec_type / signal_type / period bounds / is_active) attach
to the records at the boundary, never through the frames.
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from analyze.analysis_signals.config import RSI_PCTS_EMIT, RSI_WINDOWS
from analyze.analysis_signals.engines._base import SignalEngine
from analyze.analysis_signals.engines.signal_families.mov_rsi._buckets import (
    BucketContext,
)
from analyze.analysis_signals.engines.signal_families.mov_rsi.curve import (
    curve_records,
)
from analyze.analysis_signals.engines.signal_families.mov_rsi.threshold import (
    history_rows,
    strategy_rows,
)
from analyze.analysis_signals.fetch import (
    fetch_active_codes,
    fetch_first_dates,
    fetch_market_regimes,
    fetch_rsi_codes,
    fetch_rsi_inputs,
)

logger = logging.getLogger(__name__)


class MovRsiEngine(SignalEngine):
    """mov_rsi: RSI top/bottom-{1,2,3}% percentile strategies + history
    + the continuous depth-response curves, computed in-engine off
    analysis.mov_ave_rsi."""

    signal_type = "mov_rsi"
    stage_key = "rsi"
    identity_name = "signals_mov_rsi"
    bucket_keys = ("code", "rsi_window", "pct", "side", "regime_state")
    identity_description = (
        "mov_rsi signal strategies over the RSI percentile (rsi pct) "
        "buckets computed in-engine from analysis.mov_ave_rsi (the "
        "forecast layer was retired 2026-10-05): the top/bottom-{1,2,3}% "
        "buckets whose delay-0 blended mixed-row stats pass the plain "
        "gate (sign-aligned blended mean reversal > 0.75%) at the sample "
        "floor (4 buys / 3 sells of the window's qualifying streaks), "
        "one strategy per bucket over its trailing 10y window period + "
        "the strategy's trigger days (each qualifying streak's first "
        "day) inside its own snapshot year as history signals + the "
        "continuous depth-response curves (signal_curves: the fitted "
        "mu(s) line over the Gaussian depth score per code x window x "
        "side x regime, the 2026-10 depth-response formulation)."
    )

    def __init__(self) -> None:
        self._context: BucketContext | None = None
        self._sec_type: str | None = None

    # ---- phases -------------------------------------------------------------

    async def prepare(
        self, conn, sec_type: str, specs: list,
        codes: frozenset[str] | None = None,
    ) -> None:
        """Fetch the input frame ONCE for the run's target snapshots
        (price + the RSI window columns, bounded to the earliest
        target window start, no upper bound — forward changes need
        post-snapshot prices), the per-code first-data dates (the live
        gate) and the daily market-regime labels; build the
        BucketContext the per-snapshot computations read. ``codes``
        optionally scopes the universe (the study-scope rule: analysis
        runs write the 10 study indices unless explicitly widened)."""
        universe = (await fetch_active_codes(conn, sec_type)) \
            & (await fetch_rsi_codes(conn, sec_type))
        if codes is not None:
            universe &= set(codes)
        codes_sorted = sorted(universe)
        self._sec_type = sec_type
        logger.info(f"  [{sec_type}] mov_rsi universe: "
                    f"{len(codes_sorted):,} active codes with RSI data")
        since = min(s.lower for s in specs)
        first_dates = await fetch_first_dates(
            conn, sec_type, codes_sorted,
        )
        regimes = await fetch_market_regimes(conn, sec_type, since)
        logger.info(f"  [{sec_type}]   {len(regimes):,} daily "
                    f"market-regime rows since {since.isoformat()}")
        df = await fetch_rsi_inputs(conn, sec_type, codes_sorted, since)
        logger.info(f"  [{sec_type}]   {len(df):,} (code, date) input rows")
        if df.empty:
            self._context = BucketContext(df, specs, sec_type)
            return
        df = BucketContext.add_forward_changes(df)
        df = BucketContext.prepare_frame(df, first_dates, regimes)
        self._context = BucketContext(df, specs, sec_type)

    def buckets(self, spec) -> tuple[pd.DataFrame, pd.DataFrame]:
        assert self._context is not None, "prepare() must run first"
        bk = self._context.buckets(
            spec, rsi_windows=RSI_WINDOWS, pcts=RSI_PCTS_EMIT,
        )
        return bk.rung, bk.cells

    def curves(self, spec) -> list[dict]:
        """The snapshot's signal_curves records — the continuous
        depth-response line per (code, rsi_window, side, regime_state)
        over the snapshot's q <= 10% tail (curve/_curves.py)."""
        assert self._context is not None, "prepare() must run first"
        return curve_records(
            self, self._context, spec, self._sec_type, RSI_WINDOWS,
        )

    def values_long(
        self, codes: list[str], dates: list[date],
    ) -> pd.DataFrame:
        assert self._context is not None, "prepare() must run first"
        return self._context.values_long(codes, dates)

    def build(
        self,
        sec_type: str,
        stat_date: date,
        passing: pd.DataFrame,
        snap_trig: pd.DataFrame,
        long: pd.DataFrame,
    ) -> tuple[list[dict], list[dict]]:
        # No value points (no strategies passed the gates) → no merge
        # material at all (the empty values frame carries object-dtype
        # date columns — never merge against it).
        if values_empty(passing, snap_trig, long):
            return [], []
        return (
            strategy_rows(self, sec_type, stat_date, passing, long),
            history_rows(self, sec_type, stat_date, snap_trig, long),
        )


def values_empty(
    passing: pd.DataFrame, snap_trig: pd.DataFrame, long: pd.DataFrame,
) -> bool:
    """The build's no-material guard: nothing passed the gate, or the
    value lookup came back empty (either means no rows to build)."""
    return passing.empty or long.empty

