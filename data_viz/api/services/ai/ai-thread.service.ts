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
import { HttpError } from "../../lib/http.js";
import { labelDecodeSql } from "../infra/label-codes.js";
import { querySqlView, sqlFrag } from "../../lib/sql-views.js";
import type { QueryResultRow } from "pg";

/** One deep-agent state row — a plan step or a spawned/forked subagent. */
export interface AgentThreadStateRow {
  state_id: number;
  thread_id: string;
  /** NULL = a top-level plan step; set = a child state under that step. */
  parent_state_id: number | null;
  /** How the child was raised (the agent_child_kind label code, raw —
   *  the UI decodes from its mirror): NULL top-level | 0 spawn
   *  (fresh-context child) | 1 fork (inherit-context child). */
  child_kind: number | null;
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
   *  tag, a spawn slug, or a runner-lane tag plan|compact|result); null
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
  /** THE SERVER-COMPUTED LIVE FLAG (2026-11-13) — the same predicate
   *  the client's threadIsActive derives (any state pending/in_progress
   *  OR any runner-lane node item running), computed once HERE over the
   *  MERGED chain. The client prefers it: a client-side derivation
   *  could transiently read "settled" inside an inter-graph handoff
   *  window (both ledgers closed between graphs) and stop the poll
   *  loop forever; the flag rides the same fetch and — with the
   *  runner's chain-orchestrator item (node:super) spanning the run —
   *  never drops a live run. */
  live?: boolean;
  states: AgentThreadStateRow[];
  contexts: AgentThreadContextRow[];
  node_states: AgentNodeStateRow[];
  llm_contexts: AgentLlmContextRow[];
  /** Per-context LLM traffic rollup (empty for runs that predate the
   *  request-chain binding). */
  llm_usage: AgentLlmUsageRow[];
}

/** One AGENT STEP attributed to a task — the "agent steps" surface that
 *  replaces the raw llm-requests list (2026-11-12; aux types
 *  2026-11-13). step_type decodes via the agent_step_type label domain:
 *  0 inference = one LLM request (the payloads as recorded by the
 *  transport; a chain-bound row's request_payload is INCREMENTAL — the
 *  shared conversation stored once, flagged by _chain_incremental /
 *  _chain_shared_messages); 1 spawn = ONE spawned sub node collapsed —
 *  input_text is the fresh prompt the spawn was given, output_text is
 *  the sub node's FINAL output (its answer, or its error when the spawn
 *  failed — the parent collects the error and continues); 2 decision =
 *  one System One (jev) read and 3 tokenization = one StructBERT
 *  segmentation op (llm.deep_agent_aux_steps — recorded off-context,
 *  NEVER in any LLM conversation; input_text/output_text carry the
 *  compact faces, the typed payloads ride the raw view). The spawn step
 *  is sequential on the parent's timeline (the parent awaits the
 *  child): duration_ms is the wall span started_at→finished_at and the
 *  token columns are the cache-aware rollup over the child's OWN
 *  chain. The sub node's own intermediates are never inlined —
 *  child_state_id links the child node, whose own steps list through
 *  this same endpoint. */
export interface AgentStepRow {
  step_id: string;
  /** 0 inference | 1 spawn | 2 decision | 3 tokenization
   *  (agent_step_type). */
  step_type: number;
  llm_context_id: string | null;
  /** spawn only — the child deep_agent_states row (the child node). */
  child_state_id: number | null;
  /** spawn only — the fresh prompt the spawn was given. */
  input_text: string | null;
  /** spawn only — the sub node's final answer (or error when failed). */
  output_text: string | null;
  /** The provider type that served the request — the ext_llm_provider
   *  label loaded from static.secret_config (zhipu / dummy); the spawn
   *  arm carries min over the child chain's rows. */
  provider: string | null;
  model: string | null;
  status_code: number | null;
  is_failed: boolean;
  failure_reason: string | null;
  duration_ms: number | null;
  /** Usage token counts (inference: the row's own; spawn: the child
   *  chain's rollup; null when no usage block was recorded). */
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  /** spawn only — the child chain's cache-aware overlap sum. */
  cache_tokens: number | null;
  /** inference only. */
  request_payload: unknown;
  response_payload: unknown;
  /** spawn only — the child state's settle status. */
  child_status: string | null;
  created_at: string;
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
  // THE SERVER-COMPUTED LIVE FLAG — the threadIsActive predicate over
  // the MERGED chain (see the interface's live doc).
  const live =
    states.some((s) => s.status === "pending" || s.status === "in_progress") ||
    node_states.some((s) => s.status === "running");
  return {
    thread_id: threadId,
    super_thread_id: members[0].thread_id,
    live,
    segments,
    states,
    contexts,
    node_states,
    llm_contexts,
    llm_usage,
  };
}

/**
 * One task's AGENT STEPS (the ThreadGraph task-row expansion's data —
 * the llm-requests surface's replacement, 2026-11-12). Inference rows
 * keep the llm-requests attribution: the runner binds each node's
 * request-log chain to its LLM-context id
 * (llm_agents/_core/request_log.py bind_request_chain; chain_id =
 * llm.deep_agent_states.llm_context_id), so the requests join by
 * chain. A TOP-LEVEL plan step shares the execute node's context with
 * its sibling steps (one node one context), so its rows narrow by a
 * tick window (started_at → finished_at with a 2s tolerance — the
 * request whose response PROPOSED a tick lands milliseconds before
 * the tick's own DB write; a step never ticked gets no window). A
 * spawned-subagent child owns its whole context (its own chain) — no
 * window. Spawn rows come from the child deep_agent_states rows under
 * this task (prompt in, final output out, child-chain token rollup).
 * The SELECT lives in the sql view
 * data_viz/api/services/ai-thread/state_agent_steps.
 */
export async function getAgentStateAgentSteps(
  threadId: string,
  stateId: number,
): Promise<{ state_id: number; steps: AgentStepRow[] } | null> {
  if (!THREAD_ID_RE.test(threadId) || !Number.isInteger(stateId)) return null;
  // The UI opens a run by ANY chain member's id (usually the super
  // root), while a task of the custom/result segment lives under its
  // OWN segment's thread — resolve the OWNING member first (the view
  // resolves the chain internally), then read by it. The SELECT lives
  // in the sql view
  // data_viz/api/services/ai-thread/state_thread_owner.
  const owner = await querySqlView<{ thread_id: string } & QueryResultRow>(
    "data_viz/api/services/ai-thread/state_thread_owner",
    { thread_id: threadId, state_id: stateId },
  );
  if (owner.length === 0) return null;
  const steps = await querySqlView<AgentStepRow & QueryResultRow>(
    "data_viz/api/services/ai-thread/state_agent_steps",
    { thread_id: owner[0].thread_id, state_id: stateId },
  );
  // Distinguish "task exists, nothing recorded" from "unknown task".
  if (steps.length === 0) {
    const known = await querySqlView<
      { llm_context_id: string | null } & QueryResultRow
    >(
      "data_viz/api/services/ai-thread/state_llm_context",
      { thread_id: owner[0].thread_id, state_id: stateId },
    );
    if (known.length === 0) return null;
  }
  return { state_id: stateId, steps };
}

/** One runner-lane node's AGENT STEPS — the same four-type step list as
 *  getAgentStateAgentSteps keyed by the NODE ITEM's chain instead of a
 *  state id: the output node (and any runner-lane seat) has NO
 *  deep_agent_states row, but its store item carries the conversation's
 *  llm_context_id — its whole chain IS the node's conversation (no
 *  window, no spawn rows). The SELECT lives in the sql view
 *  data_viz/api/services/ai-thread/../server/node_agent_steps. null =
 *  unknown node / no chain stamped on its item. */
export async function getAgentNodeAgentSteps(
  threadId: string,
  nodeKey: string,
): Promise<{ node: string; steps: AgentStepRow[] } | null> {
  // nodeKey = the CANVAS node id — segment-prefixed for k>0
  // ("s2:output"; the plan segment's nodes are bare). Every graph
  // segment writes its own node item, so the prefix maps to the
  // OWNING segment's thread (chain_members.sql = the same chain-order
  // mapping fetch_chain_threads uses).
  const match = /^(?:(s(\d+)):)?([a-z_][a-z0-9_:-]{0,80})$/.exec(nodeKey);
  if (!THREAD_ID_RE.test(threadId) || match === null) {
    return null;
  }
  const bareNode = match[3];
  const segIdx = match[2] ? Number(match[2]) : 0;
  const members = await querySqlView<
    { thread_id: string } & QueryResultRow
  >("llm_agents/deep_agents/server/chain_members", {
    thread_id: threadId,
  });
  const owner = members[segIdx]?.thread_id;
  if (owner === undefined) return null;
  const steps = await querySqlView<AgentStepRow & QueryResultRow>(
    "llm_agents/deep_agents/server/node_agent_steps",
    { thread_id: owner, node: bareNode },
  );
  if (steps.length === 0) return null;
  return { node: nodeKey, steps };
}

// ---------------------------------------------------------------------------
// THE NODE ACTIONS — the Thread Graph's UI fork/spawn buttons (2026-11-15).
// The deep-agents daemon owns the delegation machinery (SpawnHooks row
// registration + the child DeepAgent run), so these FORWARD to it (the
// same transport the summary bridge uses — the daemon is WSL-local) and
// return the child's ids; the child then runs as a background task on
// the daemon and the UI's thread-detail poll picks the new child row up
// (the input hook ran synchronously before the response, so the canvas
// shows the node immediately, status running).
// ---------------------------------------------------------------------------

/** The daemon's node-action response (mirrors NodeActionResult). */
export interface NodeActionResult {
  kind: "spawn" | "fork";
  state_id: number | null;
  context_id: string | null;
  thread_id: string | null;
  parent_state_id: number | null;
  status: string;
}

const DEEP_AGENTS_HOST = "127.0.0.1";
const DEEP_AGENTS_PORT = 8766;

async function postNodeAction(
  path: string,
  body: Record<string, unknown>,
): Promise<NodeActionResult> {
  const res = await fetch(
    `http://${DEEP_AGENTS_HOST}:${DEEP_AGENTS_PORT}${path}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(30_000),
    },
  );
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const parsed = (await res.json()) as { detail?: string };
      if (parsed?.detail) detail = parsed.detail;
    } catch {
      // keep the HTTP-status fallback
    }
    // The daemon's status CARRIES: 404 unknown state / 409 an active
    // chain / 400 a bad fork request — the UI reads the detail text.
    throw new HttpError(
      res.status === 404 || res.status === 409 || res.status === 400
        ? res.status
        : 502,
      "UPSTREAM_FAILED",
      `deep-agents node action failed: ${detail}`,
    );
  }
  return (await res.json()) as NodeActionResult;
}

/** POST one UI SPAWN action — a fresh-context child under the clicked
 *  node's state (threadId may be any chain member id; the daemon
 *  resolves the state's own thread). */
export function runNodeSpawn(
  threadId: string,
  stateId: number,
  goal: string,
  maxSteps?: number,
): Promise<NodeActionResult> {
  return postNodeAction(
    `/threads/${encodeURIComponent(threadId)}/states/${stateId}/spawn`,
    maxSteps ? { goal, max_steps: maxSteps } : { goal },
  );
}

/** POST one RUN-NODE SPAWN action — a fresh-context child hanging off
 *  a runner-lane SEAT (the output node: no state row to parent from).
 *  nodeKey = the canvas node id (s<k>:-prefixed for k>0 segments); the
 *  daemon resolves the owning segment and parents the child under the
 *  chain root's plan state row. Spawn ONLY on a state-less seat — fork
 *  continues a recorded step conversation, which a graph-level seat
 *  has none of. */
export function runNodeSpawnOnNode(
  threadId: string,
  nodeKey: string,
  goal: string,
  maxSteps?: number,
): Promise<NodeActionResult> {
  return postNodeAction(
    `/threads/${encodeURIComponent(threadId)}/nodes/${encodeURIComponent(nodeKey)}/spawn`,
    maxSteps ? { goal, max_steps: maxSteps } : { goal },
  );
}

/** POST one RUN-NODE FORK action — the continuation child hanging off
 *  a runner-lane SEAT (the output node): the fork point request is
 *  validated ON the seat's OWN chain (the daemon reads it off the node
 *  item), the transcript reconstruction is the state-keyed fork's walk,
 *  and the child parents under the chain root's plan state row. */
export function runNodeForkOnNode(
  threadId: string,
  nodeKey: string,
  goal: string,
  requestId: string | number,
  maxSteps?: number,
): Promise<NodeActionResult> {
  return postNodeAction(
    `/threads/${encodeURIComponent(threadId)}/nodes/${encodeURIComponent(nodeKey)}/fork`,
    maxSteps
      ? { goal, request_id: Number(requestId), max_steps: maxSteps }
      : { goal, request_id: Number(requestId) },
  );
}

/** POST one UI FORK action — a continuation child from the clicked
 *  agent step's recorded conversation (requestId = the inference row's
 *  request id). */
export function runNodeFork(
  threadId: string,
  stateId: number,
  goal: string,
  requestId: string | number,
): Promise<NodeActionResult> {
  return postNodeAction(
    `/threads/${encodeURIComponent(threadId)}/states/${stateId}/fork`,
    { goal, request_id: Number(requestId) },
  );
}
