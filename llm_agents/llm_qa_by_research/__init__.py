"""llm_agents.llm_qa_by_research — the research-logic Q&A agent package.

Two halves sharing the llm.llm_qa_by_research table (DDL:
database/sql/text/llm/04_llm_qa_by_research.sql):

  * :mod:`.storage`  — the idempotent write/read API (upsert / fetch /
    deactivate) over the row's five-request shape;
  * :mod:`.generation` — ``generate_research_qa``: FIVE separate LLM
    requests per row (intro / return / risks / capitalization dimension
    requests + the 5th summary-and-logic_label request), with the merged
    news-group refs mapping;
  * :mod:`.prompts`   — the dimension registry and prompt/parse contract.

CLI (``python -m llm_agents.llm_qa_by_research``)::

    generate --question "…" [--industry BANKS] [--sector FIN]
             [--model glm-5.3-flash] [--provider zhipu] [--language zh]
             [--count 8] [--recency noLimit] [--category research]
    add --question "…" --answer "…" --logic-label growth […]
    list [--limit 20] [--all] [--industry …] [--sector …] [--logic-label …]
    deactivate --qa-id 42
"""
from __future__ import annotations

from .generation import DimensionResult, generate_research_qa
from .prompts import DIMENSIONS, DimensionSpec, parse_summary_json
from .storage import (
    LOGIC_LABELS, QA_TABLE, RESEARCH_COOLDOWN_DAYS, deactivate_qa_by_research,
    fetch_qa_by_research, industry_quarter_present, set_has_refs,
    upsert_qa_by_research,
)

__all__ = [
    "DIMENSIONS", "DimensionResult", "DimensionSpec", "LOGIC_LABELS",
    "QA_TABLE", "RESEARCH_COOLDOWN_DAYS", "deactivate_qa_by_research",
    "fetch_qa_by_research", "industry_quarter_present",
    "generate_research_qa", "parse_summary_json", "set_has_refs",
    "upsert_qa_by_research",
]
