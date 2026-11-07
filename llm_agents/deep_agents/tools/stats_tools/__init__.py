"""llm_agents.deep_agents.tools.stats_tools — the STATS tools: the
most frequently used study calculations as direct ``Tool`` objects, so
a step or a fork gets the standard numbers WITHOUT a sandbox script
round-trip (the sandbox keeps the deep/verification work —
study_kit — for anything beyond these).

The house conventions these tools bake in (mirroring
``analysis.mov_ave_*`` and the sandbox ``study_kit.studies``):

- MA/EMA at the house ladders — ma5/20/60/120/255 and
  ema6/12/20/26/60/120/255 come PRECOMPUTED off the daily view
  (``stats.index_tech_stats``); the 10-day short-term line and any
  other window compute from the bars (2020-01-01 warmup floor);
- RSI is Wilder smoothing (alpha = 1/w) at the six house windows
  3/6/10/14/20/60 (``analysis.mov_ave_rsi``);
- MACD is the classic 12/26/9; momentum is n-day ROC at the analysis
  anchors 5/20 (``analysis.mov_ave_momentum``);
- participation (放量/缩量) is MARKET SHARE vs its own MA — never raw
  yuan (``analysis.mov_ave_trading_amt``'s share-vs-MA gauge);
- every level anchors against the security's OWN trailing 252 trading
  days (percentile rank + z + stretched/neutral verdict), never an
  absolute bar.

Data path: read-replica SELECTs via ``_common.db_commons`` (bars only,
``::float8`` — no pandas in the harness; plain numpy over ≤ ~1700
floats). Package layout:

  bars.py      — the daily-bar fetch + the market-share denominator;
  calcs.py     — the numpy math (Wilder RSI, EMA recursion, tail
                 ranks/z, verdict bands, alignment);
  report.py    — the shared result-line shaping;
  trend.py     — StatsMaTool (``stats_ma``);
  momentum.py  — StatsRsiTool / StatsMacdTool / StatsRocTool;
  flow.py      — StatsVolumeTool / StatsRangeTool;
  kit.py       — StatsToolSet (what the runner/hooks mount).

The DEFAULT_DESCRIPTION constants seed the tools' ``llm.ai_prompts``
rows (prompt_type 4, sub_type 'stats'); a kit built with the run's
descriptions map overrides them fail-soft.
"""
from llm_agents.deep_agents.tools.stats_tools.flow import (
    StatsRangeTool, StatsVolumeTool,
)
from llm_agents.deep_agents.tools.stats_tools.kit import StatsToolSet
from llm_agents.deep_agents.tools.stats_tools.momentum import (
    StatsMacdTool, StatsRocTool, StatsRsiTool,
)
from llm_agents.deep_agents.tools.stats_tools.trend import StatsMaTool

__all__ = [
    "StatsMacdTool", "StatsMaTool", "StatsRangeTool", "StatsRocTool",
    "StatsRsiTool", "StatsToolSet", "StatsVolumeTool",
]
