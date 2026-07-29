"""
optimization/engine.py — Profit-maximizing buy/sell optimization engine.

Finds the best (buy, sell) pair in a price series that maximizes profit
subject to:
  • sell occurs after buy
  • holding period (sell_idx - buy_idx) >= min_holding_period
  • buy index is in ``buy_candidates`` (if provided)
  • sell index is in ``sell_candidates`` (if provided)

Cost function: profit = (sell_price - buy_price) / buy_price  (maximize)

The engine is generic: ``buy_candidates`` / ``sell_candidates`` can be any
signal-derived indices (e.g. MA golden-cross / death-cross days) or ``None``
to allow every index.

Algorithm (O(b·log s + s) where b = #buy candidates, s = #sell candidates):
  1. Sort sell_candidates and precompute a right-running max of their prices
     (running_max[i] = max sell price from position i onwards).
  2. For each buy candidate, binary-search the first sell candidate that
     satisfies the holding-period constraint, then look up the best sell
     price from the running-max table.
  3. Track the (buy, sell) pair with the highest profit.

The engine also supports gap-threshold optimization via
``optimize_with_gap_thresholds``: given a price series and a per-bar "gap"
series (e.g. (ma_short - ma_long) / ma_long), it finds the optimal
(buy_threshold, sell_threshold) pair such that buying when gap <=
buy_threshold and selling when gap >= sell_threshold yields the highest
single-trade profit. This implements a mean-reversion strategy (buy
oversold, sell reverted). Algorithm: O(n log n) via a Fenwick tree for
prefix-min price queries indexed by gap rank.

Usage:
    from optimization import OptimizationEngine

    engine = OptimizationEngine(min_holding_period=7, cost_func='profit')
    result = engine.optimize(
        prices=price_array,
        buy_candidates=golden_cross_indices,   # or None for unconstrained
        sell_candidates=death_cross_indices,    # or None for unconstrained
    )
    if result.is_valid:
        print(f"Buy at {result.buy_idx} ({result.buy_price}), "
              f"sell at {result.sell_idx} ({result.sell_price}), "
              f"profit={result.profit:.4f}, holding={result.holding_days}d")

    # Gap-threshold optimization (mean-reversion):
    result = engine.optimize_with_gap_thresholds(prices, gaps)
    if result.is_valid:
        print(f"Buy when gap <= {result.buy_threshold:.4f}, "
              f"sell when gap >= {result.sell_threshold:.4f}")
"""
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np


@dataclass
class OptimizationResult:
    """Result of a single optimization run."""
    buy_idx: Optional[int]
    sell_idx: Optional[int]
    buy_price: Optional[float]
    sell_price: Optional[float]
    profit: Optional[float]       # (sell_price - buy_price) / buy_price
    holding_days: Optional[int]   # sell_idx - buy_idx (in bars)

    @property
    def is_valid(self) -> bool:
        """True if a valid (buy, sell) pair was found."""
        return self.buy_idx is not None and self.sell_idx is not None

    @staticmethod
    def empty() -> "OptimizationResult":
        """An invalid (no-trade) result."""
        return OptimizationResult(None, None, None, None, None, None)


@dataclass
class GapThresholdResult:
    """Result of a gap-threshold optimization run.

    Like OptimizationResult but also carries the optimal buy/sell gap
    thresholds that define the mean-reversion strategy: buy when
    gap <= buy_threshold, sell when gap >= sell_threshold.
    """
    buy_idx: Optional[int]
    sell_idx: Optional[int]
    buy_price: Optional[float]
    sell_price: Optional[float]
    profit: Optional[float]         # (sell_price - buy_price) / buy_price
    holding_days: Optional[int]     # sell_idx - buy_idx (in bars)
    buy_threshold: Optional[float]  # optimal buy gap threshold (= gap at buy day)
    sell_threshold: Optional[float]  # optimal sell gap threshold (= gap at sell day)

    @property
    def is_valid(self) -> bool:
        """True if a valid (buy, sell) pair was found."""
        return self.buy_idx is not None and self.sell_idx is not None

    @staticmethod
    def empty() -> "GapThresholdResult":
        """An invalid (no-trade) result."""
        return GapThresholdResult(None, None, None, None, None, None, None, None)


class OptimizationEngine:
    """Profit-maximizing buy/sell optimizer.

    Parameters
    ----------
    min_holding_period : int
        Minimum number of bars (trading days) between buy and sell.
        Default 7.
    cost_func : str
        Cost function to maximize. Currently only ``'profit'``
        (= fractional return ``(sell-buy)/buy``) is supported.

    Notes
    -----
    The engine maximizes profit, i.e. it finds the (buy, sell) pair with
    the highest fractional return among all candidate pairs that satisfy
    the holding-period constraint. If ``buy_candidates`` or
    ``sell_candidates`` is ``None``, every index is a candidate.
    """

    DEFAULT_MIN_HOLDING_PERIOD = 7
    SUPPORTED_COST_FUNCS = ("profit",)

    def __init__(
        self,
        min_holding_period: int = DEFAULT_MIN_HOLDING_PERIOD,
        cost_func: str = "profit",
    ):
        if min_holding_period < 1:
            raise ValueError(
                f"min_holding_period must be >= 1, got {min_holding_period}"
            )
        if cost_func not in self.SUPPORTED_COST_FUNCS:
            raise ValueError(
                f"Unsupported cost_func: {cost_func!r}. "
                f"Supported: {self.SUPPORTED_COST_FUNCS}"
            )
        self.min_holding_period = min_holding_period
        self.cost_func = cost_func

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def optimize(
        self,
        prices: Sequence[float],
        buy_candidates: Optional[Sequence[int]] = None,
        sell_candidates: Optional[Sequence[int]] = None,
    ) -> OptimizationResult:
        """Find the (buy, sell) pair maximizing profit.

        Parameters
        ----------
        prices : 1D array-like of prices.
        buy_candidates : optional 1D array of indices where buying is
            allowed. If ``None``, all indices are candidates. Typically
            golden-cross signal days.
        sell_candidates : optional 1D array of indices where selling is
            allowed. If ``None``, all indices are candidates. Typically
            death-cross signal days.

        Returns
        -------
        OptimizationResult
            The best (buy, sell) pair, or an invalid result if no pair
            satisfies the constraints.
        """
        prices = np.asarray(prices, dtype=float)
        n = len(prices)
        if n < 2:
            return OptimizationResult.empty()

        # Default candidates: every index
        if buy_candidates is None:
            buy_candidates = np.arange(n)
        else:
            buy_candidates = np.asarray(buy_candidates, dtype=int)
        if sell_candidates is None:
            sell_candidates = np.arange(n)
        else:
            sell_candidates = np.asarray(sell_candidates, dtype=int)

        if len(buy_candidates) == 0 or len(sell_candidates) == 0:
            return OptimizationResult.empty()

        # Sort sell candidates and precompute right-running max of their
        # prices. This lets us answer, for any buy candidate, "what is the
        # max sell price among sell candidates that satisfy the holding
        # constraint?" in O(log s) via binary search.
        sell_candidates = np.sort(sell_candidates)
        sell_prices = prices[sell_candidates]
        running_max, running_max_arg = self._running_max_from_right(sell_prices)

        best_profit = -np.inf
        best_buy_idx: Optional[int] = None
        best_sell_idx: Optional[int] = None

        for b_idx in buy_candidates:
            # Earliest sell index that satisfies the holding-period constraint
            min_sell_idx = int(b_idx) + self.min_holding_period
            pos = int(np.searchsorted(sell_candidates, min_sell_idx))
            if pos >= len(sell_candidates):
                continue  # no sell candidate satisfies the constraint

            max_sell_price = running_max[pos]
            sell_pos = int(running_max_arg[pos])
            sell_idx = int(sell_candidates[sell_pos])

            buy_price = prices[b_idx]
            if not np.isfinite(buy_price) or buy_price <= 0:
                continue
            if not np.isfinite(max_sell_price):
                continue

            profit = (max_sell_price - buy_price) / buy_price
            if profit > best_profit:
                best_profit = profit
                best_buy_idx = int(b_idx)
                best_sell_idx = sell_idx

        if best_buy_idx is None:
            return OptimizationResult.empty()

        return OptimizationResult(
            buy_idx=best_buy_idx,
            sell_idx=best_sell_idx,
            buy_price=float(prices[best_buy_idx]),
            sell_price=float(prices[best_sell_idx]),
            profit=float(best_profit),
            holding_days=best_sell_idx - best_buy_idx,
        )

    # ------------------------------------------------------------------
    # Gap-threshold optimization (mean-reversion strategy)
    # ------------------------------------------------------------------
    def optimize_with_gap_thresholds(
        self,
        prices: Sequence[float],
        gaps: Sequence[float],
        min_holding_period: Optional[int] = None,
    ) -> GapThresholdResult:
        """Find the (buy_threshold, sell_threshold) and (buy, sell) pair
        that maximizes profit, where buy candidates are days with
        ``gap <= buy_threshold`` and sell candidates are days with
        ``gap >= sell_threshold``.

        This implements a mean-reversion strategy: buy when the gap is
        very negative (oversold), sell when it reverts to a higher value.
        The engine searches over ALL possible (buy_threshold,
        sell_threshold) combinations implicitly by observing that the
        optimal thresholds are exactly ``gap[buy_day]`` and
        ``gap[sell_day]`` for the best (buy_day, sell_day) pair with
        ``gap[buy_day] < gap[sell_day]``.

        Parameters
        ----------
        prices : 1D array-like of prices.
        gaps : 1D array-like of gap values (same length as prices).
            E.g. ``(ma_short - ma_long) / ma_long`` or
            ``(price - ma_long) / ma_long``.
        min_holding_period : optional override for the engine's default.

        Returns
        -------
        GapThresholdResult
            The best (buy, sell) pair with optimal thresholds, or an
            invalid result if no pair satisfies the constraints.

        Algorithm (O(n log n)):
          1. Rank days by gap value (same gap → same rank).
          2. Process days in chronological order, inserting each day into
             a Fenwick tree (indexed by gap rank, storing min price) once
             it is far enough back to satisfy the holding-period constraint.
          3. For each sell day, query the Fenwick tree for the minimum
             price among days with strictly smaller gap rank (= strictly
             smaller gap value). This gives the best buy day for that
             sell day.
          4. Track the (buy, sell) pair with the highest profit.
        """
        prices = np.asarray(prices, dtype=float)
        gaps = np.asarray(gaps, dtype=float)
        n = len(prices)
        mhp = min_holding_period if min_holding_period is not None else self.min_holding_period

        if n < 2 or mhp < 1:
            return GapThresholdResult.empty()

        # Valid mask: finite price > 0 and finite gap
        valid = np.isfinite(prices) & np.isfinite(gaps) & (prices > 0)
        valid_idx = np.where(valid)[0]
        m = len(valid_idx)
        if m < mhp + 1:
            return GapThresholdResult.empty()

        valid_prices = prices[valid_idx]
        valid_gaps = gaps[valid_idx]

        # Assign ranks by gap value (same gap → same rank, 0-indexed)
        _, gap_rank = np.unique(valid_gaps, return_inverse=True)
        num_ranks = int(gap_rank.max()) + 1 if m > 0 else 0
        if num_ranks < 2:
            # All gaps are identical — no mean-reversion possible
            return GapThresholdResult.empty()

        # Fenwick tree (1-indexed, size num_ranks) for prefix-min of price.
        # tree_val[i] = min price among inserted days whose rank falls in
        #   the range covered by Fenwick node i.
        # tree_idx[i] = position (into valid_idx) achieving that min price.
        tree_val = np.full(num_ranks + 1, np.inf)
        tree_idx = np.full(num_ranks + 1, -1, dtype=np.int64)

        best_profit = -np.inf
        best_buy_pos = -1
        best_sell_pos = -1

        next_insert = 0  # next position in valid_idx to insert into the tree

        for p in range(m):
            sell_orig = int(valid_idx[p])
            # Insert all days that are now available as buy candidates
            # (original index <= sell_orig - mhp)
            threshold = sell_orig - mhp
            while next_insert < m and int(valid_idx[next_insert]) <= threshold:
                r = int(gap_rank[next_insert]) + 1  # 1-indexed Fenwick position
                v = float(valid_prices[next_insert])
                i = r
                while i <= num_ranks:
                    if v < tree_val[i]:
                        tree_val[i] = v
                        tree_idx[i] = next_insert
                    i += i & (-i)
                next_insert += 1

            # Query: min price among days with gap_rank < gap_rank[p]
            # = 1-indexed prefix query [1, gap_rank[p]]
            q_bound = int(gap_rank[p])  # 1-indexed inclusive upper bound
            q_val = np.inf
            q_idx = -1
            i = q_bound
            while i > 0:
                if tree_val[i] < q_val:
                    q_val = float(tree_val[i])
                    q_idx = int(tree_idx[i])
                i -= i & (-i)

            if q_idx >= 0 and np.isfinite(q_val) and q_val > 0:
                profit = (float(valid_prices[p]) - q_val) / q_val
                if profit > best_profit:
                    best_profit = profit
                    best_buy_pos = q_idx
                    best_sell_pos = p

        if best_buy_pos < 0:
            return GapThresholdResult.empty()

        buy_idx = int(valid_idx[best_buy_pos])
        sell_idx = int(valid_idx[best_sell_pos])

        return GapThresholdResult(
            buy_idx=buy_idx,
            sell_idx=sell_idx,
            buy_price=float(prices[buy_idx]),
            sell_price=float(prices[sell_idx]),
            profit=float(best_profit),
            holding_days=sell_idx - buy_idx,
            buy_threshold=float(gaps[buy_idx]),
            sell_threshold=float(gaps[sell_idx]),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _running_max_from_right(arr: np.ndarray):
        """Compute right-running max of ``arr``.

        Returns ``(running_max, running_max_arg)`` where
        ``running_max[i] = max(arr[i:])`` and ``running_max_arg[i]`` is
        the index into ``arr`` achieving that max.
        """
        m = len(arr)
        running_max = np.empty(m, dtype=float)
        running_max_arg = np.empty(m, dtype=int)
        running_max[-1] = arr[-1]
        running_max_arg[-1] = m - 1
        for i in range(m - 2, -1, -1):
            if arr[i] >= running_max[i + 1]:
                running_max[i] = arr[i]
                running_max_arg[i] = i
            else:
                running_max[i] = running_max[i + 1]
                running_max_arg[i] = running_max_arg[i + 1]
        return running_max, running_max_arg
