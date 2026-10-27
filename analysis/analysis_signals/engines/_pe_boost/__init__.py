"""The PE-boost variant package (analysis.analysis_signals.engines.
_pe_boost, 2026-10-27 — the class OOP restructure of the flat
engines/variants.py): the shared is_pe_boosted axis logic the family
engines import. A PE-boosted variant of a rule is a SEPARATE row from
the unconstrained one (the PK axis; 2026-10-26 user-directed) — the
gate class AND-gates the plain candidate masks by the code's own
trailing PE-percentile condition and runs the pass beside the plain
one, judged independently by the same measured gate.

  PeBoostGate        the variant gate (population condition + stored
                     bar fragment + the pass machinery from the
                     shared VariantGate base)
  trailing_pct_rank  the per-code trailing rank (the sanctioned HOST
                     boundary)
  PE_PCT_COL         the shared attached-rank column name
"""
from ._gate import PE_PCT_COL, PeBoostGate, trailing_pct_rank

__all__ = ["PE_PCT_COL", "PeBoostGate", "trailing_pct_rank"]
