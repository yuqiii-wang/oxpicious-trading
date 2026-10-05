"""SignalEngine — the per-family engine base (identity + phases +
emission + the run entry) (analyze.analysis_signals.engines._base).

One engine class per signal family (one package under
engines/signal_families/ per family — mov_rsi), dispatched through the
registry (engines/__init__ — never if/else'd over). The class is THE
integration point: ONE object owns the family's complete lifecycle,
implemented as single-purpose layers mixed into this ABC:

  engines/_snapshots.py   SnapshotSelection — resolve_snapshots: which
                          stat_dates to (re-)emit (the incremental
                          snapshot selection over the engine's OWN
                          snapshot grid) + sweep_stale_snapshots: the
                          retired rolling-latest keys' purge.
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
               signal_thresholds.signal_type, the --metrics stage key
               and the analysis.analysis_identity row (upserted by
               run).
  phases     — the family's THREE data phases per sec_type:
                 prepare  — fetch the input frame + first dates +
                            regimes ONCE per run (pure data access);
                            builds the BucketContext the snapshots
                            compute from.
                 buckets  — one snapshot's IN-ENGINE rsi-pct bucket
                            frames (rung: the delay-0 mixed stats per
                            bucket; cells: the trigger rows) — the family's
                            detection semantics, fully vectorized
                            cudf.pandas (the forecast layer's tables
                            were REMOVED 2026-10-05; the signals tier
                            computes its own percentile buckets off
                            analysis.mov_ave_rsi + prices).
                 build    — THE family logic, fully vectorized
                            cudf.pandas: sign-align the gate, mask
                            gate-passing buckets into strategy rows,
                            explode the snapshot-owned trigger days
                            into history rows, and materialize the
                            write records at the COPY boundary.
  emission   — emit_snapshot: the template method chaining the phases
               for one (sec_type, stat_date): buckets → detect (the
               plain gate at the sample floor) →
               snapshot_triggers → value_points → values → build.
  run        — the per-sec_type entry point: --force purge → the
               stale-key sweep → the incremental snapshot loop → the
               family log line → the identity upsert (returns
               RunStats). The caller (run/pipeline) refreshes is_active
               ONCE after every family ran.

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

from analyze._common import upsert_analysis_identity
from analyze.analysis_signals.engines._frame import FrameMachinery
from analyze.analysis_signals.engines._snapshots import SnapshotSelection
from analyze.analysis_signals.engines._primitives import FramePrimitives
from analyze.analysis_signals.engines._store import SignalStore

logger = logging.getLogger(__name__)


def _trig_dates_to_python(snap_trig: pd.DataFrame) -> list[date]:
    """The snapshot-owned trigger days as python dates — the SANCTIONED
    scalar materialization (the host_array idiom, so the cudf.pandas
    proxy never reaches .tolist())."""
    from _common.df_utils import host_array

    iso = host_array(
        snap_trig["trig_date"].dt.strftime("%Y-%m-%d").to_numpy(),
    ).tolist()
    return sorted({date.fromisoformat(str(v)) for v in iso})


class RunStats(NamedTuple):
    """One engine.run() result: how many stat_dates were (re-)emitted
    and the row counts landed in the three tables."""

    snapshots: int
    thresholds: int
    history: int
    curves: int = 0


class SignalEngine(
    SnapshotSelection, SignalStore, FrameMachinery, FramePrimitives, ABC,
):
    """One signal family's COMPLETE engine (registry-dispatched — never
    if/else'd over): the family's identity + abstract data phases, the
    emission template and the run entry, over the snapshot-selection /
    store / frame-machinery / primitives mixin layers (see module
    docstring)."""

    signal_type: str          # analysis_signals.signal_thresholds.signal_type
    stage_key: str            # the --metrics stage key
    identity_name: str        # analysis.analysis_identity row name
    identity_description: str
    bucket_keys: tuple[str, ...] = ("code", "window", "side")

    # Whether the family's signals are streak-merged (consecutive
    # qualifying days collapse into one trigger); one-day event
    # families override False.
    streak_signals: bool = True

    # ---- phases (the family contract — abstract) ----------------------------

    @abstractmethod
    async def prepare(
        self, conn, sec_type: str, specs: list,
        codes: frozenset[str] | None = None,
    ) -> None:
        """Fetch the input frame + the per-code first dates + the
        market-regime labels ONCE per run (bounded to the earliest
        target window start) and build the BucketContext the snapshot
        computations read. ``codes`` optionally scopes the universe
        (the study-scope rule)."""

    @abstractmethod
    def buckets(self, spec) -> tuple[pd.DataFrame, pd.DataFrame]:
        """One snapshot's computed bucket frames — (rung, cells): the
        delay-0 mixed stats (ave_change / occurrence_count /
        trig_date / trig_excess per bucket × delay) and the anchor
        rows (bucket keys + delay + date + excess + regime_state)."""

    @abstractmethod
    def values_long(
        self, codes: list[str], dates: list[date],
    ) -> pd.DataFrame:
        """The melted (code, date, window, value) indicator frame at
        exactly the given point set — the bar recovery + history-row
        values."""

    @abstractmethod
    def build(
        self,
        sec_type: str,
        stat_date: date,
        passing: pd.DataFrame,
        snap_trig: pd.DataFrame,
        long: pd.DataFrame,
    ) -> tuple[list[dict], list[dict]]:
        """(threshold records, history records) for the snapshot from
        the gate-passing buckets, their snapshot-owned triggers and the
        values at the selected points — fully vectorized; the returned
        dicts are the COPY-boundary rows."""

    def curves(self, spec) -> list[dict]:
        """The snapshot's signal_curves records — the family's
        continuous depth-response model (mov_rsi curve/). The base
        default emits none (a family without a curve path keeps the
        two-table shape)."""
        return []

    # ---- emission + lifecycle -------------------------------------------------

    async def emit_snapshot(
        self, conn, sec_type: str, spec,
    ) -> tuple[list[dict], list[dict]]:
        """The emission template for one (sec_type, stat_date): the
        in-engine bucket frames through the machinery into build —
        returns the (threshold records, history records). detect's
        plain gate at the robustness floors yields the strategies."""
        rung, cells = self.buckets(spec)
        passing = self.detect(rung)
        snap_trig = self.snapshot_triggers(cells, passing, spec.stat_date)
        codes, dates = self.value_points(passing, snap_trig)
        long = self.values_long(codes, dates)
        return self.build(sec_type, spec.stat_date, passing, snap_trig, long)

    async def run(
        self,
        conn,
        sec_type: str,
        *,
        force: bool = False,
        snapshots: int | None = None,
        codes: frozenset[str] | None = None,
    ) -> RunStats:
        """The family's per-sec_type entry point: --force purge, the
        stale-key sweep (retired rolling-latest keys), the incremental
        snapshot loop (prepare ONCE → emit_snapshot + curves +
        write_snapshot per snapshot), the family log line and the
        analysis_identity upsert. The caller (run/pipeline) refreshes
        is_active ONCE after every family ran."""
        if force:
            await self.purge_sec_type(conn, sec_type)
        n_stale = await self.sweep_stale_snapshots(conn, sec_type)
        target_specs = await self.resolve_snapshots(
            conn, sec_type, force, snapshots,
        )
        n_thresholds = n_history = n_curves = 0
        if target_specs:
            await self.prepare(conn, sec_type, target_specs, codes)
        for spec in target_specs:
            thresholds, history = await self.emit_snapshot(
                conn, sec_type, spec,
            )
            curves = self.curves(spec)
            s, h, cv = await self.write_snapshot(
                conn, sec_type, spec.stat_date, thresholds, history,
                curves,
            )
            n_thresholds += s
            n_history += h
            n_curves += cv
        if target_specs or force or n_stale:
            logger.info(
                "  [%s] %s: %d snapshots (%d stale swept) → "
                "%d thresholds + %d history + %d curve rows",
                sec_type, self.signal_type, len(target_specs), n_stale,
                n_thresholds, n_history, n_curves,
            )
        if n_thresholds or force:
            await upsert_analysis_identity(
                conn, name=self.identity_name,
                detail_name=self.identity_name,
                description=self.identity_description,
            )
        return RunStats(
            len(target_specs), n_thresholds, n_history, n_curves,
        )
