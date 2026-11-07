"""llm_agents.deep_agents.tools.thread_search — the
``search_thread_context`` tool OBJECT.

The run-chain LLM-traffic search as a :class:`Tool` (since 2026-11-06
the search spans the run's WHOLE CHAIN of graphs — plan → custom →
result — not one thread): the schema carries the teaching, and ``run``
closes over ONE thread id (any member of the chain — the search BODY
resolves the run's every thread from it), so EVERY level of EVERY graph
(main lane or any spawned child) searches the whole run's recorded
traffic by construction. The search BODY (chain resolution, scope
healing, the segmented tsquery) lives in ``planning.thread_search`` —
the planning domain owns its DB reads; this module owns the
model-facing contract.

The DEFAULT_DESCRIPTION is the seed source for the tool's
``llm.ai_prompts`` row (prompt_type 4, sub_type 'plan'); a kit built
with the run's descriptions map overrides it fail-soft.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, Tuple

from llm_agents.deep_agents.planning.thread_search import (
    MAX_ROWS, ThreadSearch,
)
from llm_agents.deep_agents.tools.base import Tool


class ThreadSearchTool(Tool):
    """``search_thread_context`` — keyword-search the WHOLE RUN CHAIN's
    recorded LLM traffic: every agent of every graph the run minted
    (plan → custom → result), any depth."""

    name: ClassVar[str] = "search_thread_context"
    db_key: ClassVar[Tuple[str, str]] = (name, "plan")
    parameters: ClassVar[Dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Free text; e.g. 「布林带 000001」or "
                               "「rsi 查询 结果」. All words must hit "
                               "(AND). Optional ONLY with "
                               "critical_only=true.",
            },
            "max_rows": {
                "type": "integer",
                "description": "Max hits to return (default 10, cap "
                               f"{MAX_ROWS}).",
            },
            "critical_only": {
                "type": "boolean",
                "description": "A MUST CHECK before concluding: list "
                               "the run's CRITICAL DISCOVERIES — the "
                               "payload rows agents flagged as "
                               "changing the run's conclusion (query "
                               "then optional: omitted lists them "
                               "all, newest first).",
            },
        },
    }
    DEFAULT_DESCRIPTION: ClassVar[str] = (
        "Keyword-search the WHOLE RUN's recorded LLM work across its "
        "CHAIN OF GRAPHS — one run = three chained LangGraphs (plan → "
        "custom → result, one thread each), and this tool spans every "
        "thread of the run over TWO full-text lanes: the recorded "
        "REQUESTS (what was ASKED, ANSWERED or FETCHED anywhere in the "
        "run — the planner's proposal, each step and fork, the "
        "reviews and the final answer) and the recorded CONTEXT "
        "PAYLOADS themselves (the plan JSON, step directives and "
        "result notes, fork answers, the graphs' syntheses — matched "
        "over their GIN-indexed tags). Words are AND-ed; Chinese is "
        "segmented automatically — natural keywords work, no "
        "operators needed. Request hits show the request id, the "
        "GRAPH and node/step each belongs to ([plan|node] / "
        "[custom|node] / [result|node]) and snippets of the matched "
        "input and output; context hits show ctx#<id> with the "
        "payload's kind label and snippet; the result header lists "
        "each graph's full thread id. CRITICAL DISCOVERIES are a MUST "
        "CHECK before you conclude: context rows the earlier agents "
        "flagged as findings that change the run's conclusion render "
        "with a CRITICAL mark — call with critical_only=true (query "
        "optional) to list them alone, and weigh every one in your "
        "own reply. HOW TO READ A FULL CONTEXT: search first to "
        "locate the row, then fetch the exact persisted payload with "
        "db_run_query over the context table — SELECT c.node, "
        "l.label AS kind, c.payload FROM llm.deep_agent_contexts c "
        "JOIN static.label_dim l ON l.domain = 'agent_context_kind' "
        "AND l.code = c.kind WHERE c.thread_id = '<the graph's thread "
        "id from the header>' (kind labels: plan = the plan JSON, "
        "step-input/step-result = a step's directive/result note, "
        "graph-output = the custom graph's synthesis, final-result = "
        "the run's answer). USE IT before redoing work an earlier "
        "graph, step or fork may already have finished (a fetched "
        "table, a computed number, a concluded judgment), and when "
        "your node needs evidence or the plan contract an earlier "
        "graph's conversation saw.")

    def __init__(self, thread_id: str, *,
                 description: Any = None) -> None:
        super().__init__(
            description=str(description) if description else None)
        self._thread_id = thread_id
        # The search BODY as the bound object it became (2026-11-07).
        self._search = ThreadSearch(thread_id)

    async def run(self, arguments: Dict[str, Any]) -> str:
        try:
            return await self._search.search(
                str(arguments.get("query") or ""),
                int(arguments.get("max_rows") or 10),
                critical_only=arguments.get("critical_only") is True)
        except Exception as exc:  # noqa: BLE001 — tool text, not a crash
            return (f"{self.name} error: {type(exc).__name__}: {exc}")
