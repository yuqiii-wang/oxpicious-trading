"""The registered standalone cells — the mov_rsi family's own window x
tail vocabulary measured on the COMPONENT curves (market + both legs)
and registered as REAL mov_rsi rows under the is_in_portfolio TRUE
tier (the sourced layer's own tier; never portfolio rows themselves —
each portfolio rule maps them through its signal_rule_ids). The
method is MERGED INTO the signal base class: StandaloneCellsMixin is
composed into HedgePairTrioEntry (via standalone.StandaloneSub)."""
from analysis.analysis_signals.engines.signal_families.hedge_pairs.standalone.register import (
    StandaloneCellsMixin,
)


class StandaloneSub(StandaloneCellsMixin):
    """The registered standalone cells' behaviour as one mixin of
    HedgePairTrioEntry."""


__all__ = ["StandaloneSub", "StandaloneCellsMixin"]
