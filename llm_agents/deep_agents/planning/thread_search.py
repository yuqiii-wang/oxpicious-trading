"""llm_agents.deep_agents.planning.thread_search — the RUN-CHAIN
LLM-traffic SEARCH BODY (since 2026-11-06 the THREE-GRAPH refactor,
scoped to the WHOLE CHAIN; formerly one thread; 2026-11-06 (b) the
CONTEXTS lane).

ONE search spanning the run's WHOLE CHAIN of LangGraph threads (plan →
custom → result — every thread the run minted, chained by
``llm.deep_agent_states.super_thread_id``; any member's id resolves the
chain, legacy single-thread runs resolve to themselves) over TWO
GIN-indexed lanes:

* REQUESTS — keyword-search every agent level's recorded
  ``llm.llm_requests`` rows across all member threads — the plan
  graph's proposal session, each of the custom graph's step hops and
  its output synthesis, the result graph's review seats and final
  answer, every spawned fork at any depth — because a node's requests
  ride ``chain_id`` = its ``llm_context_id`` and every chain of every
  member thread resolves from ``llm.deep_agent_states`` /
  ``llm.deep_agent_contexts`` (the one-node-one-chained-context key).
  Index side: ``llm.llm_requests.search_tsv`` (the weighted
  GIN-indexed tsvector over each request's INCREMENTAL input + output,
  StructBERT-segmented by the repo's shared tokenization stack).

* CONTEXTS — the run's recorded PAYLOADS: ``llm.deep_agent_contexts``
  rows (plan JSONs, step directives and results, fork answers, the
  graphs' syntheses) matched over their ``tags`` column (the
  StructBERT-tokenized GIN-indexed vector over each payload's text —
  the same tokenization contract, written by ``planning.store``'s
  ONE insert path). Hits carry their kind label
  (``static.label_dim`` 'agent_context_kind') and — the CRITICAL
  lane's point — the row's ``is_critical`` flag: the model's own mark
  of a run-conclusion-changing discovery. ``critical_only=True``
  narrows the whole search to the flagged rows (query then optional —
  the MUST-CHECK read the step/review prompts teach: what the run has
  already flagged as critical).

Before searching, the search HEALS the whole chain scope on BOTH
lanes: request rows the request-log writer stored with a NULL vector
and context rows a tokenizer outage left untagged are indexed first,
scoped to every member thread — so a search never misses this run's
own traffic. Requests still sitting in the process's recorder queue
are flushed first (``request_log.flush`` — same-process
fire-and-forget contract).

Hits are labeled ``[<graph>|<node>]`` (plan / custom / result by chain
position), and the result header lists each graph's FULL thread id —
the handle a follow-up ``db_run_query`` over ``llm.deep_agent_contexts``
needs to fetch an exact payload (the HOW the prompt rows teach).

Ranking is RECENCY (id DESC): the shared tokenization contract strips
the vectors (GIN indexes lexemes only — and ``strip`` removes weights
as well as positions, so ``ts_rank`` scores ≈ 0 on them), and for
run-internal recall the latest row that touched the keywords is the
relevant hit. The tables are deliberately NOT one of the DF-aware
``text.search_lexicon_df`` family — chain scopes are tens of rows.

Mounted at EVERY level by the plan kits (``tools.plan_tools``): the
main lane's kit (the step hops' agents, in every graph of the chain)
and every spawned child's kit (stamped on ``SubagentRequest.builtin_
tools`` by the input hook, recursively for sub-sub agents). The tool
carries its own teaching in its schema, and the DB-seeded prompt rows
(the type-4 tool row + the type-3 step addendum) teach the chain-reading
HOW alongside it.

Plain accessor: failures render as the tool's result text (the loop
must survive a broken tool — the same contract as every handler).
"""
from __future__ import annotations

import logging
from typing import Dict, List, Tuple

logger = logging.getLogger("deep_agents.thread_search")

# Per-call result cap: hits carry ~400-char snippets; a tight cap keeps
# one tool call well inside an LLM context (the MCP text_search bound).
MAX_ROWS = 20

# The chain-scope resolution: any member's id → the ROOT's chain id
# (every row of the run carries the root's super_thread_id; legacy
# single-thread runs carry NULL — COALESCE keeps the requested thread as
# its own root), then every thread sharing it, ordered by each member's
# earliest row (the plan thread is the run's first row).
_CHAIN_SQL = """
WITH root AS (
    SELECT COALESCE(MIN(s.super_thread_id), $1) AS chain_id
      FROM llm.deep_agent_states s
     WHERE s.super_thread_id = $1 OR s.thread_id = $1
)
SELECT s.thread_id
  FROM llm.deep_agent_states s, root
 WHERE s.super_thread_id = root.chain_id OR s.thread_id = root.chain_id
 GROUP BY s.thread_id
 ORDER BY MIN(s.created_at), s.thread_id
"""


class ThreadSearch:
    """The run-chain LLM-traffic search as ONE object (2026-11-07 — the
    former ``search_thread_context`` module function): bound to ONE
    thread id (any member of the run's chain — the chain scope resolves
    the run's every thread from it), :meth:`search` runs the whole
    body: flush -> resolve the chain -> heal the scope's unindexed rows
    on both lanes -> the segmented tsquery over ``llm_requests.search_
    tsv`` (requests lane) and ``deep_agent_contexts.tags`` (contexts
    lane), ranked by recency. Plain accessor: failures render as the
    tool's result text (never an exception)."""

    def __init__(self, thread_id: str) -> None:
        self._thread_id = thread_id

    @staticmethod
    def _graph_labels(member_count: int) -> List[str]:
        if member_count <= 1:
            return ["run"]
        labels = ["plan"] + ["custom"] * (member_count - 2)
        labels.append("custom" if member_count == 2 else "result")
        return labels


    @staticmethod
    async def _chain_scope(
        conn, thread_id: str
    ) -> Tuple[List[str], List[str], Dict[str, Tuple[str, str]]]:
        """The run-chain search scope: (member thread ids in chain order,
        their graph labels, and the chain-id -> (graph label, node) map —
        every llm_context_id every member minted (states + contexts rows:
        step hops, forks at any depth, the plan node, the output seats),
        each labeled with the graph and the node that ran it."""
        members = [r["thread_id"] for r in await conn.fetch(_CHAIN_SQL, thread_id)]
        if not members:
            members = [thread_id]
        labels = ThreadSearch._graph_labels(len(members))
        label_by_tid = dict(zip(members, labels))
        rows = await conn.fetch(
            """SELECT s.llm_context_id AS cid, s.node AS node, s.thread_id AS tid
                 FROM llm.deep_agent_states s
                WHERE s.thread_id = ANY($1::text[]) AND s.llm_context_id IS NOT NULL
               UNION
               SELECT c.llm_context_id AS cid, c.node AS node, c.thread_id AS tid
                 FROM llm.deep_agent_contexts c
                WHERE c.thread_id = ANY($1::text[]) AND c.llm_context_id IS NOT NULL""",
            members)
        chains = {
            r["cid"]: (label_by_tid.get(r["tid"], "run"), r["node"] or "?")
            for r in rows
        }
        return members, labels, chains


    async def search(
            self, query: str, max_rows: int = 10, *,
            critical_only: bool = False) -> str:
        """The search body: flush -> resolve the run's WHOLE CHAIN (any
        member id) -> heal the scope's unindexed rows on both lanes ->
        segmented tsquery over ``llm_requests.search_tsv`` (the requests
        lane) and ``deep_agent_contexts.tags`` (the contexts lane — the
        payloads themselves), ranked by recency. ``critical_only=True``
        drops the requests lane and filters the contexts lane to the rows
        the model flagged ``is_critical`` (the MUST-CHECK read; *query* is
        then optional — omitted, it lists every flagged discovery of the
        run). Returns the tool's result text; every failure renders as
        ``search_thread_context error: …`` (never an exception)."""
        if not self._thread_id:
            return ("search_thread_context error: this run carries no thread "
                    "— nothing recorded to search")
        query = (query or "").strip()
        if not query and not critical_only:
            return ("search_thread_context error: 'query' is required (it is "
                    "optional only with critical_only=true)")

        from llm_agents._core import request_log

        if not critical_only:
            try:
                request_log.flush(10)
            except Exception as exc:  # noqa: BLE001 — queued rows may miss this call
                logger.warning("thread search: request-log flush failed: %s", exc)

        tokens: str = ""
        if query:
            try:
                # Query-side segmentation — the SAME tokenization the document
                # vectors were built with (raw Chinese in a tsquery matches
                # nothing).
                from _common.tokenization import SearchIndexer

                tokens = SearchIndexer.get().tokenize_for_search(query)
            except Exception as exc:  # noqa: BLE001 — tool text, not a crash
                return f"search_thread_context error: query segmentation failed: {exc}"
            if not tokens.strip() and not critical_only:
                return ("search_thread_context error: query produced no search "
                        "tokens")

        from _common.db_commons import get_db_connection_async

        conn = await get_db_connection_async()
        try:
            members, labels, chains = await self._chain_scope(conn, self._thread_id)
            label_by_tid = dict(zip(members, labels))
            if not chains and not members:
                return ("search_thread_context: no recorded LLM traffic for "
                        "this run's chain yet")
            healed = healed_ctx = 0
            limit = max(1, min(int(max_rows or 10), MAX_ROWS))
            rows = []
            if not critical_only and chains:
                try:
                    healed = await request_log.backfill_search_tsv_async(
                        conn, chains=list(chains))
                except Exception as exc:  # noqa: BLE001 — search what is indexed
                    logger.warning("thread search: scope heal failed (%s) — "
                                   "searching indexed rows only", exc)
                rows = await conn.fetch(
                    """SELECT r.request_id, r.chain_id, r.is_failed,
                              (r.created_at AT TIME ZONE 'Asia/Shanghai')::time
                                ::text AS at,
                              LEFT(r.request_payload -> 'messages' -> -1
                                   ->> 'content', 200) AS last_in,
                              COALESCE(
                                  LEFT(r.response_payload -> 'choices' -> 0
                                       -> 'message' ->> 'content', 300),
                                  LEFT(r.response_payload -> 'choices' -> 0
                                       -> 'message' -> 'tool_calls' -> 0
                                       -> 'function' ->> 'name', 80),
                                  '') AS out_text
                         FROM llm.llm_requests r
                         CROSS JOIN websearch_to_tsquery('simple', $2::text) tsq
                        WHERE r.chain_id = ANY($1::text[])
                          AND r.search_tsv @@ tsq
                        ORDER BY r.request_id DESC
                        LIMIT $3""",
                    list(chains), tokens, limit)
            # The CONTEXTS lane: heal the scope's untagged payload rows
            # first (the same never-miss-own-traffic contract), then match
            # the GIN-indexed tags over every member thread — kind decoded
            # through static.label_dim (never a CASE map).
            try:
                from llm_agents.deep_agents.planning.store import (PLAN_STORE)

                healed_ctx = await PLAN_STORE.backfill_context_tags_async(
                    conn, thread_ids=members)
            except Exception as exc:  # noqa: BLE001 — search what is indexed
                logger.warning("thread search: context-tags heal failed (%s) "
                               "— searching tagged rows only", exc)
            ctx_rows = await conn.fetch(
                """SELECT c.context_id, c.thread_id, c.node, c.is_critical,
                          l.label AS kind,
                          (c.created_at AT TIME ZONE 'Asia/Shanghai')::time
                            ::text AS at,
                          LEFT(COALESCE(c.payload ->> 'note',
                                        c.payload ->> 'answer',
                                        c.payload ->> 'output',
                                        c.payload ->> 'review',
                                        c.payload::text), 260) AS snippet
                     FROM llm.deep_agent_contexts c
                     JOIN static.label_dim l
                       ON l.domain = 'agent_context_kind' AND l.code = c.kind
                    WHERE c.thread_id = ANY($1::text[])
                      AND ($2::text = '' OR c.tags @@ websearch_to_tsquery(
                              'simple', $2::text))
                      AND (NOT $3::boolean OR c.is_critical)
                    ORDER BY c.is_critical DESC, c.context_id DESC
                    LIMIT $4""",
                members, tokens, bool(critical_only), limit)
        finally:
            await conn.close()

        mode = ("critical discoveries" if critical_only
                else f"'{query}'" if query else "all traffic")
        lines = [
            f"chain search {mode} -> {len(rows)} request hit(s) + "
            f"{len(ctx_rows)} context hit(s) across the run's "
            f"{len(members)} graph thread(s) "
            f"({' -> '.join(labels)}), {len(chains)} conversation(s)"
            + (f" ({healed + healed_ctx} row(s) just indexed)"
               if healed or healed_ctx else "")
        ]
        if len(members) > 1:
            # The follow-up-read legend: each graph's FULL thread id — the
            # handle a db_run_query over llm.deep_agent_contexts needs for an
            # exact payload (the HOW the prompt rows teach).
            lines.append("threads: " + " ".join(
                f"{label}={tid}" for label, tid in zip(labels, members)))
        for row in rows:
            graph, node = chains.get(row["chain_id"], ("?", "?"))
            flag = " FAILED" if row["is_failed"] else ""
            line = f"#{row['request_id']} [{graph}|{node}]{flag} {row['at']}"
            if row["last_in"]:
                line += f" | in: {str(row['last_in']).strip()}"
            if row["out_text"]:
                line += f" | out: {str(row['out_text']).strip()}"
            lines.append(line)
        for row in ctx_rows:
            graph = label_by_tid.get(row["thread_id"], "run")
            node = row["node"] or "?"
            flag = " CRITICAL" if row["is_critical"] else ""
            line = (f"ctx#{row['context_id']} [{graph}|{node}] "
                    f"{row['kind']}{flag} {row['at']} "
                    f"| {str(row['snippet']).strip()}")
            lines.append(line)
        if not rows and not ctx_rows:
            lines.append("(no match — try fewer or different keywords; the "
                         "index covers what every conversation of the run's "
                         "chain asked, answered and fetched, plus every "
                         "recorded payload's tags)")
        return "\n".join(lines)
