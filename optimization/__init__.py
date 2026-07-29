"""
optimization — Buy/sell optimization engine.

Finds the best (buy, sell) pair in a price series that maximizes a cost
function (default: profit = fractional return) subject to constraints
(minimum holding period, signal-derived candidate dates).

Also supports gap-threshold optimization (mean-reversion strategy) via
``OptimizationEngine.optimize_with_gap_thresholds``.
"""
from .engine import OptimizationEngine, OptimizationResult, GapThresholdResult

__all__ = ["OptimizationEngine", "OptimizationResult", "GapThresholdResult"]
