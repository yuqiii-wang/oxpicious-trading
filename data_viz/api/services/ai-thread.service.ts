/**
 * AI thread service — the deep-agent thread observability read for the
 * Thread Graph view: one thread's full node graph in a single round trip
 * so the UI can poll it while a run is live.
 *
 *   GET /api/ai/threads/:threadId  (routes/ai-threads.ts)
 *     → AgentThreadDetail: every text.deep_agent_states row of the thread
 *       (top-level plan steps AND spawned-subagent child states), every
 *       text.deep_agent_contexts payload they point into (kind
 *       plan / step-input / step-result / subagent-result, plus the
 *       runner's hardcoded result node's kind final-result — the UI
 *       Result view's display document), PLUS the
 *       LangGraph store's items (database/sql/text/
 *       12_deep_agent_store.sql — written by the runner via
 *       llm_agents/deep_agents/graph/state_store.py DeepAgentStore, hooked
 *       with compile(store=...)): the runner-lane NODE states
 *       (plan/execute/finalize lifecycle + counts — real statuses, not
 *       the client-side derivation) and the LLM-context REGISTRY (one
 *       reference item per context: node, langgraph checkpoint id, call
 *       counts — the context DATA itself is never persisted, by
 *       design; each state row's llm_context_id points into the
 *       registry). One node one LLM context: plan steps share the
 *       execute context, each subagent child has its own item.
 *
 * Uncached by design (the api-client side skips its cache too): a live
 * thread's rows change on every tick, and a just-ticked step must show
 * on the next poll. Times render as Shanghai-local text (the QA list
 * pattern). Write side: llm_agents/deep_agents/planning/store.py +
 * state_store.py.
 */
import { queryRows } from "./db.service.js";
import { labelDecodeSql } from "./label-codes.js";
import type { QueryResultRow } from "pg";

/** One deep-agent state row — a plan step or a spawned subagent. */
export interface AgentThreadStateRow {
  state_id: number;
  thread_id: string;
  /** NULL = a top-level plan step; set = a child state under that step. */
  parent_state_id: number | null;
  /** The graph node the state belongs to (work tag / spawn_subagent). */
  node: string;
  title: string;
  status: "pending" | "in_progress" | "completed" | "failed" | "skipped";
  /** 1-based plan order (children carry 0 — order among siblings is id). */
  ordinal: number;
  input_context_id: number | null;
  result_context_id: number | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  /** The LLM context this state ran under (registry key) — a REFERENCE;
   *  the context data is never persisted. */
  llm_context_id: string | null;
}

/** One runner-lane node state from the LangGraph store (node:<name>
 *  items — lifecycle + counts; the store persists states only). A step
 *  node's item (node:step-<state_id>) carries the state row its hop
 *  executed — the UI's join key for the dynamic step chain. */
export interface AgentNodeStateRow {
  /** plan | step-<state_id> | finalize. */
  node: string;
  status: "running" | "completed" | "failed" | "skipped";
  llm_context_id: string | null;
  /** The langgraph state (checkpoint) id the node ran under. */
  checkpoint_id: string | null;
  model: string | null;
  error: string | null;
  n_llm_calls: number;
  n_tool_calls: number;
  n_states: number;
  answer_chars: number;
  /** The deep_agent_states row this item's step hop executed (null for
   *  the runner-lane nodes plan/finalize/result). */
  state_id: number | null;
  updated_at: string;
}

/** One LLM-context registry item from the LangGraph store — references
 *  and counts ONLY (the messages are never persisted anywhere). */
export interface AgentLlmContextRow {
  context_id: string;
  kind: "node" | "subagent" | string;
  node: string;
  /** The deep_agent_states row for a subagent child's context. */
  state_id: number | null;
  parent_context_id: string | null;
  checkpoint_id: string | null;
  model: string | null;
  n_llm_calls: number;
  n_tool_calls: number;
  updated_at: string;
}

/** One JSONB context payload a state (or the thread) points into. The
 *  kind column is a CONVERTED label column (agent_context_kind code in
 *  the DB); the query decodes it back to the label string, so the
 *  response contract stays the pre-conversion vocabulary. */
export interface AgentThreadContextRow {
  context_id: number;
  kind:
    | "plan"
    | "step-input"
    | "step-result"
    | "subagent-result"
    | "final-result"
    | string;
  /** The graph node / task the entry belongs to (the step's business
   *  tag, a fork slug, or a runner-lane tag plan|compact|result); null
   *  on rows written before the column existed. */
  node: string | null;
  /** The deep_agent_states row of the task/step the entry belongs to
   *  (null for final-result and legacy rows). */
  state_id: number | null;
  /** The ONE chained LLM context (uuid hex) of the node conversation
   *  that produced the entry — the same key as the state rows'
   *  llm_context_id and llm_requests.chain_id: joining the requests by
   *  it links this payload to the calls that produced it. Null when no
   *  LLM work produced the entry (final-result) or pre-change rows. */
  llm_context_id: string | null;
  /** The JSONB payload as parsed (pg returns jsonb as plain objects). */
  payload: unknown;
  created_at: string;
}

/** One thread's full graph — states + contexts + the store's node
 *  states and context registry (empty arrays for threads that predate
 *  the store — the UI falls back to its derived runner-lane statuses). */
export interface AgentThreadDetail {
  thread_id: string;
  states: AgentThreadStateRow[];
  contexts: AgentThreadContextRow[];
  node_states: AgentNodeStateRow[];
  llm_contexts: AgentLlmContextRow[];
}

/** One text.llm_requests row attributed to a task (plan step /
 *  subagent child): the request/response payloads as recorded by the
 *  transport. A chain-bound row's request_payload is INCREMENTAL (the
 *  shared conversation stored once — _chain_incremental /
 *  _chain_shared_messages flags inside the payload; the full request as
 *  sent = merge of the chain up to that row). */
export interface AgentStateLlmRequestRow {
  request_id: string;
  model: string | null;
  status_code: number | null;
  is_failed: boolean;
  failure_reason: string | null;
  duration_ms: number | null;
  /** Usage token counts extracted at write time (null when the response
   *  carried no usage block — web-search POSTs, failures, old rows). */
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  /** The chain predecessor (walkable for full-payload reconstruction). */
  prev_request_id: string | null;
  request_payload: unknown;
  response_payload: unknown;
  created_at: string;
}

/** GET /threads/:threadId/states/:stateId/llm-requests — one task's
 *  recorded LLM requests/responses. */
export interface AgentStateLlmRequests {
  state_id: number;
  llm_context_id: string | null;
  requests: AgentStateLlmRequestRow[];
}

/** thread_id is a uuid hex (plan_store) — accept that shape only, so a
 *  junk id fails fast with a 400 instead of a pointless table scan. */
const THREAD_ID_RE = /^[0-9a-f]{32}$/i;

export async function getAgentThreadDetail(
  threadId: string,
): Promise<AgentThreadDetail | null> {
  if (!THREAD_ID_RE.test(threadId)) return null;
  // The BIGINT id columns cast to int: node-postgres hands int8 back as
  // a STRING, and the declared contract (AgentThreadStateRow, the
  // client's AgentThreadState) is number — the ThreadGraph keys its
  // child-state lookups on these ids. Row volumes are a handful per
  // thread, int4 range is far away.
  const states = await queryRows<AgentThreadStateRow & QueryResultRow>(
    `SELECT state_id::int AS state_id, thread_id,
            parent_state_id::int AS parent_state_id, node, title, status,
            ordinal, input_context_id::int AS input_context_id,
            result_context_id::int AS result_context_id, error,
            llm_context_id,
            (created_at AT TIME ZONE 'Asia/Shanghai')::text  AS created_at,
            (started_at AT TIME ZONE 'Asia/Shanghai')::text  AS started_at,
            (finished_at AT TIME ZONE 'Asia/Shanghai')::text AS finished_at
       FROM text.deep_agent_states
      WHERE thread_id = $1
      ORDER BY ordinal, state_id`,
    [threadId],
  );
  if (states.length === 0) return null;
  const contexts = await queryRows<AgentThreadContextRow & QueryResultRow>(
    `SELECT context_id::int AS context_id,
            ${labelDecodeSql("agent_context_kind", "kind")} AS kind,
            node,
            state_id::int AS state_id,
            llm_context_id,
            payload,
            (created_at AT TIME ZONE 'Asia/Shanghai')::text AS created_at
       FROM text.deep_agent_contexts
      WHERE thread_id = $1
      ORDER BY context_id`,
    [threadId],
  );
  // The LangGraph store's items (state_store.DeepAgentStore writes):
  // node states under ("deep_agent", <tid>), the context registry under
  // ("deep_agent", <tid>, "contexts"). The value JSONB parses to the
  // row shape minus the key/updated_at tail; jsonb numbers are numbers.
  const node_states = await queryRows<AgentNodeStateRow & QueryResultRow>(
    `SELECT (value->>'node')   AS node,
            (value->>'status') AS status,
            value->>'llm_context_id' AS llm_context_id,
            value->>'checkpoint_id'  AS checkpoint_id,
            value->>'model'          AS model,
            value->>'error'          AS error,
            (value->>'n_llm_calls')::int   AS n_llm_calls,
            (value->>'n_tool_calls')::int  AS n_tool_calls,
            (value->>'n_states')::int      AS n_states,
            (value->>'answer_chars')::int  AS answer_chars,
            NULLIF(value->>'state_id', '')::int AS state_id,
            (updated_at AT TIME ZONE 'Asia/Shanghai')::text AS updated_at
       FROM text.deep_agent_store_items
      WHERE namespace = ARRAY['deep_agent', $1]::text[]
        AND key LIKE 'node:%'
      ORDER BY key`,
    [threadId],
  );
  const llm_contexts = await queryRows<AgentLlmContextRow & QueryResultRow>(
    `SELECT key AS context_id,
            (value->>'kind') AS kind,
            (value->>'node') AS node,
            NULLIF(value->>'state_id', '')::int AS state_id,
            value->>'parent_context_id' AS parent_context_id,
            value->>'checkpoint_id'     AS checkpoint_id,
            value->>'model'             AS model,
            (value->>'n_llm_calls')::int  AS n_llm_calls,
            (value->>'n_tool_calls')::int AS n_tool_calls,
            (updated_at AT TIME ZONE 'Asia/Shanghai')::text AS updated_at
       FROM text.deep_agent_store_items
      WHERE namespace = ARRAY['deep_agent', $1, 'contexts']::text[]
      ORDER BY key`,
    [threadId],
  );
  return { thread_id: threadId, states, contexts, node_states, llm_contexts };
}

/**
 * One task's recorded LLM requests/responses (the ThreadGraph task-row
 * expansion's data).
 *
 * Attribution — the runner binds each node's request-log chain to its
 * LLM-context id (llm_agents/_core/request_log.py bind_request_chain;
 * chain_id = text.deep_agent_states.llm_context_id), so the requests
 * join by chain. A TOP-LEVEL plan step shares the execute node's
 * context with its sibling steps (one node one context), so its rows
 * narrow by a tick window:
 *   start  = started_at (the in_progress tick), falling back — for
 *            steps the model completed without starting — to the
 *            latest finished_at of an earlier sibling, else the row's
 *            created_at;
 *   end    = finished_at (the completed/failed tick), else now() (a
 *            live step's window stays open).
 * Both ends carry a 2s tolerance: the request whose response PROPOSED
 * a tick lands milliseconds before the tick's own DB write, and
 * batch-completed siblings' tick stamps interleave — boundary requests
 * may show on adjacent tasks, which is what they are. A step never
 * touched by a tick (pending) gets no window at all, so a live run's
 * later requests never leak into it. A spawned-subagent child owns its
 * whole context (its own chain) — no window. Zero rows for tasks of
 * runs that predate the chain binding (chain_id was never written).
 */
export async function getAgentStateLlmRequests(
  threadId: string,
  stateId: number,
): Promise<AgentStateLlmRequests | null> {
  if (!THREAD_ID_RE.test(threadId) || !Number.isInteger(stateId)) return null;
  const requests = await queryRows<
    (AgentStateLlmRequestRow & { llm_context_id: string | null }) &
    QueryResultRow
  >(
    `WITH s AS (
       SELECT state_id, parent_state_id, llm_context_id, created_at,
              started_at, finished_at,
              COALESCE(
                started_at,
                (SELECT max(p.finished_at) FROM text.deep_agent_states p
                  WHERE p.thread_id = s.thread_id
                    AND p.parent_state_id IS NULL
                    AND p.state_id < s.state_id),
                s.created_at) AS win_start
         FROM text.deep_agent_states s
        WHERE s.thread_id = $1 AND s.state_id = $2
     )
     SELECT r.request_id::text          AS request_id,
            s.llm_context_id            AS llm_context_id,
            r.model,
            r.status_code::int          AS status_code,
            r.is_failed,
            r.failure_reason,
            r.duration_ms::int          AS duration_ms,
            r.input_tokens::int         AS input_tokens,
            r.output_tokens::int        AS output_tokens,
            r.total_tokens::int         AS total_tokens,
            r.prev_request_id::text     AS prev_request_id,
            r.request_payload,
            r.response_payload,
            (r.created_at AT TIME ZONE 'Asia/Shanghai')::text AS created_at
       FROM s
       JOIN text.llm_requests r
         ON r.chain_id = s.llm_context_id
        AND (s.parent_state_id IS NOT NULL
             OR ((s.started_at IS NOT NULL OR s.finished_at IS NOT NULL)
                 AND r.created_at >= s.win_start - interval '2 seconds'
                 AND r.created_at <= COALESCE(s.finished_at, now())
                                        + interval '2 seconds'))
      ORDER BY r.request_id`,
    [threadId, stateId],
  );
  if (requests.length === 0) {
    // Distinguish "task exists, nothing recorded" from "unknown task".
    const known = await queryRows<
      { llm_context_id: string | null } & QueryResultRow
    >(
      `SELECT llm_context_id FROM text.deep_agent_states
        WHERE thread_id = $1 AND state_id = $2`,
      [threadId, stateId],
    );
    if (known.length === 0) return null;
    return {
      state_id: stateId,
      llm_context_id: known[0].llm_context_id,
      requests: [],
    };
  }
  return {
    state_id: stateId,
    // The chain every row joined on — the task's llm_context_id. The
    // response contract drops the per-row copy the query carried.
    llm_context_id: requests[0].llm_context_id,
    requests: requests.map((row) => {
      const { llm_context_id: _chain, ...rest } = row;
      void _chain;
      return rest;
    }),
  };
}
