/**
 * AI Analysis Summary client — generate the AI section's analysis summary
 * from the AnalysisSummaryPage's selection, and fetch its prompts catalog
 * (text.ai_prompts via GET /api/ai/prompts).
 *
 * The generation request body IS the spec of what to summarize, in JSON:
 * the selected analysis items (key + label + optional insight-registry
 * identity) and the picked prompt_key values — the skills- and plan-type
 * text.ai_prompts rows (the deep agent fetches the matching instruction
 * texts from text.ai_prompts itself). The server enriches the items from
 * the insight registry and runs the deep agent
 * (llm_agents.deep_agents.server) — a tool-calling loop over the
 * read-only DB, so this takes minutes, not seconds.
 *
 * Plain fetch for the generation (not fetchJson): a POST that runs a
 * multi-minute deep-agent loop server-side — one generous-timeout
 * attempt, no blind retry (a retry would double-run the agent). The run
 * is cancellable while it runs: stopAnalysisSummary() POSTs
 * /api/ai/summary/stop, the daemon cancels the langgraph thread, and
 * the still-pending generate then resolves with stopped: true (empty
 * answer; the plan snapshot settled with the stop).
 */
export interface AiPrompt {
  prompt_key: string;
  /** label_dim prompt_type code: 0 system | 1 skills | 2 plan. */
  prompt_type: number;
  /** API-decoded prompt_type label ("system" | "skills" | "plan"). */
  prompt_type_label: string;
  title: string;
  /** Revision no. within (prompt_type, title) — bumped by one on every
   *  saved edit of the instruction (prompt_key itself never changes). */
  version: number;
  instruction: string;
  note: string | null;
  ordinal: number;
}

/** GET /api/ai/prompts — the prompts catalog (active rows, all types). */
export async function fetchAiPrompts(): Promise<AiPrompt[]> {
  const res = await fetch("/api/ai/prompts");
  if (!res.ok) {
    throw new Error(`prompts fetch failed (HTTP ${res.status})`);
  }
  return (await res.json()) as AiPrompt[];
}

/** PUT /api/ai/prompts/:promptKey — save an edited instruction; the
 *  server replaces the row's text in place and bumps its version by one,
 *  responding with the refreshed row. */
export async function updateAiPrompt(
  promptKey: string,
  instruction: string,
): Promise<AiPrompt> {
  const res = await fetch(`/api/ai/prompts/${encodeURIComponent(promptKey)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ instruction }),
  });
  if (!res.ok) {
    throw new Error(`prompt save failed (HTTP ${res.status})`);
  }
  return (await res.json()) as AiPrompt;
}

export interface AiSummaryItemRequest {
  /** Stable category/item key, e.g. "commons/ma-spread". */
  key: string;
  label: string;
  description?: string;
  /** The item's insight-registry page (tab/page) when registered. */
  insight?: { tab: string; page: string };
}

/** The classification-nav pick that focuses the summary — sec_type plus
 *  the security code (index/etf/stock) or the industry id (industry
 *  entities). Absent = market-wide. */
export interface AiSummaryScope {
  secType: "industry" | "index" | "etf" | "stock";
  /** Security code (sec_type index/etf/stock). */
  code?: string;
  /** Industry id (sec_type industry). */
  industry?: string;
  /** Display name of the pick (from the nav tree) — prompt context. */
  name?: string;
}

export interface AiSummaryRequest {
  analysisItems: AiSummaryItemRequest[];
  /** text.ai_prompts prompt_key values (the skills + plan rows) — the
   *  agent resolves their instruction texts itself. */
  prompts?: string[];
  /** The classification-nav scope pick (absent = market-wide). */
  scope?: AiSummaryScope;
  /** Optional focus instruction for the run. */
  question?: string;
}

/** One plan step's final state — a text.deep_agent_states row from the
 *  run's thread snapshot (present when the run planned + persisted). */
export interface AiSummaryPlanStep {
  state_id: number;
  ordinal: number;
  /** The graph node the step belongs to (its work tag). */
  node: string;
  title: string;
  status: "pending" | "in_progress" | "completed" | "failed" | "skipped";
}

export interface AiSummaryResponse {
  success: boolean;
  answer: string;
  model?: string;
  steps?: number;
  n_tool_calls?: number;
  /** The run's deep-agent thread id when the run planned + persisted. */
  thread_id?: string;
  /** The plan steps' final states, in plan order. */
  plan?: AiSummaryPlanStep[];
  /** True when the run was cancelled via stopAnalysisSummary while it
   *  ran (answer is empty; plan/thread_id carry the settled thread). */
  stopped?: boolean;
  error?: string;
}

/** One deep-agent run (multi-step LLM tool loop) — see file header. */
const AI_SUMMARY_TIMEOUT_MS = 900_000;

export async function generateAnalysisSummary(
  req: AiSummaryRequest,
): Promise<AiSummaryResponse> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), AI_SUMMARY_TIMEOUT_MS);
  try {
    const res = await fetch("/api/ai/summary", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
      signal: controller.signal,
    });
    const text = await res.text();
    if (!res.ok) {
      throw new Error(
        `summary generation failed (HTTP ${res.status}): ${text.slice(0, 300)}`,
      );
    }
    return JSON.parse(text) as AiSummaryResponse;
  } finally {
    clearTimeout(timer);
  }
}

export interface AiSummaryStopResult {
  /** How many in-flight runs were actually cancelled (0 = the run had
   *  already finished on its own — the pending generate still resolves
   *  normally with its answer). */
  stopped: number;
  thread_ids: string[];
}

/** One in-flight run as GET /api/ai/summary/active reports it. */
export interface AiActiveSummaryRun {
  thread_id: string;
  started_at: number;
}

/** GET /api/ai/summary/active — the in-flight run(s)' thread ids, empty
 *  when idle. The live view polls this from the Generate click onward:
 *  the daemon registers the run (and its thread id) the moment the
 *  bridge's POST lands — ~20-80s before the plan phase persists any
 *  rows the strip could surface. Plain no-store fetch (live state); the
 *  route answers { active: [...] } and the array is unwrapped here
 *  (defensively — a malformed body reads as empty, never a crash). */
export async function fetchActiveSummaryRuns(): Promise<AiActiveSummaryRun[]> {
  const res = await fetch("/api/ai/summary/active", { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`active runs fetch failed (HTTP ${res.status})`);
  }
  const body = (await res.json()) as { active?: AiActiveSummaryRun[] };
  return Array.isArray(body.active) ? body.active : [];
}

/** GET /api/ai/summary/stop — cancel the in-flight summary run (the
 *  pending generateAnalysisSummary then resolves with stopped: true). */
export async function stopAnalysisSummary(): Promise<AiSummaryStopResult> {
  const res = await fetch("/api/ai/summary/stop", { method: "POST" });
  if (!res.ok) {
    throw new Error(`summary stop failed (HTTP ${res.status})`);
  }
  return (await res.json()) as AiSummaryStopResult;
}

// ---------------------------------------------------------------------------
// Run history — one row per planned deep-agent run (a text.deep_agent_states
// thread); the Analysis Summary page's date-event strip source. The run's
// scope pick (the classification-nav subject that focused it) rides in from
// text.deep_agent_identities — the code <-> thread_id link the runner's plan
// node writes.
// ---------------------------------------------------------------------------

export interface AiSummaryRun {
  thread_id: string;
  /** GMT operational day of the run's first state (YYYY-MM-DD). */
  run_date: string;
  /** The plan's goal when planned (null otherwise). */
  goal: string | null;
  /** All thread states — plan steps + spawned-subagent child states. */
  steps_total: number;
  steps_completed: number;
  steps_failed: number;
  steps_skipped: number;
  /** First state's created_at, Shanghai-local text. */
  first_at: string;
  last_at: string | null;
  /** The run's scope pick when scoped (null = market-wide). */
  scope_sec_type?: string | null;
  scope_code?: string | null;
  scope_name?: string | null;
}

/** The classification-nav filter for the run-history read — secType is
 *  the label name (index|etf|stock|industry, encoded API-side), code the
 *  subject id (security code or industry id). Either alone filters on
 *  that one axis; both = the exact pick; absent = every run. */
export interface AiSummaryRunFilters {
  secType?: string;
  code?: string;
}

/** GET /api/ai/summary-runs — recent planned runs, most recent first.
 *  With a nav pick the read narrows to runs scoped to it (the indexed
 *  text.deep_agent_identities lookup). Plain no-store fetch (NOT the
 *  shared fetchJson): this is LIVE run state — the generation flow polls
 *  it every 3s while a run is in flight, and a 10-min-TTL cache entry
 *  captured before the run would keep serving the empty pre-run list for
 *  the whole run (the endpoint has no version source to invalidate it). */
export async function fetchSummaryRuns(
  filters?: AiSummaryRunFilters,
  limit = 200,
): Promise<AiSummaryRun[]> {
  const qs = new URLSearchParams();
  if (filters?.secType) qs.set("sec_type", filters.secType);
  if (filters?.code) qs.set("code", filters.code);
  qs.set("limit", String(limit));
  const res = await fetch(`/api/ai/summary-runs?${qs.toString()}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`summary runs fetch failed (HTTP ${res.status})`);
  }
  return (await res.json()) as AiSummaryRun[];
}

/** DELETE /api/ai/summary-runs/:threadId — delete one run entry (the
 *  Summary History dropdown's hover-delete cross). The server purges the
 *  thread's states, contexts, identity link, store items and langgraph
 *  checkpoints in one transaction; a still-running run is refused (409)
 *  and an unknown thread 404s — both surface as the thrown error. */
export async function deleteSummaryRun(threadId: string): Promise<void> {
  const res = await fetch(
    `/api/ai/summary-runs/${encodeURIComponent(threadId)}`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as
      | { error?: string }
      | null;
    throw new Error(body?.error ?? `run delete failed (HTTP ${res.status})`);
  }
}
