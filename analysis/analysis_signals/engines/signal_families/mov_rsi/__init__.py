"""mov_rsi signal-family engine (analysis.analysis_signals.engines.
signal_families.mov_rsi) — re-exports the engine; the implementation
sits in _engine (identity + phases) over the shared _buckets
machinery, with the emission split into threshold/ (the discrete
{1,2,3}% percentile-bucket strategies + history) and curve/ (the
continuous depth-response signal_curves rows)."""
from analysis.analysis_signals.engines.signal_families.mov_rsi._engine import (
    MovRsiEngine,
)

__all__ = ["MovRsiEngine"]
