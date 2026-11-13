"""llm_agents.deep_agents.langgraph.data_hints — the MATERIALIZED-DATA
hints lane's shared lookup (2026-11-10): one fail-soft read-only KG
query behind every conversation builder that wants the
load-before-compute block. The step hop wires it through
``StepDeps.get_data_hints`` (runner base); the SPAWN CHILD wires it
through the spawn input hook (2026-11-10 — the spawns do the run's
studying and used to skip the lane entirely: run 532c6a78's momentum
spawn computed RSI/MACD/ROC from raw bars, blind to the materialized
``analysis.mov_ave_rsi`` / ``mov_ave_momentum`` the KG stores).

Presentation lives in ``langgraph.prompts.data_hints_block``; this
module is the LOOKUP only — no rendering, no tool surface. The
kg_search TOOL lane stays the logged/promoted surface; these hits are
read-only (no chain log, no promotion). The FORCED SCOPE (2026-11-11)
rides this lane too: the callers pass the run's ``allowed_tables`` /
``allowed_schemas`` (``kg_tools.table_scope_from_data`` /
``schema_scope_from_data`` over the run data) so the hints never
suggest a table the run's KG surface may not serve.
"""
from __future__ import annotations

import logging
from typing import Collection, Dict, Optional

from llm_agents.deep_agents.limits import run_limits
from llm_agents.kg import KNOWLEDGE_GRAPHS

logger = logging.getLogger("deep_agents.langgraph.data_hints")


async def data_hints_for(directive: str, *,
                         allowed_tables: Optional[Collection[str]] = None,
                         allowed_schemas: Optional[Collection[str]] = None,
                         ) -> Dict[str, str]:
    """*directive* KG-searched over the TTL-cached ns0 graph —
    read-only (``KNOWLEDGE_GRAPHS.table_hits``) — returning at most
    ``data_hints_rows`` analysis tables as a ``{name: one-line
    description}`` map. *allowed_tables* / *allowed_schemas* — the
    run's forced scope (``None`` = no limitation). Fail-soft to {} on
    every failure (an unseeded graph, a DB outage, a no-hit
    directive); 0 rows configured disables the lane."""
    limits = run_limits()
    if limits.data_hints_rows <= 0 or not str(directive).strip():
        return {}
    try:
        return dict(await KNOWLEDGE_GRAPHS.table_hits(
            str(directive), namespace=0, hops=1,
            max_results=limits.data_hints_rows,
            allowed_tables=allowed_tables,
            allowed_schemas=allowed_schemas))
    except Exception as exc:  # noqa: BLE001 — hints never break a hop
        logger.warning("data hints failed for %r: %s: %s", directive,
                       type(exc).__name__, exc)
        return {}
