"""llm_agents.deep_agents.tools.kg_tools.kit — ``KgToolSet``, the
knowledge-graph kit the runner mounts on every step conversation and
the delegation hooks stamp on every spawned child: the DB schema's and
the prompt catalog's SEMANTIC index as two direct tools, so an agent
finds the right tables / the right skill by business meaning instead
of listing schemas blind or fetching prompts wholesale (``db_list_tables``
/ ``db_describe_table`` stay the mechanical column layer — the KG is
what tells you WHICH table to describe; ``load_prompt`` stays the
loading lane — the KG is what tells you WHICH skill to load).

Thin adapters by design (2026-11-06 migration): the KG domain lives in
the INDEPENDENT ``llm_agents.kg`` package — the tools carry only the
model-facing schema, the loose-argument coercion, the error formatting
and the agent-flow hints, and delegate the flow (TTL graph load, the
two-layer chain cache, the render) to ``KNOWLEDGE_GRAPHS``, the
process-wide ``KnowledgeGraphService``.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Optional, Tuple

from llm_agents.deep_agents.tools.base import Tool, ToolKit
from llm_agents.kg import NS_SCOPES, KNOWLEDGE_GRAPHS, KgUnavailableError
from llm_agents.kg import graph as kg_graph

_HOPS_SEARCH = {
    "type": "integer", "minimum": 1, "maximum": 3,
    "description": "Spreading-activation hops from the matched nodes "
                   "(default 2: a node's sources/derivations/family).",
}

_NAMESPACE_PARAM = {
    "type": "integer", "enum": [0, 1],
    "description": "Which knowledge-graph namespace to search: 0 = DB "
                   "semantics, one node per table/view (default); 1 = "
                   "the prompt catalog, one node per skill / plan "
                   "prompt (find WHICH skill teaches a domain).",
}

# The agent flow after a hit differs per namespace (ns0 walks into
# columns/data, ns1 loads a skill) — tool-layer vocabulary, kept HERE
# while the scope labels themselves live with the domain (NS_SCOPES).
NS_NEXT_HINTS = {
    0: ("next: kg_expand a table above for its full neighborhood; "
        "db_describe_table / db_run_query for columns and data."),
    1: ("next: kg_expand a prompt above for its related prompts; load a "
        "found skill with load_prompt when its key is in your [Prompt "
        "catalog] block (loading stays run-scoped)."),
}


def _namespace(arguments: Dict[str, Any]) -> int:
    """The validated namespace argument (0/1; default 0)."""
    value = arguments.get("namespace", 0)
    try:
        return 0 if value is None else int(value)
    except (TypeError, ValueError):
        return 0


def _int_arg(arguments: Dict[str, Any], key: str,
             default: int) -> int:
    """The coerced integer argument (loose model JSON → default)."""
    try:
        return int(arguments.get(key) or default)
    except (TypeError, ValueError):
        return default


class KgSearchTool(Tool):
    """``kg_search`` — the traversal search over the KG namespaces."""

    name: ClassVar[str] = "kg_search"
    db_key: ClassVar[Tuple[str, str]] = (name, "kg")
    parameters: ClassVar[Dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Free keywords, Chinese or English "
                               "(trader vocabulary: 动量/momentum, 均线/"
                               "ma, 估值/valuation, 融资融券/margin, "
                               "期权/options, K线/ohlcv, 情绪/sentiment …).",
            },
            "namespace": _NAMESPACE_PARAM,
            "hops": _HOPS_SEARCH,
            "max_results": {
                "type": "integer", "minimum": 4, "maximum": 16,
                "description": "Nodes returned (default 10).",
            },
        },
        "required": ["query"],
    }
    DEFAULT_DESCRIPTION: ClassVar[str] = (
        "Search a knowledge graph for WHAT a question is about — call "
        "this BEFORE db_run_query / db_list_tables or wholesale prompt "
        "fetches. namespace 0 (default) = DB semantics: the TABLES by "
        "business meaning (动量 momentum, 均线 ma, 估值 valuation pe, "
        "融资融券 margin, 期权 implied vol, 拥挤度 crowdedness …) with "
        "their one-line meaning, trader tags, and the edges saying how "
        "they relate (computed_from = derived ← source, anchored_by = "
        "keyed on an identity table's grain, similar_to = same-concept "
        "family, joins_on = the key two tables are commonly JOINed on, "
        "(code, date) or code) — then db_describe_table the picked "
        "tables. namespace 1 = the prompt catalog: WHICH SKILL teaches "
        "a domain and which prompts relate (similar_to = same-domain "
        "skills, alternates_with = one-of-a-kind picks like the "
        "study-scope rows) — then load_prompt the skill when it is in "
        "your [Prompt catalog] block. You always get the small "
        "relevant subgraph, never a dump."
    )

    async def run(self, arguments: Dict[str, Any]) -> str:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return "kg_search error: pass business keywords " \
                   "(e.g. '动量 momentum' or 'margin 融资融券')"
        namespace = _namespace(arguments)
        if namespace not in NS_SCOPES:
            return ("kg_search error: namespace must be one of "
                    f"{sorted(NS_SCOPES)}")
        hops = _int_arg(arguments, "hops", 2)
        max_results = _int_arg(
            arguments, "max_results", kg_graph.NODE_MAX_DEFAULT)
        try:
            core = await KNOWLEDGE_GRAPHS.search(
                query, namespace=namespace, hops=hops,
                max_results=max_results)
        except KgUnavailableError as exc:  # noqa: BLE001 — tool text
            return f"kg_search error: knowledge graph unavailable: {exc}"
        except ValueError as exc:  # noqa: BLE001 — tool text
            return f"kg_search error: {exc}"
        except Exception as exc:  # noqa: BLE001 — tool text, not a crash
            return f"kg_search error: {type(exc).__name__}: {exc}"
        return core + "\n" + NS_NEXT_HINTS[namespace]


class KgExpandTool(Tool):
    """``kg_expand`` — one node's neighborhood (the drill-down face of
    the same graphs)."""

    name: ClassVar[str] = "kg_expand"
    db_key: ClassVar[Tuple[str, str]] = (name, "kg")
    parameters: ClassVar[Dict[str, Any]] = {
        "type": "object",
        "properties": {
            "names": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 4,
                "description": "Schema-qualified or bare names "
                               "('analysis.mov_ave_rsi' or 'mov_ave_rsi' "
                               "in ns0; a prompt_key like 'pe-guide' "
                               "in ns1).",
            },
            "namespace": _NAMESPACE_PARAM,
            "hops": {
                "type": "integer", "minimum": 1, "maximum": 2,
                "description": "Neighborhood radius (default 1).",
            },
        },
        "required": ["names"],
    }
    DEFAULT_DESCRIPTION: ClassVar[str] = (
        "Expand name(s) into their knowledge-graph neighborhood: every "
        "edge the node rides — for a TABLE (namespace 0): what it is "
        "computed FROM, what is computed from IT, which identity table "
        "anchors its grain, its same-concept family; for a PROMPT "
        "(namespace 1): which prompts teach the same domain and which "
        "are alternate picks. Use after kg_search to drill into a hit, "
        "or when you already know a name and need what feeds it / what "
        "it feeds."
    )

    async def run(self, arguments: Dict[str, Any]) -> str:
        raw = arguments.get("names")
        names = [str(name).strip() for name in
                 (raw if isinstance(raw, list) else [raw])
                 if str(name).strip()]
        if not names:
            return "kg_expand error: pass name(s), e.g. " \
                   "['analysis.mov_ave_rsi'] (ns0) or ['pe-guide'] (ns1)"
        namespace = _namespace(arguments)
        if namespace not in NS_SCOPES:
            return ("kg_expand error: namespace must be one of "
                    f"{sorted(NS_SCOPES)}")
        hops = _int_arg(arguments, "hops", 1)
        try:
            core = await KNOWLEDGE_GRAPHS.expand(
                names, namespace=namespace, hops=hops)
        except KgUnavailableError as exc:  # noqa: BLE001 — tool text
            return f"kg_expand error: knowledge graph unavailable: {exc}"
        except ValueError as exc:  # noqa: BLE001 — tool text
            return f"kg_expand error: {exc}"
        except Exception as exc:  # noqa: BLE001 — tool text, not a crash
            return f"kg_expand error: {type(exc).__name__}: {exc}"
        return core + "\n" + NS_NEXT_HINTS[namespace]


class KgToolSet(ToolKit):
    """The knowledge-graph kit (``kg_search`` + ``kg_expand``) — the
    DB schema's semantic search index as direct tools (no sandbox
    round-trip; the graph is cached per process).

    ``descriptions`` (the run's resolved ``llm.ai_prompts`` tool rows,
    prompt_type 4) overrides each tool's schema description with its
    DB row's instruction — fail-soft to the built-in constants."""

    def __init__(self, *,
                 descriptions: Optional[Dict[str, str]] = None) -> None:
        super().__init__(descriptions)

    def tools(self) -> List[Tool]:
        return [
            KgSearchTool(
                description=self.db_description(KgSearchTool.db_key)),
            KgExpandTool(
                description=self.db_description(KgExpandTool.db_key)),
        ]

    @staticmethod
    def schemas() -> List[Dict[str, Any]]:
        """The two schemas WITHOUT any state (the CLI's --list-tools —
        description overrides need a run's descriptions map; the plain
        constants are the no-DB form)."""
        return [KgSearchTool().schema, KgExpandTool().schema]
