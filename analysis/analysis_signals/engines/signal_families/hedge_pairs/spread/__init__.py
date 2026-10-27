"""The spread subs — the pair's long-short spread curve measured at
its own RSI tails: hp_entry_spread (the convergence entry, BUY) and
hp_exit_spread (the exhaustion-tail exit, SELL), one-buy-one-sell
mirrors of each other. The subs' methods are MERGED INTO the signal
base class: SpreadSub composes this package's mixins and
HedgePairTrioEntry inherits it."""
from analysis.analysis_signals.engines.signal_families.hedge_pairs.spread.entry import (
    SpreadEntryMixin,
)
from analysis.analysis_signals.engines.signal_families.hedge_pairs.spread.exit import (
    SpreadExitMixin,
)
from analysis.analysis_signals.engines.signal_families.hedge_pairs.spread.members import (
    SpreadMembersMixin,
)


class SpreadSub(SpreadEntryMixin, SpreadExitMixin, SpreadMembersMixin):
    """The spread subs' behaviour — the entry run, the exit run and
    the leg member records — as one mixin of HedgePairTrioEntry."""


__all__ = ["SpreadSub", "SpreadEntryMixin", "SpreadExitMixin",
           "SpreadMembersMixin"]
