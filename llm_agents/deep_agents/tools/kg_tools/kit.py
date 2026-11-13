"""llm_agents.deep_agents.tools.kg_tools.kit — ``KgToolSet``, the
knowledge-graph kit the runner mounts on every step conversation and
the delegation hooks stamp on every spawned child: the DB schema's and
the prompt catalog's SEMANTIC index as two direct tools, so an agent
finds the right tables / the right skill by business meaning instead
of listing schemas blind or fetching prompts wholesale (retrieved
TABLE hits carry their exact column names + comments auto-loaded from
pg_catalog — ``db_describe_table`` only for tables a result does not
show; ``load_prompt`` stays the loading lane — the KG is what tells
you WHICH skill to load).

Thin adapters by design (2026-11-06 migration): the KG domain lives in
the INDEPENDENT ``llm_agents.kg`` package — the tools carry only the
model-facing schema, the loose-argument coercion, the error formatting
and the agent-flow hints, and delegate the flow (TTL graph load, the
two-layer chain cache, the render) to ``KNOWLEDGE_GRAPHS``, the
process-wide ``KnowledgeGraphService``.

The FORCED SCOPE (2026-11-11): the run's ``data.forced_tables`` (the
user's table pick) and ``data.forced_schemas`` (the AI-summary
bridge's standing schema allowlist: analysis / strategy / stats /
text / multi_media) narrow the served graph — the kit reads them at
MOUNT time (:func:`table_scope_from_data` /
:func:`schema_scope_from_data`) and every tool hands them to the
service as ``allowed_tables`` / ``allowed_schemas``; structural
enforcement lives in the service (the scoped subgraph + its own
chain-cache digest), so an out-of-scope table cannot surface whatever
the model asks. A mount without the scope (the supervisor's
diagnostic kit, the CLI) serves everything — the default.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, FrozenSet, List, Optional, Tuple

from llm_agents.deep_agents.tools.base import Tool, ToolKit
from llm_agents.deep_agents.tools.kg_tools.gate import KgJevGate, gate_for
from llm_agents.kg import (
    NS_SCOPES, KNOWLEDGE_GRAPHS, KgUnavailableError,
    normalize_schema_scope, normalize_table_scope,
)
from llm_agents.kg import graph as kg_graph

_HOPS_SEARCH = {
    "type": "integer", "minimum": 1, "maximum": 3,
    "description": "Spreading-activation hops from the matched nodes "
                   "(default 2: a node's sources/derivations/family).",
}

_NAMESPACE_PARAM = {
    "type": "integer", "enum": [0, 1, 2],
    "description": "Which knowledge-graph namespace to search: 0 = DB "
                   "semantics, one node per table/view (default — when "
                   "omitted, 0 AND 2 are searched together); 1 = the "
                   "prompt catalog, one node per skill / plan prompt "
                   "(find WHICH skill teaches a domain); 2 = data & "
                   "labels, the static.label_dim SMALLINT type "
                   "vocabularies and the tables each one types (find "
                   "WHICH tables carry a coded vocabulary — macd, "
                   "buy/sell, top/bottom, CALL/PUT …).",
}

# The agent flow after a hit differs per namespace (ns0 walks into
# columns/data, ns1 loads a skill, ns2 decodes codes / describes the
# located tables) — tool-layer vocabulary, kept HERE while the scope
# labels themselves live with the domain (NS_SCOPES). Retrieved table
# hits carry their column names + comments AND their primary-key /
# index grain auto-loaded, so the flow goes straight to data queries
# keyed on the PK columns.
NS_NEXT_HINTS = {
    0: ("next: kg_expand a table above for its full neighborhood; table "
        "hits above carry their column names + comments AND their "
        "pk/index grain inline — db_run_query the data directly, "
        "filtering on the key columns shown (db_describe_table only "
        "for a table not shown). The sql views listed under a table "
        "are the read-side query templates that read it (name = the "
        "template path); kg_expand the table for the full set. If the "
        "hits miss part of your concept, PROPOSE 2-3 SYNONYMS / "
        "paraphrases (Chinese AND English trader terms, e.g. "
        "间距→spread→乖离, 排列→alignment) and kg_search them again."),
    1: ("next: kg_expand a prompt above for its related prompts; load a "
        "found skill with load_prompt when its key is in your [Prompt "
        "catalog] block (loading stays run-scoped)."),
    2: ("next: the located tables carry their column names + comments "
        "and pk/index grain inline; decode their SMALLINT codes "
        "through static.label_dim — db_run_query: SELECT code, label "
        "FROM static.label_dim WHERE domain = '<the label_domain "
        "name>'. If the hits miss part of your concept, PROPOSE 2-3 "
        "SYNONYMS / paraphrases (Chinese AND English trader terms) "
        "and kg_search again."),
}

# The default kg_search lane searches ns0 AND ns2 together (the two
# table-location corpora): the merged hint covers both flows.
_MERGED_NEXT_HINT = (
    "next: db_run_query the located tables — ns0 lines say how the "
    "tables relate and the sql views under a table name the read-side "
    "query templates that read it; ns2 lines name the SMALLINT "
    "vocabularies that type them (decode via static.label_dim: "
    "SELECT code, label FROM static.label_dim WHERE domain = '<the "
    "label_domain name>'); table hits carry their column names + "
    "comments AND their pk/index grain inline (filter on the key "
    "columns). If the hits miss part of your concept, PROPOSE 2-3 "
    "SYNONYMS / paraphrases (Chinese AND English trader terms, e.g. "
    "间距→spread→乖离, 排列→alignment, 量→volume/amount) and kg_search "
    "again.")

# The default search's second lane and its leaner node budget (ns0
# keeps the caller's max_results; ns2 rides along at most this many).
_MERGED_NS2_MAX = 6

# The GRADUAL-LOAD LADDER (2026-11-10) — three attempts, taught in the
# description and appended to every hit hint:
#   1. kg_search renders the LEAN tier (default): the head of the
#      ranking — top tables, their key columns, their reading SQL.
#   2. the lean tier missed → the SAME query with detail:'full' loads
#      every ranked node with complete schemas and all related SQL.
#   3. BOTH tiers missed → STOP searching; write a sandbox script
#      (sandbox_write_file + sandbox_run, study_kit first) and compute.
_LADDER_HINT = (
    "Loads GRADUALLY — this result is one tier of three: when it misses "
    "part of your concept, kg_search the SAME query with "
    "detail:'full' for every ranked node + complete schemas; when BOTH "
    "tiers miss, STOP searching and compute in the sandbox instead — "
    "sandbox_write_file a script + sandbox_run it, importing study_kit "
    "first (db.fetch_daily_bars / studies.*) to load raw bars and "
    "calculate.")

_DETAIL_PARAM = {
    "type": "string", "enum": ["lean", "full"],
    "description": "The gradual-load tier (default 'lean'): lean "
                   "renders the head of the ranking only — top tables "
                   "with their key columns + reading SQL templates; "
                   "'full' renders EVERY ranked node with complete "
                   "column schemas and all related SQL. Re-run the "
                   "same query with detail:'full' ONLY when the lean "
                   "tier missed.",
}


def _namespace(arguments: Dict[str, Any]) -> int:
    """The validated namespace argument (0/1/2; default 0)."""
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


def _detail(arguments: Dict[str, Any]) -> str:
    """The validated gradual-load tier ('lean' unless an explicit
    'full' — anything else falls to the safe first attempt)."""
    value = str(arguments.get("detail") or "").strip().lower()
    return "full" if value == "full" else "lean"


def table_scope_from_data(
        data: Optional[Dict[str, Any]]) -> Optional[FrozenSet[str]]:
    """The run's FORCED-TABLE-SCOPE from its structured input ``data``
    (the ``forced_tables`` allowlist the AI-summary bridge rides) — the
    normalized set the kits mount their tools with, ``None`` when the
    run carries no usable list (no limitation — the default). Junk
    shapes (a non-list, non-string entries) drop out tolerantly: the
    scope narrows the KG surface, it never breaks a run."""
    raw = (data or {}).get("forced_tables")
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return None
    return normalize_table_scope(raw)


def schema_scope_from_data(
        data: Optional[Dict[str, Any]]) -> Optional[FrozenSet[str]]:
    """The run's FORCED-SCHEMA-SCOPE from its structured input ``data``
    (``forced_schemas`` — the AI-summary bridge's standing five:
    analysis / strategy / stats / text / multi_media) — the normalized
    set the kits mount beside the table scope, ``None`` when absent
    (every schema serves). Same junk-tolerant contract as
    :func:`table_scope_from_data`."""
    raw = (data or {}).get("forced_schemas")
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return None
    return normalize_schema_scope(raw)


class KgSearchTool(Tool):
    """``kg_search`` — the traversal search over the KG namespaces.

    ``table_scope`` — the run's forced-table allowlist handed to the
    service (``None`` = no limitation). The mount-time scope is
    structural: the model cannot argue its way past it, and the
    rendered note line tells it out-of-scope means filtered."""

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
                "description": "Nodes returned (default 10; the lean "
                               "tier renders its head).",
            },
            "detail": _DETAIL_PARAM,
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
        "(code, date) or code). namespace 1 = the prompt catalog: WHICH "
        "SKILL teaches a domain and which prompts relate — then "
        "load_prompt the skill when it is in your [Prompt catalog] "
        "block. namespace 2 = data & labels: the static.label_dim "
        "SMALLINT vocabularies (macd, mov_rsi, buy/sell, top/bottom, "
        "CALL/PUT, regime …) and WHICH tables each one types "
        "(labeled_by/for_<column> edges). OMIT namespace and BOTH 0 "
        "and 2 are searched together — the tables by meaning plus the "
        "typed-vocabulary lane that catches code-word queries plain "
        "table vocabulary misses. Results load GRADUALLY in two tiers: "
        "the default detail:'lean' renders the head of the ranking — "
        "top tables with their key columns + reading SQL templates; "
        "when that misses, re-search the SAME query with "
        "detail:'full' for every ranked node with complete column "
        "schemas (filter on the PRIMARY KEY / key columns shown — "
        "usually (code, date)); when BOTH tiers miss, STOP searching "
        "and compute in the sandbox — sandbox_write_file a script + "
        "sandbox_run it, importing study_kit first (db.fetch_daily_bars "
        "/ studies.*) to load raw bars and calculate. Token "
        "matching is EXACT trader vocabulary — when the top hits miss "
        "your concept or cover only part of it, PROPOSE 2-3 SYNONYMS / "
        "paraphrases (Chinese AND English trader terms: 间距→spread→"
        "乖离, 排列→alignment, 量→volume/amount, 动量→momentum/roc) and "
        "kg_search them again BEFORE listing schemas blind with "
        "db_list_tables."
    )

    def __init__(self, *, description: Optional[str] = None,
                 table_scope: Optional[FrozenSet[str]] = None,
                 schema_scope: Optional[FrozenSet[str]] = None,
                 decision_engine: Optional[Any] = None) -> None:
        super().__init__(description=description)
        self._table_scope = table_scope
        self._schema_scope = schema_scope
        self._gate = gate_for(decision_engine)

    async def run(self, arguments: Dict[str, Any]) -> str:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return "kg_search error: pass business keywords " \
                   "(e.g. '动量 momentum' or 'margin 融资融券')"
        hops = _int_arg(arguments, "hops", 2)
        max_results = _int_arg(
            arguments, "max_results", kg_graph.NODE_MAX_DEFAULT)
        detail = _detail(arguments)
        scope = self._table_scope
        schemas = self._schema_scope
        judge = (self._gate.judge if self._gate is not None else None)
        explicit = arguments.get("namespace")
        if explicit is not None:
            namespace = _namespace(arguments)
            if namespace not in NS_SCOPES:
                return ("kg_search error: namespace must be one of "
                        f"{sorted(NS_SCOPES)}")
            try:
                if judge is not None:
                    core, verdict = await KNOWLEDGE_GRAPHS.search_gated(
                        query, judge=judge, namespace=namespace, hops=hops,
                        max_results=max_results, detail=detail,
                        allowed_tables=scope, allowed_schemas=schemas)
                    if verdict == "rejected":
                        return self._gate.reject_nudge(
                            query, NS_SCOPES[namespace])
                else:
                    core = await KNOWLEDGE_GRAPHS.search(
                        query, namespace=namespace, hops=hops,
                        max_results=max_results, detail=detail,
                        allowed_tables=scope, allowed_schemas=schemas)
            except KgUnavailableError as exc:  # noqa: BLE001 — tool text
                return f"kg_search error: knowledge graph unavailable: {exc}"
            except ValueError as exc:  # noqa: BLE001 — tool text
                return f"kg_search error: {exc}"
            except Exception as exc:  # noqa: BLE001 — tool text, not a crash
                return f"kg_search error: {type(exc).__name__}: {exc}"
            return core + "\n" + NS_NEXT_HINTS[namespace] \
                + "\n" + _LADDER_HINT
        # The default lane: ns0 + ns2 searched together (fail-soft per
        # lane — one unavailable corpus never blanks the other). The
        # tier rides BOTH lanes; the engine caps stay tier-independent.
        parts: List[str] = []
        for namespace, cap in ((0, max_results),
                               (2, min(_MERGED_NS2_MAX, max_results))):
            try:
                if judge is not None:
                    part, verdict = await KNOWLEDGE_GRAPHS.search_gated(
                        query, judge=judge, namespace=namespace, hops=hops,
                        max_results=cap, detail=detail,
                        allowed_tables=scope, allowed_schemas=schemas)
                    if verdict == "rejected":
                        parts.append(self._gate.reject_nudge(
                            query, NS_SCOPES[namespace]))
                        continue
                    parts.append(part)
                else:
                    parts.append(await KNOWLEDGE_GRAPHS.search(
                        query, namespace=namespace, hops=hops,
                        max_results=cap, detail=detail,
                        allowed_tables=scope, allowed_schemas=schemas))
            except KgUnavailableError as exc:  # noqa: BLE001 — tool text
                parts.append(f"(ns{namespace} knowledge graph "
                             f"unavailable: {exc})")
            except ValueError as exc:  # noqa: BLE001 — tool text
                return f"kg_search error: {exc}"
            except Exception as exc:  # noqa: BLE001 — tool text, not a crash
                parts.append(f"(ns{namespace} error: "
                             f"{type(exc).__name__}: {exc})")
        return "\n\n".join(parts) + "\n" + _MERGED_NEXT_HINT \
            + "\n" + _LADDER_HINT


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
                               "in ns1; a label_dim domain like "
                               "'signal_type' in ns2).",
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
        "anchors its grain, its same-concept family, plus its exact "
        "column names + comments loaded from the DB and the PRIMARY "
        "KEY + index grain; for a PROMPT "
        "(namespace 1): which prompts teach the same domain and which "
        "are alternate picks; for a LABEL DOMAIN (namespace 2): every "
        "table a SMALLINT vocabulary types and which column carries "
        "it. Use after kg_search to drill into a hit, or when you "
        "already know a name and need what feeds it / what it feeds."
    )

    def __init__(self, *, description: Optional[str] = None,
                 table_scope: Optional[FrozenSet[str]] = None,
                 schema_scope: Optional[FrozenSet[str]] = None,
                 decision_engine: Optional[Any] = None) -> None:
        super().__init__(description=description)
        self._table_scope = table_scope
        self._schema_scope = schema_scope
        self._gate = gate_for(decision_engine)

    async def run(self, arguments: Dict[str, Any]) -> str:
        raw = arguments.get("names")
        names = [str(name).strip() for name in
                 (raw if isinstance(raw, list) else [raw])
                 if str(name).strip()]
        if not names:
            return "kg_expand error: pass name(s), e.g. " \
                   "['analysis.mov_ave_rsi'] (ns0), ['pe-guide'] (ns1) " \
                   "or ['signal_type'] (ns2)"
        namespace = _namespace(arguments)
        if namespace not in NS_SCOPES:
            return ("kg_expand error: namespace must be one of "
                    f"{sorted(NS_SCOPES)}")
        hops = _int_arg(arguments, "hops", 1)
        judge = (self._gate.judge if self._gate is not None else None)
        try:
            if judge is not None:
                core, verdict = await KNOWLEDGE_GRAPHS.expand_gated(
                    names, judge=judge, namespace=namespace, hops=hops,
                    allowed_tables=self._table_scope,
                    allowed_schemas=self._schema_scope)
                if verdict == "rejected":
                    return self._gate.reject_nudge(
                        ", ".join(names), NS_SCOPES[namespace])
            else:
                core = await KNOWLEDGE_GRAPHS.expand(
                    names, namespace=namespace, hops=hops,
                    allowed_tables=self._table_scope,
                    allowed_schemas=self._schema_scope)
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
    DB row's instruction — fail-soft to the built-in constants.
    ``table_scope`` / ``schema_scope`` — the run's forced allowlists
    (:func:`table_scope_from_data` / :func:`schema_scope_from_data`
    over the run's data) handed to every member tool; ``None`` = no
    limitation on that channel (the unscoped mounts — the supervisor's
    diagnostic kit, the CLI — serve the whole graph)."""

    def __init__(self, *,
                 descriptions: Optional[Dict[str, str]] = None,
                 table_scope: Optional[FrozenSet[str]] = None,
                 schema_scope: Optional[FrozenSet[str]] = None,
                 decision_engine: Optional[Any] = None) -> None:
        super().__init__(descriptions)
        self._table_scope = table_scope
        self._schema_scope = schema_scope
        self._decision_engine = decision_engine

    def tools(self) -> List[Tool]:
        return [
            KgSearchTool(
                description=self.db_description(KgSearchTool.db_key),
                table_scope=self._table_scope,
                schema_scope=self._schema_scope,
                decision_engine=self._decision_engine),
            KgExpandTool(
                description=self.db_description(KgExpandTool.db_key),
                table_scope=self._table_scope,
                schema_scope=self._schema_scope,
                decision_engine=self._decision_engine),
        ]

    @staticmethod
    def schemas() -> List[Dict[str, Any]]:
        """The two schemas WITHOUT any state (the CLI's --list-tools —
        description overrides need a run's descriptions map; the plain
        constants are the no-DB form)."""
        return [KgSearchTool().schema, KgExpandTool().schema]
