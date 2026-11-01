"""llm_agents.deep_agents.planning.thread_search — the thread-internal
LLM-traffic search tool.

ONE tool, scoped to ONE langgraph thread: ``search_thread_context``
keyword-searches the run's OWN recorded ``text.llm_requests`` rows —
every agent level's conversations (the plan node's proposal session,
each step hop, every spawned fork at any depth), because a node's
requests ride ``chain_id`` = its ``llm_context_id`` and every chain of
the thread resolves from ``text.deep_agent_states`` /
``text.deep_agent_contexts`` (the one-node-one-chained-context key).

The index side is ``text.llm_requests.search_tsv`` (07_llm_requests.sql
— the weighted GIN-indexed tsvector over each request's INCREMENTAL
input + output, StructBERT-segmented by the repo's shared tokenization
stack). Before searching, the tool HEALS its thread's scope: rows the
request-log writer stored with a NULL vector (tokenizer outage at write
time) are indexed first, scoped to the thread's chains — so a search
never misses this run's own traffic. Requests still sitting in the
process's recorder queue are flushed first (``request_log.flush`` —
same-process fire-and-forget contract).

Ranking is RECENCY (request_id DESC): the shared tokenization contract
strips the vectors (GIN indexes lexemes only — and ``strip`` removes
weights as well as positions, so ``ts_rank`` scores ≈ 0 on them), and
for thread-internal recall the latest request that touched the keywords
is the relevant hit. The table is deliberately NOT one of the
DF-aware ``text.search_lexicon_df`` family — thread scopes are tens of
rows.

Mounted at EVERY level by the plan kits (``planning.authorities``):
the main lane's kit (``main_plan_tools`` — the step hops' agents) and
every spawned child's kit (``subagent_plan_tools`` — stamped on
``SubagentRequest.builtin_tools`` by the input hook, recursively for
sub-sub agents). The tool carries its own teaching in its schema — no
prompt-addendum change (the DB-seeded prompt rows stay untouched).

Plain accessor: failures render as the tool's result text (the loop
must survive a broken tool — the same contract as every handler in
``authorities``).
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from llm_agents.deep_agents.core.tools import BuiltinTool

logger = logging.getLogger("deep_agents.thread_search")

# Per-call result cap: hits carry ~400-char snippets; a tight cap keeps
# one tool call well inside an LLM context (the MCP text_search bound).
MAX_ROWS = 20

SEARCH_THREAD_CONTEXT_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "search_thread_context",
        "description": (
            "Keyword-search THIS run's own recorded LLM traffic — every "
            "agent of this same thread (the planner, each step, every "
            "forked sub-agent, any depth): what was ASKED, ANSWERED or "
            "FETCHED earlier in the run. Rows are this thread's recorded "
            "requests (incremental input + model output per call), "
            "retrieved by full-text match, newest first (words are "
            "AND-ed; Chinese is segmented automatically — natural "
            "keywords work, no operators needed). USE IT before redoing "
            "work an earlier step or fork may already have finished (a "
            "fetched table, a computed number, a concluded judgment), "
            "and when a finding needs evidence an earlier conversation "
            "saw. Hits show the request id, the node/step it belongs "
            "to, and snippets of the matched input and output."),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Free text; e.g. 「布林带 000001」or "
                                   "「rsi 查询 结果」. All words must hit "
                                   "(AND).",
                },
                "max_rows": {
                    "type": "integer",
                    "description": "Max hits to return (default 10, cap "
                                   f"{MAX_ROWS}).",
                },
            },
            "required": ["query"],
        },
    },
}


async def _thread_chains(conn, thread_id: str) -> Dict[str, str]:
    """The thread's chain-id -> node-label map — every llm_context_id the
    thread minted (states + contexts rows: step hops, forks at any
    depth, the plan node), each labeled with the node that ran it."""
    rows = await conn.fetch(
        """SELECT s.llm_context_id AS cid, s.node AS node
             FROM text.deep_agent_states s
            WHERE s.thread_id = $1 AND s.llm_context_id IS NOT NULL
           UNION
           SELECT c.llm_context_id AS cid, c.node AS node
             FROM text.deep_agent_contexts c
            WHERE c.thread_id = $1 AND c.llm_context_id IS NOT NULL""",
        thread_id)
    return {r["cid"]: (r["node"] or "?") for r in rows}


async def search_thread_context(
        thread_id: str, query: str, max_rows: int = 10) -> str:
    """The tool body: flush -> resolve the thread's chains -> heal the
    scope's unindexed rows -> segmented tsquery over search_tsv, ranked
    by ts_rank. Returns the tool's result text; every failure renders
    as ``search_thread_context error: …`` (never an exception)."""
    if not thread_id:
        return ("search_thread_context error: this run carries no thread "
                "— nothing recorded to search")
    query = (query or "").strip()
    if not query:
        return "search_thread_context error: 'query' is required"

    from llm_agents._core import request_log

    try:
        request_log.flush(10)
    except Exception as exc:  # noqa: BLE001 — queued rows may miss this call
        logger.warning("thread search: request-log flush failed: %s", exc)

    try:
        # Query-side segmentation — the SAME tokenization the document
        # vectors were built with (raw Chinese in a tsquery matches
        # nothing).
        from _common.tokenization import SearchIndexer

        tokens = SearchIndexer.get().tokenize_for_search(query)
    except Exception as exc:  # noqa: BLE001 — tool text, not a crash
        return f"search_thread_context error: query segmentation failed: {exc}"
    if not tokens.strip():
        return ("search_thread_context error: query produced no search "
                "tokens")

    from _common.db_commons import get_db_connection_async

    conn = await get_db_connection_async()
    try:
        chains = await _thread_chains(conn, thread_id)
        if not chains:
            return ("search_thread_context: no recorded LLM traffic for "
                    "this thread yet")
        healed = 0
        try:
            healed = await request_log.backfill_search_tsv_async(
                conn, chains=list(chains))
        except Exception as exc:  # noqa: BLE001 — search what is indexed
            logger.warning("thread search: scope heal failed (%s) — "
                           "searching indexed rows only", exc)
        limit = max(1, min(int(max_rows or 10), MAX_ROWS))
        rows = await conn.fetch(
            """SELECT r.request_id, r.chain_id, r.is_failed,
                      (r.created_at AT TIME ZONE 'Asia/Shanghai')::time::text
                        AS at,
                      LEFT(r.request_payload -> 'messages' -> -1
                           ->> 'content', 200) AS last_in,
                      COALESCE(
                          LEFT(r.response_payload -> 'choices' -> 0
                               -> 'message' ->> 'content', 300),
                          LEFT(r.response_payload -> 'choices' -> 0
                               -> 'message' -> 'tool_calls' -> 0
                               -> 'function' ->> 'name', 80),
                          '') AS out_text
                 FROM text.llm_requests r
                 CROSS JOIN websearch_to_tsquery('simple', $2::text) tsq
                WHERE r.chain_id = ANY($1::text[])
                  AND r.search_tsv @@ tsq
                ORDER BY r.request_id DESC
                LIMIT $3""",
            list(chains), tokens, limit)
    finally:
        await conn.close()

    lines = [f"thread search '{query}' -> {len(rows)} hit(s) of this "
             f"thread's {len(chains)} conversation(s)"
             + (f" ({healed} row(s) just indexed)" if healed else "")]
    for row in rows:
        node = chains.get(row["chain_id"], "?")
        flag = " FAILED" if row["is_failed"] else ""
        line = f"#{row['request_id']} [{node}]{flag} {row['at']}"
        if row["last_in"]:
            line += f" | in: {str(row['last_in']).strip()}"
        if row["out_text"]:
            line += f" | out: {str(row['out_text']).strip()}"
        lines.append(line)
    if not rows:
        lines.append("(no match — try fewer or different keywords; the "
                     "index covers what each conversation asked, answered "
                     "and fetched)")
    return "\n".join(lines)


def thread_search_tool(thread_id: str) -> BuiltinTool:
    """The mounted tool for one thread — the handler closes over the
    thread id, so EVERY level of the run (main lane or any spawned
    child) searches only its OWN thread's traffic by construction."""

    async def handler(args: Dict[str, Any]) -> str:
        try:
            return await search_thread_context(
                thread_id,
                str(args.get("query") or ""),
                int(args.get("max_rows") or 10))
        except Exception as exc:  # noqa: BLE001 — tool text, not a crash
            return f"search_thread_context error: {type(exc).__name__}: {exc}"

    return BuiltinTool(schema=SEARCH_THREAD_CONTEXT_SCHEMA, handler=handler)


def thread_search_handler(
        thread_id: str,
) -> Callable[[Dict[str, Any]], Awaitable[str]]:
    """Bare handler form (kits that compose their own BuiltinTool)."""
    return thread_search_tool(thread_id).handler
