"""llm_agents._core.models — Shared result-model primitives.

Constants every llm_agents provider/model layer shares; the per-agent
``core.models`` modules re-export them so existing import paths keep
working. Nothing here touches the network or the DB.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

# Shared "today" reference for fallback dates and prompt anchors — the
# corpus and the market calendar it serves are Asia/Shanghai.
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
