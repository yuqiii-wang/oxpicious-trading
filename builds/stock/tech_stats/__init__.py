"""builds.stock.tech_stats — Stock technical indicator computation.

Computes MA5/20/60/120/255 + MA5 ratio + EMA6/12/20/26/60/120/255 from
stats.stock_basic_stats.close, plus trading_amt_per_pct_change, storing
results in stats.stock_tech_stats.

The conn-first implementation lives in
:mod:`builds.stock.tech_stats.runner` (OOP batch B code motion): the
former ``run_tech_stats_chunked(conn, …)`` entry became
:class:`TechStatsRunner` (conn in the ctor). This package ``__init__``
re-exports the pandas-using runner module's names for backwards
compatibility — importing this package still installs the cudf.pandas
hook before pandas' first import (the activation lives in runner.py).

Usage:
    # As standalone module:
    python -m builds.stock.tech_stats
    python -m builds.stock.tech_stats --date 2026-08-28   # refresh one date

    # Integrated into builds.stock:
    from builds.stock.tech_stats import TechStatsRunner
    total = await TechStatsRunner(conn).run(force=..., chunk_size=500)
"""
from __future__ import annotations

from builds.stock.tech_stats.runner import (
    SOURCE_TABLE,
    TABLE,
    TechStatsRunner,
)

__all__ = ["TechStatsRunner", "TABLE", "SOURCE_TABLE"]
