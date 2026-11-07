"""InsertRows — data definition.

Migrated verbatim from :mod:`builds.stock.pipeline.writer` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class InsertRows:
    """All row lists needed for the decoupled DB writes.

    basic_stats ownership split: OHLCV columns are owned exclusively by the
    OHLCV pass (write_basic_stats_ohlcv), PE/eps/is_pe_estimated by the PE
    passes (write_pe_only) — neither can clobber the other."""
    ov_rows: list[dict]              # OHLCV-scoped basic_stats rows (NO pe/eps)
    snapshot_pe_rows: list[dict]     # rows whose pe came from daily snapshots
    liq_rows: list[dict]             # liquidity_margin rows (trading_* only)
    n_actual: int                    # rows with snapshot-derived pe
    n_missing: int                   # rows without any pe
