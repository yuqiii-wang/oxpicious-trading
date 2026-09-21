"""builds.market_regimes — daily market-REGIME state build (ETF+Index+Stock).

Populates stats.market_regimes (see
database/sql/stats/18_market_regimes.sql): one row per (sec_type,
code, TRADING date) — the DAILY 4-state regime label (calm / hot /
panic / quiet) derived from two no-look-ahead trailing legs (the px_vol
log_level convention): vol_z = z of the code's EWMA realized
return-volatility vs its own trailing moments, amt_z = z of
log(trading_amount) vs its own trailing moments — both SHIFTED 1 row.
The same run also MATERIALIZES the derived stats.market_regime_spans
shading table (one row per CONTIGUOUS same-regime run — the former
per-query VIEW the /api/analysis/market-regimes endpoint reads).
REPLACES builds.market_hypes (the boolean episode detector over the
dropped stats.mov_ave_market_hypes).

Phase-A study basis: docs/market_regimes_study.md + temp_scripts/
study_market_regimes_weights.py (index mov_rsi/mov_std: the regime
split carries materially different forward behavior — reversal means
5-9x stronger in hot/panic than calm; the taxonomy's bars are the
study's chosen calibration).

Module layout (cross_stats convention):
  config.py  — pure constants (tables, detector params, source SQL)
  fetch.py   — DB reads only (per-sec_type source)
  compute.py — pure pandas/cudf compute (features -> label -> spans)
  runner.py  — orchestration + DELETE-scope / chunked COPY writes
               (registry + derived spans)
  __main__.py — thin CLI (--force / --sec-type / --code)

Rebuild semantics (margin_changes precedent): trailing-window stats
never change past rows, but ETF adj_close back-adjustments rewrite
price history, so every run DELETEs its scope (one sec_type, or one
--code) and recomputes ALL rows from the FULL per-code history;
--force additionally truncates both tables upfront.
"""
# cudf.pandas activation — must run before pandas first import (the
# config / fetch / compute / runner submodules import pandas at module
# scope; importing this package activates the hook first).
from _common.df_utils._activate import activate

activate()

from builds.market_regimes.config import (  # noqa: E402
    MARKET_REGIME_SPANS_COLUMNS,
    MARKET_REGIMES_COLUMNS,
    MARKET_REGIMES_DESCRIPTION,
    REGIMES,
    SPANS_TABLE,
    TABLE,
)
from builds.market_regimes.runner import (  # noqa: E402
    run_build,
    run_market_regimes,
)

__all__ = [
    "MARKET_REGIME_SPANS_COLUMNS",
    "MARKET_REGIMES_COLUMNS",
    "MARKET_REGIMES_DESCRIPTION",
    "REGIMES",
    "SPANS_TABLE",
    "TABLE",
    "run_build",
    "run_market_regimes",
]
