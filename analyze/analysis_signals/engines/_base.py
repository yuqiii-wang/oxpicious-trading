"""SignalEngine — the per-family engine base (identity + phases +
emission + the run entry) (analyze.analysis_signals.engines._base).

One engine class per signal family (one package under
engines/signal_families/ per family — mov_rsi, mov_std, ...),
dispatched through the registry (engines/__init__ — never if/else'd
over). The class is THE integration point: ONE object owns the
family's complete lifecycle, implemented as four single-purpose
layers mixed into this ABC:

  engines/_months.py      MonthSelection — resolve_months: which
                          stat_months to (re-)emit (the incremental
                          month selection).
  engines/_store.py       SignalStore — write_month / purge_sec_type:
                          the transactional writes (ONE transaction per
                          (sec_type, signal_type, stat_month): purge the
                          month, COPY both tables) + the --force
                          whole-family purge.
  engines/_frame.py       FrameMachinery — detect / month_triggers /
                          value_points: the gate-passing strategy set,
                          the month-owned history trigger days and the
                          bounded value point set.
  engines/_primitives.py  FramePrimitives — action_of / frame_records /
                          window_start (+ the side→action constants):
                          the shared vectorized primitives the
                          machinery AND the family builds use.

This module keeps the class's own core:

  identity   — class attrs naming the family everywhere it is stored:
               signal_strategies.signal_type, forecast_identities.bucket,
               the --metrics stage key and the analysis.analysis_identity
               row (upserted by run).
  phases     — the family's THREE abstract data phases per (sec_type,
               stat_month):
                 fetch_buckets — the family's forecast buckets for the
                                 snapshot month (LONG frame: one row per
                                 bucket × trigger day; bucket-level gate
                                 fields repeated on every trigger row) —
                                 pure data access.
                 fetch_values  — the indicator values at exactly the
                                 (code, date) points the buckets'
                                 triggers name (WIDE frame) — pure data
                                 access.
                 build         — THE family logic, fully vectorized
                                 cudf.pandas (no row loops, no if/else
                                 branches): sign-align the gate, mask
                                 gate-passing buckets into strategy rows,
                                 explode the month-owned trigger days into
                                 history rows, and materialize the write
                                 records at the COPY boundary.
  emission   — emit_month: the template method chaining the phases for
               one month (fetch_buckets → detect → the FINAL
               SignalQuality gate → month_triggers → value_points →
               fetch_values → build); only quality-passing strategies
               register and own history events.
  run        — the per-sec_type entry point: --force purge → the month
               loop → the family log line → the identity upsert
               (returns RunStats). The caller (run/pipeline) refreshes
               is_active + signal_order ONCE after every family ran.

Every frame is cudf.pandas and stays NATIVE-DTYPE ONLY (numerics,
strings, datetime64): no object columns, no datetime64[D] casts, no
pd.Timestamp comparisons — those are the known cudf-unsupported fast
paths that poison frames until transfers block. Per-month constants
(sec_type / signal_type / period bounds / times) are attached to the
record dicts at the COPY boundary, never streamed through frames.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import date
from typing import NamedTuple

import pandas as pd

from analyze._common import upsert_analysis_identity
from analyze.analysis_forecasts.fetch import fetch_market_regimes
from analyze.analysis_signals.engines._frame import FrameMachinery
from analyze.analysis_signals.engines._months import MonthSelection
from analyze.analysis_signals.engines._primitives import FramePrimitives
from analyze.analysis_signals.engines._store import SignalStore
from analyze.analysis_signals.engines._quality import SignalQuality

logger = logging.getLogger(__name__)


class RunStats(NamedTuple):
    """One engine.run() result: how many stat_months were (re-)emitted
    and the row counts landed in the two tables."""

    months: int
    strategies: int
    history: int


class SignalEngine(
    MonthSelection, SignalStore, FrameMachinery, FramePrimitives, ABC,
):
    """One signal family's COMPLETE engine (registry-dispatched — never
    if/else'd over): the family's identity + abstract data phases, the
    emission template and the run entry, over the month-selection /
    store / frame-machinery / primitives mixin layers (see module
    docstring)."""

    signal_type: str          # analysis_signals.signal_strategies.signal_type
    bucket: str               # analysis_forecasts.forecast_identities.bucket
    stage_key: str            # the --metrics stage key
    identity_name: str        # analysis.analysis_identity row name
    identity_description: str
    bucket_keys: tuple[str, ...] = ("code", "window", "side")

    # The FINAL quality gate (shared across families — stateless): the
    # strategies that register are the plain-gate survivors that ALSO
    # pass this (see engines._quality and emit_month).
    quality: SignalQuality = SignalQuality()

    # ---- phases (the family contract — abstract) ----------------------------

    @abstractmethod
    async def fetch_buckets(
        self, conn, sec_type: str, month: date,
    ) -> pd.DataFrame:
        """The family's forecast buckets for the snapshot month (long
        frame: one row per bucket × trigger day)."""

    @abstractmethod
    async def fetch_values(
        self, conn, sec_type: str, codes: list[str], dates: list[date],
    ) -> pd.DataFrame:
        """The indicator values (wide frame) at exactly the (code,
        date) points value_points names."""

    @abstractmethod
    def build(
        self,
        sec_type: str,
        month: date,
        passing: pd.DataFrame,
        month_trig: pd.DataFrame,
        values: pd.DataFrame,
    ) -> tuple[list[dict], list[dict]]:
        """(strategy records, history records) for the month from the
        gate-passing buckets, their month-owned triggers and the
        values at the selected points — fully vectorized; the returned
        dicts are the COPY-boundary rows."""

    # ---- emission + lifecycle -------------------------------------------------

    async def emit_month(
        self, conn, sec_type: str, month: date,
    ) -> tuple[list[dict], list[dict]]:
        """The emission template for one (sec_type, month): the buckets
        (long) + the indicator values at exactly the trigger points
        (wide) + the code's daily regime states (the REUSED forecast-side
        fetcher) through the machinery into build — returns (strategy
        records, history records). detect's plain gate yields the
        candidate strategies; ONLY those that ALSO pass the final
        SignalQuality gate register (and own history events)."""
        buckets = await self.fetch_buckets(conn, sec_type, month)
        passing = self.detect(buckets)
        passing = self.quality.gate(passing)
        month_trig = self.month_triggers(buckets, passing, month)
        regimes = await fetch_market_regimes(
            conn, sec_type, self.window_start(month),
        )
        month_trig = self.regime_label(month_trig, regimes)
        codes, dates = self.value_points(passing, month_trig)
        values = await self.fetch_values(conn, sec_type, codes, dates)
        return self.build(sec_type, month, passing, month_trig, values)

    async def run(
        self,
        conn,
        sec_type: str,
        *,
        force: bool = False,
        months: int | None = None,
    ) -> RunStats:
        """The family's per-sec_type entry point: --force purge, the
        incremental month loop (emit_month + write_month per month),
        the family log line and the analysis_identity upsert. The
        caller (run/pipeline) refreshes is_active + signal_order ONCE
        after every family ran."""
        if force:
            await self.purge_sec_type(conn, sec_type)
        target_months = await self.resolve_months(conn, sec_type, force, months)
        n_strategies = n_history = 0
        for month in target_months:
            strategies, history = await self.emit_month(conn, sec_type, month)
            s, h = await self.write_month(
                conn, sec_type, month, strategies, history,
            )
            n_strategies += s
            n_history += h
        if target_months or force:
            logger.info(
                "  [%s] %s: %d months → %d strategies + %d history rows",
                sec_type, self.signal_type, len(target_months),
                n_strategies, n_history,
            )
        if n_strategies or force:
            await upsert_analysis_identity(
                conn, name=self.identity_name,
                detail_name=self.identity_name,
                description=self.identity_description,
            )
        return RunStats(len(target_months), n_strategies, n_history)
