"""OpProfile — data definition.

Migrated verbatim from :mod:`_common.df_utils._thresholds` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OpProfile:
    """Benchmark constants for one operation type.

    Attributes:
        cpu_rate_sec_per_mrow: pandas seconds per 1M rows x 10 numeric cols.
        gpu_rate_sec_per_mrow: cuDF   seconds per 1M rows x 10 numeric cols.
        fixed_overhead_sec:    cuDF fixed overhead (JIT + launch + context).
    """
    cpu_rate_sec_per_mrow: float
    gpu_rate_sec_per_mrow: float
    fixed_overhead_sec: float
