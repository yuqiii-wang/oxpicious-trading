"""The OHLCV-boost package (analysis.analysis_signals.engines.
_ohlcv_boost, 2026-10-27 — the class OOP restructure of the flat
engines/variants.py + the engines/boost.py consolidation): the two
collaborators of the is_ohlcv_boosted / boost axis the family
engines and rule_base import.

  OhlcvBoostGate   the VARIANT gate (engines._variant_base.
                   VariantGate subclass): the turnover-climax-
                   confirmed population beside the plain rows on the
                   is_ohlcv_boosted PK axis — returned to generation
                   2026-10-26 (user-directed "create shared pe and
                   ohlcv logic ... use them where possible or
                   relevant"), each variant judged independently by
                   the same measured gate
  OhlcvBoost       the TRUST-MEASUREMENT collaborator (the day-0
                   anatomy trust score, measurement.py): never a
                   gate, never adds/reweights history rows — the
                   per-run derivation audit only (the plain rows are
                   the population of record)
"""
from ._gate import OhlcvBoostGate
from .measurement import OhlcvBoost

__all__ = ["OhlcvBoostGate", "OhlcvBoost"]
