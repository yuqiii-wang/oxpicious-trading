"""Internal strategy risk analytics package.

Computes risk metrics (chronological concentration, exponential risk score,
per-period gain/loss distributions) from strategy.trade_decision history.

Public entry point: ``RiskRunner`` (the former ``compute_and_upsert_risks()``
orchestrator). Called by ``strategy.singleton_trading`` after each backtest
run so risks are always fresh — there is no standalone
``python -m strategy._risks`` entry point.

Risk philosophy:
  If most gains/losses are concentrated in a short period, risk INCREASES
  EXPONENTIALLY (regime-dependent behavior, higher ruin probability).
  If spread evenly across time, risk DROPS (consistent across regimes).

Module layout:
  - ``constants``  — risk-specific tables + thresholds
  - ``periods``    — year/season/month label helpers
  - ``compute``    — pure-pandas concentration / drawdown / risk_seq /
    risk_period computations (no DB)
  - ``upsert``     — DB write wrappers for the risk tables
  - ``runner``     — conn-scoped orchestrator ``RiskRunner`` (the former
    ``compute_and_upsert_risks``)
  - ``__init__``   — re-exports the public surface
"""
from __future__ import annotations

# Re-export the public compute + upsert surface so callers can import
# everything from ``strategy._risks`` without reaching into submodules.
from strategy._risks.compute import (  # noqa: F401
    compute_risk_seq, compute_risk_periods,
)
from strategy._risks.upsert import (  # noqa: F401
    upsert_risk_seq, upsert_risk_periods, upsert_risk_factors,
)
from strategy._risks.runner import RiskRunner  # noqa: F401

__all__ = [
    "RiskRunner",
    "compute_risk_seq",
    "compute_risk_periods",
    "upsert_risk_seq",
    "upsert_risk_periods",
    "upsert_risk_factors",
]
