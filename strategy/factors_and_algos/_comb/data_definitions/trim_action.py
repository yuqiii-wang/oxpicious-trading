"""Ad-hoc trim action (the strategy's self-proposed partial SELL).

Data carrier only; the pass-A shadow-walk logic that fills it lives in
:mod:`strategy.factors_and_algos._comb.adhoc`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class TrimAction:
    """One planned ad-hoc trim on a (code, trade_date) bar.

    ``conf`` is the injected SELL confidence magnitude (0-100) — the
    engine sells ``conf/100`` of the current position, so ``conf`` IS
    the planned sell fraction × 100. The remaining fields are the
    plan-time evidence carried into the ``__ADHOC__`` reason payload.
    """

    code: str
    trade_date: date
    conf: float
    #: position size at plan time, in lots (total_qty / 100)
    lots: float
    #: the profit-aware cap that was breached (lots)
    cap: float
    #: mark-to-market standing at plan time (total_pnl / capital_deployed)
    pnl_ratio: float
    #: the trailing small-rise that armed the trim (rise_window_rows return)
    r5: float
    #: planned sell fraction (conf / 100)
    sell_frac: float

    def payload(self) -> dict:
        """The ``__ADHOC__`` JSON payload (compact, UI-tooltip-ready)."""
        return {
            "k": "trim",
            "lots": round(self.lots, 3),
            "cap": round(self.cap, 2),
            "pnl": round(self.pnl_ratio, 4),
            "r5": round(self.r5, 4),
            "sell": round(self.sell_frac, 3),
        }
