"""llm_agents.llm_ask.core.models — Normalized result models.

Provider-independent dataclasses used by every provider and the CLI,
mirroring the models layer of ``llm_agents.online_search_summary.core.
models`` minus the search vocabulary — this package sends plain LLM chat
completions only, so there are no hits, no citations, and no search
options. Nothing here touches the network or the DB.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from llm_agents._core.models import SHANGHAI_TZ

from llm_agents.llm_ask.core.data_definitions import (
    AskOptions,
    AskResult,
    ChatCompletion,
)
# Shared "today" reference for prompt anchors — the corpus and the market
# calendar it serves are Asia/Shanghai (re-export of the llm_agents._core
# constant).
