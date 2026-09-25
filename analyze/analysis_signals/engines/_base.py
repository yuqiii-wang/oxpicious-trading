"""SignalEngine — the per-family engine base (identity + phases +
emission + the run entry) (analyze.analysis_signals.engines._base).

One engine class per signal family (one package under
engines/signal_families/ per family — mov_rsi, mov_std, ...),
dispatched through the registry (engines/__init__ — never if/else'd
over). The class is THE integration point: ONE object owns the
family's complete lifecycle, implemented as four single-purpose
layers mixed into this ABC:

  engines/_months.py      SnapshotSelection — resolve_snapshots: which
                          stat_dates to (re-)emit (the incremental
                          snapshot selection) + sweep_stale_snapshots:
                          the retired rolling-latest keys' purge.
  engines/_store.py       SignalStore — write_snapshot / purge_sec_type:
                          the transactional writes (ONE transaction per
                          (sec_type, signal_type, stat_date): purge the
                          snapshot, COPY both tables) + the --force
                          whole-family purge.
  engines/_frame.py       FrameMachinery — detect / snapshot_triggers /
                          value_points: the gate-passing strategy set,
                          the snapshot-owned history trigger days and
                          the bounded value point set.
  engines/_primitives.py  FramePrimitives — action_of / frame_records /
                          (+ the side→action constants):
                          the shared vectorized primitives the
                          machinery AND the family builds use.

This module keeps the class's own core:

  identity   — class attrs naming the family everywhere it is stored:
               signal_strategies.signal_type, forecast_identities.bucket,
               the --metrics stage key and the analysis.analysis_identity
               row (upserted by run).
  phases     — the family's THREE abstract data phases per (sec_type,
               stat_date):
                 fetch_buckets — the family's forecast buckets for the
                                 snapshot key (LONG frame: one row per
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
                                 explode the snapshot-owned trigger days into
                                 history rows, and materialize the write
                                 records at the COPY boundary.
  emission   — emit_snapshot: the template method chaining the phases
               for one (sec_type, stat_date) (fetch_buckets → detect →
               the FINAL SignalQuality gate → snapshot_triggers → the
               trigger-point regime fetch → regime_label →
               value_points → fetch_values → build); only
               quality-passing strategies register and own history
               events.
  run        — the per-sec_type entry point: --force purge → the
               stale-key sweep → the incremental snapshot loop → the
               family log line → the identity upsert (returns
               RunStats). The caller (run/pipeline) refreshes is_active
               + signal_order ONCE after every family ran.

Every frame is cudf.pandas and stays NATIVE-DTYPE ONLY (numerics,
strings, datetime64): no object columns, no datetime64[D] casts, no
pd.Timestamp comparisons — those are the known cudf-unsupported fast
paths that poison frames until transfers block. Per-snapshot constants
(sec_type / signal_type / period bounds / times) are attached to the
record dicts at the COPY boundary, never streamed through frames.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import date
from typing import NamedTuple

import pandas as pd

from _common.df_utils import host_array

from analyze._common import upsert_analysis_identity
from analyze.analysis_forecasts.fetch import fetch_market_regimes_at
from analyze.analysis_signals.engines._frame import FrameMachinery
from analyze.analysis_signals.engines._months import SnapshotSelection
from analyze.analysis_signals.engines._primitives import FramePrimitives
from analyze.analysis_signals.engines._store import SignalStore
from analyze.analysis_signals.engines._quality import SignalQuality

logger = logging.getLogger(__name__)


def _trig_dates_to_python(snap_trig: pd.DataFrame) -> list[date]:
    """The snapshot-owned trigger days as python dates — the SANCTIONED
    scalar materialization (the SQL layer takes python date lists; the
    same host_array idiom as FrameMachinery.value_points, so the
    cudf.pandas proxy never reaches .tolist())."""
    iso = host_array(
        snap_trig["trig_date"].dt.strftime("%Y-%m-%d").to_numpy(),
    ).tolist()
    return sorted({date.fromisoformat(str(v)) for v in iso})


class RunStats(NamedTuple):
    """One engine.run() result: how many stat_dates were (re-)emitted
    and the row counts landed in the two tables."""

    months: int
    strategies: int
    history: int


class SignalEngine(
    SnapshotSelection, SignalStore, FrameMachinery, FramePrimitives, ABC,
):
    """One signal family's COMPLETE engine (registry-dispatched — never
    if/else'd over): the family's identity + abstract data phases, the
    emission template and the run entry, over the snapshot-selection /
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
    # pass this (see engines._quality and emit_snapshot).
    quality: SignalQuality = SignalQuality()

    # ---- phases (the family contract — abstract) ----------------------------

    @abstractmethod
    async def fetch_buckets(
        self, conn, sec_type: str, stat_date: date,
    ) -> pd.DataFrame:
        """The family's forecast buckets for the snapshot key (long
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
        stat_date: date,
        passing: pd.DataFrame,
        snap_trig: pd.DataFrame,
        values: pd.DataFrame,
    ) -> tuple[list[dict], list[dict]]:
        """(strategy records, history records) for the snapshot from the
        gate-passing buckets, their snapshot-owned triggers and the
        values at the selected points — fully vectorized; the returned
        dicts are the COPY-boundary rows."""

    # ---- emission + lifecycle -------------------------------------------------

    async def emit_snapshot(
        self, conn, sec_type: str, stat_date: date,
    ) -> tuple[list[dict], list[dict]]:
        """The emission template for one (sec_type, stat_date): the
        buckets (long) + the snapshot-owned trigger days + the
        indicator values at exactly the trigger points (wide) through
        the machinery into build — returns (strategy records, history
        records). detect's plain gate yields the candidate strategies;
        ONLY those that ALSO pass the final SignalQuality gate register
        (and own history events). The regime labels are fetched AT the
        trigger dates only (fetch_market_regimes_at) — a since-bounded
        fetch would drag the whole 10-year window's (code, date) rows
        for the handful of labels the snapshot-owned triggers need."""
        buckets = await self.fetch_buckets(conn, sec_type, stat_date)
        passing = self.detect(buckets)
        passing = self.quality.gate(passing)
        snap_trig = self.snapshot_triggers(buckets, passing, stat_date)
        if snap_trig.empty:
            regimes = pd.DataFrame()
        else:
            regimes = await fetch_market_regimes_at(
                conn, sec_type, _trig_dates_to_python(snap_trig),
            )
        snap_trig = self.regime_label(snap_trig, regimes)
        codes, dates = self.value_points(passing, snap_trig)
        values = await self.fetch_values(conn, sec_type, codes, dates)
        return self.build(sec_type, stat_date, passing, snap_trig, values)

    async def run(
        self,
        conn,
        sec_type: str,
        *,
        force: bool = False,
        months: int | None = None,
    ) -> RunStats:
        """The family's per-sec_type entry point: --force purge, the
        stale-key sweep (retired rolling-latest keys), the incremental
        snapshot loop (emit_snapshot + write_snapshot per snapshot),
        the family log line and the analysis_identity upsert. The
        caller (run/pipeline) refreshes is_active + signal_order ONCE
        after every family ran."""
        if force:
            await self.purge_sec_type(conn, sec_type)
        n_stale = await self.sweep_stale_snapshots(conn, sec_type)
        target_snapshots = await self.resolve_snapshots(
            conn, sec_type, force, months,
        )
        n_strategies = n_history = 0
        for stat_date in target_snapshots:
            strategies, history = await self.emit_snapshot(
                conn, sec_type, stat_date,
            )
            s, h = await self.write_snapshot(
                conn, sec_type, stat_date, strategies, history,
            )
            n_strategies += s
            n_history += h
        if target_snapshots or force or n_stale:
            logger.info(
                "  [%s] %s: %d snapshots (%d stale swept) → "
                "%d strategies + %d history rows",
                sec_type, self.signal_type, len(target_snapshots),
                n_stale, n_strategies, n_history,
            )
        if n_strategies or force:
            await upsert_analysis_identity(
                conn, name=self.identity_name,
                detail_name=self.identity_name,
                description=self.identity_description,
            )
        return RunStats(len(target_snapshots), n_strategies, n_history)
