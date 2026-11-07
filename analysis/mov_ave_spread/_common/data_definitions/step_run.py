"""StepRun — data definition.

Migrated verbatim from :mod:`analysis.mov_ave_spread._common.steps` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import asyncpg


@dataclass(frozen=True)
class StepRun:
    """Per-call execution context for one TableStep invocation.

    The parent pipeline builds one per ``execute`` call from its own
    lifecycle state (amain()'s conn/pool, apply_args()' derivations) —
    the old run_* keyword soup (``force=force, pool=pool,
    max_concurrent=max_concurrent, sec_type=st, code_filter=code``)
    collapses onto this value. ``sec_type`` stays an ``execute``
    argument: it is the parent loop's per-call scope, not run state.
    """

    conn: asyncpg.Connection
    # mov_ave_spread always opens the pool (pool_size = --max-concurrent).
    pool: asyncpg.Pool
    force: bool
    max_concurrent: int
    # Single-code (--code) mode: recompute ALL dates for this code only.
    code_filter: Optional[str]
    # --backfill: lift the default increment bound (deep backlog too).
    backfill: bool
