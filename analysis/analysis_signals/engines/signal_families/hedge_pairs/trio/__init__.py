"""The hp_entry_trio co-wash sub — the portfolio family's ONE shipped
market-wide resonance condition (both RSI windows' at-least tails
unioned into one cell), plus its per-window tail pre-selection, the
blow-off mirror check and the event witnesses. The sub's methods are
MERGED INTO the signal base class: TrioSub composes this package's
mixins and HedgePairTrioEntry inherits it."""
from analysis.analysis_signals.engines.signal_families.hedge_pairs.trio.entry import (
    TrioEntryMixin,
)
from analysis.analysis_signals.engines.signal_families.hedge_pairs.trio.mirror import (
    TrioMirrorMixin,
)
from analysis.analysis_signals.engines.signal_families.hedge_pairs.trio.witnesses import (
    TrioWitnessesMixin,
)


class TrioSub(TrioEntryMixin, TrioMirrorMixin, TrioWitnessesMixin):
    """The trio sub's behaviour — run + tail pre-selection + the
    measured mirror check + the event witnesses — as one mixin of
    HedgePairTrioEntry."""


__all__ = ["TrioSub", "TrioEntryMixin", "TrioMirrorMixin",
           "TrioWitnessesMixin"]
