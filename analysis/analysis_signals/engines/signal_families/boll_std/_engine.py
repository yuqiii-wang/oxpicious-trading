"""BollStdEngine — the boll_std signal-family engine
(analysis.analysis_signals.engines.signal_families.boll_std._engine).

THE Bollinger σ-depth family (NO percentile logic — the mov_rsi
families' pct axis is replaced by the EXCESS σ-DEPTH over the fixed
bands): the value is z = (price − SMA_W)/σ_W, computed IN-ENGINE on the
fetched price frame (boll_std/_compute — the classic trailing
construction + the fixed-0.6745σ episode layer; there is no
analysis-layer boll table since 2026-10-10), the bars are the FIXED
band multipliers k ∈ {2, 2.5, 3}σ (2 the classic default — the rsi14
analog), a breach IS |z| >= k on the episode population, and the breach
excess depth |z| − k is the curve tier's x axis (s = |z|). TWO emission
paths over the same shared machinery (boll_std/_buckets — the mov_rsi
BucketContext engine with the fixed-σ detection substituted):

  threshold/  the DISCRETE path: per snapshot the fixed-σ band-breach
    buckets, streak-merged to delay-0 episode-first triggers, gated at
    the robustness floors → ONE signal_thresholds row per passing
    bucket (the bar the FIXED ±k constant in the z space, sub_type
    boll{W}_{k}std) + the snapshot-owned trigger days as
    history_signals rows.
  curve/      the CONTINUOUS path: the σ-depth response curve(s) =
    mu_center + interp(fit_curve, s) per (code, boll_window, side,
    regime_state) over the snapshot's episode-band days
    (|z| >= 0.6745), episode-weighted 1/size, BOTH pass populations x
    BOTH boost variants → analysis_signals.signal_curves rows (no
    gate — the confidence model, not a strategy).

Emission slice (threshold path): k ∈ {2, 2.5, 3}, BOTH sides, every
regime split (regime_state is a signal_thresholds PK member — each
split registers on its own gate pass). The family imports the GENERAL
ohlcv-boost formula (engines/ohlcv_boost) and stamps its day-0 verdict
as the is_ohlcv_boosted marker on the SAME rows — an analysis flag
that gates nothing (no measured (boll_std, sec_type) config exists
yet, so the verdict stays FALSE until one is calibrated).

Frames stay NATIVE-DTYPE only (see engines._base): the per-snapshot
constants (sec_type / signal_type / period bounds / is_active) attach
to the records at the boundary, never through the frames.
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from analysis.analysis_signals.config import (
    BOLL_STDS_EMIT,
    BOLL_WINDOWS,
)
from analysis.analysis_signals.engines._base import SignalEngine
from analysis.analysis_signals.engines.ohlcv_boost import (
    anchor_scores,
    bucket_flags,
    side_configs,
)
from analysis.analysis_signals.engines.signal_families.boll_std._buckets import (
    BollBucketContext,
)
from analysis.analysis_signals.engines.signal_families.boll_std._compute import (
    add_boll_value_columns,
)
from analysis.analysis_signals.engines.signal_families.boll_std.curve import (
    curve_records,
)
from analysis.analysis_signals.engines.signal_families.boll_std.threshold import (
    history_rows,
    strategy_rows,
)
from analysis.analysis_signals.fetch import (
    fetch_active_codes,
    fetch_boll_inputs,
    fetch_first_dates,
    fetch_index_classes,
    fetch_latest_boll_date,
    fetch_market_regimes,
)
from analysis.analysis_signals.engines.signal_families.mov_rsi._engine import (
    values_empty,
)

logger = logging.getLogger(__name__)


class BollStdEngine(SignalEngine):
    """boll_std: the fixed-σ Bollinger band-breach strategies + history +
    the continuous σ-depth response curves, computed IN-ENGINE off the
    fetched prices (signal_type 'bollinger' in the DB since 2026-10-10;
    the stage/registry key stays 'boll')."""

    signal_type = "bollinger"
    stage_key = "boll"
    identity_name = "signals_bollinger"
    bucket_keys = ("code", "boll_window", "std", "side", "regime_state")
    identity_description = (
        "bollinger signal strategies over the fixed-σ Bollinger band "
        "breaches (no percentile logic — the excess σ-depth over the "
        "bands replaces it), computed in-engine from the fetched prices "
        "(z = (price − SMA_W)/σ_W per window, classic trailing SMA + "
        "population σ + the fixed-0.6745σ episode layer — no "
        "analysis-layer boll table): "
        "the 2 / 2.5 / 3σ buckets (sub_type boll{W}_{k}std — 2σ the "
        "classic default) whose delay-0 blended mixed-row stats pass "
        "the plain gate (sign-aligned blended mean reversal > its "
        "side's bar: buy 0.75% / sell 0%) at the sample floor (4 buys "
        "/ 3 sells of the window's qualifying episodes), one strategy "
        "per bucket over its trailing 10y window period (the bar the "
        "FIXED ±k constant in the z space; the breach excess depth "
        "|z| − k in σ units drives the confidence scale) + the "
        "strategy's trigger days (each qualifying episode's first day) "
        "inside its own snapshot year as history signals + the "
        "continuous σ-depth response curves (signal_curves: the fitted "
        "mu(s) line over the σ-depth s = |z| per code x window x side "
        "x regime, the depth-response formulation the mov_rsi family "
        "established, transplanted onto the band-excess axis)."
    )

    def __init__(self) -> None:
        self._context: BollBucketContext | None = None
        self._sec_type: str | None = None
        # per-code primary classification industry
        # (stats.sec_classification.industry_id) — the curve shape's
        # pooling key (fetched in prepare)
        self.code_class: dict[str, str] = {}

    # ---- phases -------------------------------------------------------------

    async def prepare(
        self, conn, sec_type: str, specs: list,
        codes: frozenset[str] | None = None,
    ) -> None:
        """Fetch the input frame ONCE for the run's target snapshots
        (the price frame over each code's FULL history — the family's
        z + episode columns are computed in-engine right after, since
        the trailing windows and the episode segmentation need the
        whole per-code series), the per-code first-data dates (the live
        gate) and the daily market-regime labels; build the
        BollBucketContext the per-snapshot computations read. ``codes``
        optionally scopes the universe (the study-scope rule)."""
        universe = await fetch_active_codes(conn, sec_type)
        if codes is not None:
            universe &= set(codes)
        codes_sorted = sorted(universe)
        self._sec_type = sec_type
        logger.info(f"  [{sec_type}] bollinger universe: "
                    f"{len(codes_sorted):,} active codes")
        since = min(s.lower for s in specs)
        first_dates = await fetch_first_dates(
            conn, sec_type, codes_sorted,
        )
        regimes = await fetch_market_regimes(conn, sec_type, since)
        logger.info(f"  [{sec_type}]   {len(regimes):,} daily "
                    f"market-regime rows since {since.isoformat()}")
        df = await fetch_boll_inputs(conn, sec_type, codes_sorted)
        logger.info(f"  [{sec_type}]   {len(df):,} (code, date) input rows")
        if df.empty:
            self._context = BollBucketContext(df, specs, sec_type)
            return
        df = add_boll_value_columns(df, sec_type)
        df = BollBucketContext.add_forward_changes(df)
        df = BollBucketContext.prepare_frame(df, first_dates, regimes)
        self._context = BollBucketContext(df, specs, sec_type)
        self.code_class = await fetch_index_classes(
            conn, sec_type, codes_sorted,
        )
        classes = sorted(set(self.code_class.values()))
        logger.info(f"  [{sec_type}]   curve fit pooling classes: "
                    f"{len(classes)} ({', '.join(classes[:8])}"
                    f"{'…' if len(classes) > 8 else ''})")

    def buckets(self, spec) -> tuple[pd.DataFrame, pd.DataFrame]:
        assert self._context is not None, "prepare() must run first"
        bk = self._context.buckets(
            spec, boll_windows=BOLL_WINDOWS, stds=BOLL_STDS_EMIT,
        )
        return self._apply_boost(bk.rung, bk.cells)

    def _apply_boost(
        self,
        rung: pd.DataFrame,
        cells: pd.DataFrame,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Score the day-0 anchors through the GENERAL ohlcv-boost
        formula (engines/ohlcv_boost — the day-0 OHLC pattern +
        (vol_state, px_speed) cell + the σ-depth past the bar, per-side
        measured ladders) and stamp the verdicts on the SAME frames —
        the mov_rsi engine's pattern verbatim. No measured (boll_std,
        sec_type) config exists yet, so anchor_scores keeps the
        unboosted shape (score 0 / weight 1 / flag 0) and the marker
        stays FALSE until one is calibrated. Marker only — the gate,
        the bars and the plain confidences are untouched."""
        keys = list(self.bucket_keys)
        if cells.empty:
            return rung, cells
        cfgs = side_configs(self.signal_type, self._sec_type)
        scored = anchor_scores(
            cells[cells["delay"] == 0], keys, cfgs,
            excess_col="trig_excess",
        )
        cells = cells.merge(
            scored[keys + ["trig_date", "boost_score", "boost_weight",
                           "boost_flag"]],
            on=keys + ["trig_date"], how="left",
        )
        cells["boost_score"] = cells["boost_score"] \
            .fillna(0.0).astype("float64")
        cells["boost_weight"] = cells["boost_weight"] \
            .fillna(1.0).astype("float64")
        cells["boost_flag"] = cells["boost_flag"] \
            .fillna(0).astype("int64")
        flags = bucket_flags(scored, keys, cfgs)
        rung = rung.merge(flags, on=keys, how="left")
        rung["is_ohlcv_boosted"] = rung["is_ohlcv_boosted"] \
            .fillna(0).astype("int64")
        return rung, cells

    def curves(self, spec) -> list[dict]:
        """The snapshot's signal_curves records — the continuous
        σ-depth response per (code, boll_window, side, regime_state)
        over the snapshot's episode band (the boll layer's episode
        sizes — every in-band day, episode-weighted), BOTH population
        variants per cell (the plain + the ohlcv-boost weighted twin;
        curve/_curves.py)."""
        assert self._context is not None, "prepare() must run first"
        return curve_records(
            self, self._context, spec, self._sec_type, BOLL_WINDOWS,
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

    # ---- snapshot grid --------------------------------------------------------

    async def latest_data_date(self, conn, sec_type: str) -> date | None:
        """The family's value-source max date — the ROLLING LATEST
        snapshot key (the sec_type's latest available price-data date,
        the horizon the in-engine z is computed from)."""
        return await fetch_latest_boll_date(conn, sec_type)
