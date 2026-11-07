/**
 * AI thread service — the deep-agent thread observability read for the
 * Thread Graph view: one thread's full node graph in a single round trip
 * so the UI can poll it while a run is live.
 *
 *   GET /api/ai/threads/:threadId  (routes/ai-threads.ts)
 *     → AgentThreadDetail: every llm.deep_agent_states row of the thread
 *       (top-level plan steps AND spawned-subagent child states), every
 *       llm.deep_agent_contexts payload they point into (kind
 *       plan / step-input / step-result / subagent-result, plus the
 *       runner's hardcoded result node's kind final-result — the UI
 *       Result view's display document), PLUS the
 *       LangGraph store's items (database/sql/text/
 *       10_deep_agent_store.sql — written by the runner via
 *       llm_agents/deep_agents/langgraph/state_store.py DeepAgentStore, hooked
 *       with compile(store=...)): the runner-lane NODE states
 *       (plan/execute/finalize lifecycle + counts — real statuses, not
 *       the client-side derivation) and the LLM-context REGISTRY (one
 *       reference item per context: node, langgraph checkpoint id, call
 *       counts — the context DATA itself is never persisted, by
 *       design; each state row's llm_context_id points into the
 *       registry). One node one LLM context: plan steps share the
 *       execute context, each subagent child has its own item.
 *       Node items carry their elapsed wall time (created_at is the
 *       begin write, updated_at the finish write — the runner writes
 *       node items only at begin/finish, so the row columns ARE the
 *       node's start/end stamps; a running node's elapsed is computed
 *       against now() so the live poll ticks it), and llm_usage rolls
 *       the thread's llm.llm_requests up per context (chain_id = the
 *       context id — token consumption, busy time, first/last call).
 *
 * Uncached by design (the api-client side skips its cache too): a live
 * thread's rows change on every tick, and a just-ticked step must show
 * on the next poll. Times render as Shanghai-local text (the QA list
 * pattern). Write side: llm_agents/deep_agents/planning/store.py +
 * state_store.py.
 */
import { labelDecodeSql } from "./label-codes.js";
import { querySqlView, sqlFrag } from "../lib/sql-views.js";
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
  /** plan | step-<state_id> | compact-<ordinal> | supervisor |
   *  finalize | result. */
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
  /** The item's FIRST write — the node's begin stamp (runner-lane nodes
   *  write only at begin/finish), Shanghai-local text. */
  created_at: string;
  updated_at: string;
  /** The node's wall time in seconds: updated_at (the finish write) −
   *  created_at for settled nodes, now() − created_at while running
   *  (computed server-side so the live poll's elapsed ticks without
   *  client-side timezone math on the Shanghai-local text). */
  elapsed_s: number | null;
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
    | "supervisor-review"
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

/** One context's recorded LLM traffic — llm.llm_requests aggregated by
 *  chain_id (the context id): the Contexts panel's token consumption and
 *  LLM busy-time. One row per context that recorded at least one
 *  request; token sums are NULL when no request in the chain carried a
 *  usage block. */
export interface AgentLlmUsageRow {
  context_id: string;
  n_requests: number;
  /** Sum of the calls' duration_ms — the context's LLM busy time (ms). */
  llm_ms: number | null;
  /** First → last request wall-clock span in seconds. */
  span_s: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  first_at: string | null;
  last_at: string | null;
}

/** One thread's full graph — states + contexts + the store's node
 *  states and context registry (empty arrays for threads that predate
 *  the store — the UI falls back to its derived runner-lane statuses).
 *
 *  THREE-GRAPH CHAIN (2026-11-06): a run is a CHAIN of three threads
 *  (plan / custom / result) sharing ``super_thread_id``; the read of
 *  ANY member resolves the whole chain and returns the MERGED view —
 *  every segment's rows concatenated (states/contexts are id-unique;
 *  the store's runner-lane NODE NAMES collide across segments, so
 *  segment k>0's non-step names carry an ``s<k>:`` namespace that is
 *  exactly the canvas node id). ``segments`` carries the chain
 *  metadata; ``thread_id`` stays the REQUESTED member (the root when
 *  the UI opens a run — its id is the chain id). */
export interface AgentThreadSegmentMeta {
  thread_id: string;
  /** plan | custom | result by chain position (single = a legacy /
   *  degraded one-thread run — no chain split). */
  graph_type: "plan" | "custom" | "result" | "single";
}

export interface AgentThreadDetail {
  thread_id: string;
  /** The chain's ROOT thread id (= the run's super_thread_id; equals
   *  thread_id on legacy single-thread runs). */
  super_thread_id?: string;
  /** The chain's segments in order (single-element "single" when the
   *  run predates the three-graph refactor). */
  segments?: AgentThreadSegmentMeta[];
  states: AgentThreadStateRow[];
  contexts: AgentThreadContextRow[];
  node_states: AgentNodeStateRow[];
  llm_contexts: AgentLlmContextRow[];
  /** Per-context LLM traffic rollup (empty for runs that predate the
   *  request-chain binding). */
  llm_usage: AgentLlmUsageRow[];
}

/** One llm.llm_requests row attributed to a task (plan step /
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

/** One thread's raw rows (states / contexts / node states / registry /
 *  usage) — the per-member read the chain merge below fans out. */
async function readOneThread(
  threadId: string,
): Promise<AgentThreadDetail | null> {
  // The BIGINT id columns cast to int: node-postgres hands int8 back as
  // a STRING, and the declared contract (AgentThreadStateRow, the
  // client's AgentThreadState) is number — the ThreadGraph keys its
  // child-state lookups on these ids. Row volumes are a handful per
  // thread, int4 range is far away.
  const states = await querySqlView<AgentThreadStateRow & QueryResultRow>(
    "data_viz/api/services/ai-thread/thread_states",
    { thread_id: threadId },
  );
  if (states.length === 0) return null;
  const contexts = await querySqlView<AgentThreadContextRow & QueryResultRow>(
    "data_viz/api/services/ai-thread/thread_contexts",
    {
      thread_id: threadId,
      kind_decode: sqlFrag(labelDecodeSql("agent_context_kind", "kind")),
    },
  );
  // The LangGraph store's items (state_store.DeepAgentStore writes):
  // node states under ("deep_agent", <tid>), the context registry under
  // ("deep_agent", <tid>, "contexts"). The value JSONB parses to the
  // row shape minus the key/updated_at tail; jsonb numbers are numbers.
  // The SELECTs live in the sql views
  // data_viz/api/services/ai-thread/node_states and .../llm_contexts.
  const node_states = await querySqlView<AgentNodeStateRow & QueryResultRow>(
    "data_viz/api/services/ai-thread/node_states",
    { thread_id: threadId },
  );
  const llm_contexts = await querySqlView<AgentLlmContextRow & QueryResultRow>(
    "data_viz/api/services/ai-thread/llm_contexts",
    { thread_id: threadId },
  );
  // Per-context LLM traffic — the thread's request chains rolled up by
  // context id (a node's registry item is written only at finish, so the
  // chain set unions the node items' live context ids in too; a running
  // node's requests are already recording under its chain).
  const chainIds = Array.from(
    new Set([
      ...node_states.flatMap((n) => (n.llm_context_id ? [n.llm_context_id] : [])),
      ...llm_contexts.map((c) => c.context_id),
    ]),
  );
  const llm_usage =
    chainIds.length === 0
      ? []
      : await querySqlView<AgentLlmUsageRow & QueryResultRow>(
          "data_viz/api/services/ai-thread/llm_usage",
          { chain_ids: chainIds },
        );
  return { thread_id: threadId, states, contexts, node_states, llm_contexts, llm_usage };
}

/** The chain's graph types by position — the three-graph refactor mints
 *  plan (root) → custom → result in that order; a single-member chain
 *  is a legacy / degraded one-thread run ("single"). */
const CHAIN_TYPES: AgentThreadSegmentMeta["graph_type"][] = [
  "plan", "custom", "result",
];

export async function getAgentThreadDetail(
  threadId: string,
): Promise<AgentThreadDetail | null> {
  if (!THREAD_ID_RE.test(threadId)) return null;
  // THE CHAIN RESOLUTION: any member's id resolves the run — every row
  // of the run carries the root's super_thread_id (legacy runs resolve
  // to their single thread; the root is the earliest row's thread).
  const members = await querySqlView<{ thread_id: string } & QueryResultRow>(
    "data_viz/api/services/ai-thread/chain_threads",
    { thread_id: threadId },
  );
  if (members.length === 0) return null;
  const ones = await Promise.all(
    members.map((m) => readOneThread(m.thread_id)),
  );
  const segments: AgentThreadSegmentMeta[] = [];
  const states: AgentThreadStateRow[] = [];
  const contexts: AgentThreadContextRow[] = [];
  const node_states: AgentNodeStateRow[] = [];
  const llm_contexts: AgentLlmContextRow[] = [];
  const llm_usage: AgentLlmUsageRow[] = [];
  for (const [k, one] of ones.entries()) {
    if (one === null) continue;
    segments.push({
      thread_id: one.thread_id,
      graph_type: members.length === 1
        ? "single"
        : CHAIN_TYPES[k] ?? "custom",
    });
    states.push(...one.states);
    contexts.push(...one.contexts);
    llm_usage.push(...one.llm_usage);
    // Runner-lane node names ("output", "finalize", "compact-*") collide
    // across the chain's segments — namespace segment k>0's non-step
    // names ("s<k>:output"); step-* names are unique per state id and
    // the canvas ids rely on them staying bare. The namespaced name IS
    // the canvas node id, so the panel lookups join on it directly.
    if (k === 0) {
      node_states.push(...one.node_states);
      llm_contexts.push(...one.llm_contexts);
    } else {
      const nsName = (node: string): string =>
        node.startsWith("step-") ? node : `s${k}:${node}`;
      node_states.push(
        ...one.node_states.map((n) => ({ ...n, node: nsName(n.node) })),
      );
      llm_contexts.push(
        ...one.llm_contexts.map((c) => ({ ...c, node: nsName(c.node) })),
      );
    }
  }
  if (segments.length === 0) return null;
  return {
    thread_id: threadId,
    super_thread_id: members[0].thread_id,
    segments,
    states,
    contexts,
    node_states,
    llm_contexts,
    llm_usage,
  };
}

/**
 * One task's recorded LLM requests/responses (the ThreadGraph task-row
 * expansion's data).
 *
 * Attribution — the runner binds each node's request-log chain to its
 * LLM-context id (llm_agents/_core/request_log.py bind_request_chain;
 * chain_id = llm.deep_agent_states.llm_context_id), so the requests
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
  // The SELECT lives in the sql view
  // data_viz/api/services/ai-thread/state_llm_requests.
  const requests = await querySqlView<
    (AgentStateLlmRequestRow & { llm_context_id: string | null }) &
    QueryResultRow
  >(
    "data_viz/api/services/ai-thread/state_llm_requests",
    { thread_id: threadId, state_id: stateId },
  );
  if (requests.length === 0) {
    // Distinguish "task exists, nothing recorded" from "unknown task".
    // The SELECT lives in the sql view
    // data_viz/api/services/ai-thread/state_llm_context.
    const known = await querySqlView<
      { llm_context_id: string | null } & QueryResultRow
    >(
      "data_viz/api/services/ai-thread/state_llm_context",
      { thread_id: threadId, state_id: stateId },
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
