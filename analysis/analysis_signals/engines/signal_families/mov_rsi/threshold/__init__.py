"""mov_rsi THRESHOLD emission (mov_rsi.threshold) — the discrete
{1,2,3}% percentile-bucket path: strategy_rows (one signal_thresholds
row per gate-passing bucket) + history_rows (the snapshot-owned
trigger days). Migrated verbatim from the family root 2026-10-05 when
the family split into its threshold/ and curve/ subpackages (the
bucket semantics unchanged — _buckets stays the shared machinery)."""
from analysis.analysis_signals.engines.signal_families.mov_rsi.threshold._history import (  # noqa: E501
    history_rows,
)
from analysis.analysis_signals.engines.signal_families.mov_rsi.threshold._strategies import (  # noqa: E501
    strategy_rows,
)

__all__ = ["strategy_rows", "history_rows"]
