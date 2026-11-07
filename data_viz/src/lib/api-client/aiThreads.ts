/**
 * AI thread-graph client — the Thread Graph view's reads: one planned
 * deep-agent run's full node graph by thread id (GET
 * /api/ai/threads/:threadId — every llm.deep_agent_states row plus the
 * llm.deep_agent_contexts payloads), polled while the run is live.
 *
 * The thread LIST comes from the existing fetchSummaryRuns
 * (GET /api/ai/summary-runs, aiSummary.ts) — same source, no duplicate
 * endpoint. Plain fetch (not fetchJson) for the detail: the api-server
 * side is uncached by design and a live thread must never serve a stale
 * cache entry while polling.
 */

export type { AiSummaryRun } from "./aiSummary";
export { fetchSummaryRuns } from "./aiSummary";

/** One state node of the thread graph (a plan step or a subagent). */
export interface AgentThreadState {
  state_id: number;
  thread_id: string;
  /** NULL = a top-level plan step; set = a child state under that step. */
  parent_state_id: number | null;
  /** The graph node the state belongs to (work tag / spawn_subagent). */
  node: string;
  title: string;
  status: "pending" | "in_progress" | "completed" | "failed" | "skipped";
  /** 1-based plan order (children carry 0 — sibling order is state_id). */
  ordinal: number;
  input_context_id: number | null;
  result_context_id: number | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  /** The LLM context this state ran under (registry key) — a REFERENCE;
   *  the context data is never persisted (states only, by design). */
  llm_context_id: string | null;
}

/** One runner-lane node state from the LangGraph store — real
 *  plan / step-* / compact-* / supervisor / result / finalize lifecycle
 *  + counts (empty for threads that predate the store; the graph model
 *  then derives them as before). A step node's item carries the
 *  deep_agent_states row its hop executed (state_id) — the join key
 *  between the dynamic step chain on the canvas and the per-node call
 *  counts. */
export interface AgentNodeState {
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
  /** The item's first write — the node's begin stamp (node items are
   *  written only at begin/finish), Shanghai-local text. */
  created_at: string;
  updated_at: string;
  /** The node's wall time in seconds — finish − begin for settled nodes,
   *  now − begin while running (server-computed at each poll). */
  elapsed_s: number | null;
}

/** One context's recorded LLM traffic — llm.llm_requests rolled up by
 *  chain_id (the context id): the Contexts panel's token consumption
 *  (rendered in k) and LLM busy time. Absent for contexts that recorded
 *  no request (or runs predating the chain binding). */
export interface AgentLlmUsage {
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

/** One LLM-context registry item — references and counts only: which
 *  node, which langgraph state id, how many calls. Never the messages. */
export interface AgentLlmContext {
  context_id: string;
  kind: "node" | "subagent" | string;
  node: string;
  state_id: number | null;
  parent_context_id: string | null;
  checkpoint_id: string | null;
  model: string | null;
  n_llm_calls: number;
  n_tool_calls: number;
  updated_at: string;
}

/** One JSONB context payload the states point into. final-result is the
 *  RESULT graph's final output — the run's answer document the Result
 *  view displays; graph-output is the CUSTOM graph's final output — the
 *  chain handoff document the result graph's reviews read as their
 *  input; supervisor-review is the supervisor node's main-path review —
 *  the Thread Graph's default beneath-the-graph document (shown when no
 *  node is clicked). */
export interface AgentThreadContext {
  context_id: number;
  kind:
    | "plan"
    | "step-input"
    | "step-result"
    | "subagent-result"
    | "final-result"
    | "graph-output"
    | "supervisor-review"
    | string;
  /** The graph node / task the entry belongs to (null on pre-change
   *  rows). */
  node: string | null;
  /** The deep_agent_states row of the task/step (null for final-result
   *  and legacy rows). */
  state_id: number | null;
  /** The ONE chained LLM context (uuid hex) of the node conversation
   *  that produced the entry — joins llm_requests.chain_id (null when
   *  no LLM work produced the entry or on pre-change rows). */
  llm_context_id: string | null;
  payload: unknown;
  created_at: string;
}

/** One segment of the run's three-graph chain (2026-11-06): plan /
 *  custom / result by chain position; "single" = a legacy or degraded
 *  one-thread run. */
export interface AgentThreadSegment {
  thread_id: string;
  graph_type: "plan" | "custom" | "result" | "single";
}

/** GET /api/ai/threads/:threadId — the WHOLE CHAIN's graph (any member
 *  id resolves the run; the service merges every segment's rows, with
 *  segment k>0's runner-lane node names namespaced "s<k>:…" — the
 *  canvas node ids join on them). */
export interface AgentThreadDetail {
  thread_id: string;
  /** The chain's ROOT thread id (= the run's super_thread_id; equals
   *  thread_id on legacy single-thread runs). */
  super_thread_id?: string;
  /** The chain's segments in order (single "single" entry when the run
   *  predates the three-graph refactor). */
  segments?: AgentThreadSegment[];
  states: AgentThreadState[];
  contexts: AgentThreadContext[];
  node_states: AgentNodeState[];
  llm_contexts: AgentLlmContext[];
  /** Per-context LLM traffic rollup (token consumption + busy time),
   *  keyed by context_id in the usageByContext lookups. */
  llm_usage: AgentLlmUsage[];
}

/** One llm.llm_requests row attributed to a task. A chain-bound row's
 *  request_payload is INCREMENTAL — `_chain_incremental` /
 *  `_chain_shared_messages` inside the payload flag it (the shared
 *  conversation stored once in the chain's first row); the response
 *  payload is the final attempt's full response body. */
export interface AgentStateLlmRequest {
  request_id: string;
  model: string | null;
  status_code: number | null;
  is_failed: boolean;
  failure_reason: string | null;
  duration_ms: number | null;
  /** Usage token counts from the response's usage block (null on rows
   *  recorded before the columns existed or responses without usage). */
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  prev_request_id: string | null;
  request_payload: unknown;
  response_payload: unknown;
  created_at: string;
}

/** GET /threads/:threadId/states/:stateId/llm-requests — one task's
 *  recorded requests: a top-level step's rows are its context's chain
 *  rows between its start/finish ticks; a subagent child owns its whole
 *  chain. */
export interface AgentStateLlmRequests {
  state_id: number;
  llm_context_id: string | null;
  requests: AgentStateLlmRequest[];
}

const PENDING_OR_RUNNING = new Set(["pending", "in_progress"]);

/** A thread is still running while any of its states (steps OR spawned
 *  subagents) is pending/in_progress, or a runner-lane node state (the
 *  LangGraph store's real statuses) is running — the poll-on condition. */
export function threadIsActive(detail: AgentThreadDetail): boolean {
  return (
    detail.states.some((s) => PENDING_OR_RUNNING.has(s.status)) ||
    detail.node_states.some((s) => s.status === "running")
  );
}

/** Fetch one thread's graph. null = unknown / malformed thread id (404). */
export async function fetchAgentThread(
  threadId: string,
): Promise<AgentThreadDetail | null> {
  const res = await fetch(`/api/ai/threads/${encodeURIComponent(threadId)}`, {
    cache: "no-store",
  });
  if (res.status === 404) return null;
  if (!res.ok) {
    throw new Error(`thread fetch failed (HTTP ${res.status})`);
  }
  return (await res.json()) as AgentThreadDetail;
}

/** Fetch one task's recorded LLM requests/responses. null = unknown
 *  thread / task (404). Uncached: a live run ticks new requests in. */
export async function fetchStateLlmRequests(
  threadId: string,
  stateId: number,
): Promise<AgentStateLlmRequests | null> {
  const res = await fetch(
    `/api/ai/threads/${encodeURIComponent(threadId)}/states/${stateId}/llm-requests`,
    { cache: "no-store" },
  );
  if (res.status === 404) return null;
  if (!res.ok) {
    throw new Error(`task llm-requests fetch failed (HTTP ${res.status})`);
  }
  return (await res.json()) as AgentStateLlmRequests;
}
