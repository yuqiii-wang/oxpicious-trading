/**
 * AI Analysis Summary client — generate the AI section's analysis summary
 * from the AnalysisSummaryPage's selection, and fetch its prompts catalog
 * (llm.ai_prompts via GET /api/ai/prompts).
 *
 * The generation request body IS the spec of what to summarize, in JSON:
 * the selected analysis items (key + label + optional insight-registry
 * identity) and the picked prompt_key values — the skills- and plan-type
 * llm.ai_prompts rows (the deep agent fetches the matching instruction
 * texts from llm.ai_prompts itself). The server enriches the items from
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
  /** The row's domain within its prompt_type ('' = none): skills rows
   *  carry db_query | online_search | biz_guide, tool rows the tool
   *  kit/domain (sandbox | delegation | plan_main | plan_fork | plan |
   *  mcp) — the picker nests the rows into foldable sub_type sections
   *  inside each type. */
  prompt_sub_type: string;
  /** label_dim prompt_type code: 0 system | 1 skills | 2 plan |
   *  3 internal | 4 tool. */
  prompt_type: number;
  /** API-decoded prompt_type label ("system" | "skills" | "plan" |
   *  "internal" | "tool"). */
  prompt_type_label: string;
  title: string;
  /** Revision no. within (prompt_type, title) — bumped by one on every
   *  saved edit of the instruction (prompt_key itself never changes). */
  version: number;
  instruction: string;
  /** One-sentence summary shown as the picker row's caption. */
  preview: string;
  note: string | null;
  /** Force-load keywords — a run question containing one force-loads the
   *  prompt alongside the picked rows (contract: 06_ai_prompts.sql).
   *  Mostly Chinese terms, one distinctive phrase per element. */
  bound_labels: string[];
  /** Search labels — the prompt's find vocabulary (lowercase English /
   *  alias phrases; '{}' = unlabeled). */
  optional_labels: string[];
  ordinal: number;
}

/**
 * A prompt row's UI identity — prompt_key flattened with its sub_type
 * ("key|sub"), the same join the deep agent's tool-prompt lookup uses.
 * prompt_key alone is NOT unique (update_plan exists once per lane as
 * the plan_main / plan_fork rows), so row identity — the open state,
 * the save round-trip, the React keys — always goes through this.
 */
export function promptIdentity(prompt: AiPrompt): string {
  return `${prompt.prompt_key}|${prompt.prompt_sub_type}`;
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
 *  responding with the refreshed row. promptSubType narrows the match to
 *  the one (prompt_key, prompt_sub_type) row — the PK — so duplicate-key
 *  rows (update_plan's two lanes) save independently. */
export async function updateAiPrompt(
  promptKey: string,
  instruction: string,
  promptSubType?: string,
): Promise<AiPrompt> {
  const res = await fetch(`/api/ai/prompts/${encodeURIComponent(promptKey)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ instruction, prompt_sub_type: promptSubType }),
  });
  if (!res.ok) {
    throw new Error(`prompt save failed (HTTP ${res.status})`);
  }
  return (await res.json()) as AiPrompt;
}

/** A prompt row's two label arrays (the Labels editor's payload — sent
 *  as the FULL effective sets; the server replaces wholesale, trimming
 *  and deduping, lowercasing the optional group). */
export interface PromptLabelSets {
  bound: string[];
  optional: string[];
}

/** PUT /api/ai/prompts/:promptKey/labels — the Labels editor's permanent
 *  save: replaces the row's bound_labels / optional_labels arrays and
 *  responds with the refreshed row (the picker + open view follow).
 *  promptSubType narrows the match to the one PK row, exactly like the
 *  instruction save. */
export async function updateAiPromptLabels(
  promptKey: string,
  labels: PromptLabelSets,
  promptSubType?: string,
): Promise<AiPrompt> {
  const res = await fetch(
    `/api/ai/prompts/${encodeURIComponent(promptKey)}/labels`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        bound_labels: labels.bound,
        optional_labels: labels.optional,
        prompt_sub_type: promptSubType,
      }),
    },
  );
  if (!res.ok) {
    throw new Error(`prompt labels save failed (HTTP ${res.status})`);
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
  /** llm.ai_prompts prompt_key values (the skills + plan rows) — the
   *  agent resolves their instruction texts itself. */
  prompts?: string[];
  /** Run-scoped label overrides for the Labels editor's "just this run"
   *  saves — prompt identity (promptIdentity, "key|sub_type") → the row's
   *  EFFECTIVE label arrays for this run (the DB arrays + the session
   *  additions − the session removals). Rides into the agent's structured
   *  data as prompt_labels; never persisted. */
  promptLabels?: Record<string, PromptLabelSets>;
  /** The classification-nav scope picks — the multi-selected subject
   *  tags, one entry each (absent/empty = market-wide). The page always
   *  sends the plural; the singular `scope` below is the legacy
   *  single-pick form the route still accepts. */
  scopes?: AiSummaryScope[];
  /** Legacy single-pick form (one subject) — the route folds it into the
   *  same scope list. */
  scope?: AiSummaryScope;
  /** Optional focus instruction for the run. */
  question?: string;
  /** A SAVED PLAN TEMPLATE id (llm.deep_agent_plan_templates — the
   *  Thread Graph Save button's placeholder-templated plan): the run's
   *  plan materializes from the template with THIS run's scope/question
   *  args, bypassing the LLM planning session (fail-soft — an unusable
   *  template falls back to the normal planning proposal). */
  planTemplateId?: number;
}

/** One plan step's final state — a llm.deep_agent_states row from the
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

/** One deep-agent run (multi-step LLM tool loop) — see file header.
 *  5 HOURS, the same total budget the daemon enforces
 *  (deep_agent_config's run_timeout_sec — the step lane routes to
 *  finalize there, so this abort is the LAST resort, and its error is
 *  the friendly line below). Individual LLM requests are capped at
 *  llm_request_timeout_sec (default 5 min) server-side — an overrun
 *  terminates the request and the harness refactors the input into
 *  smaller tasks instead of waiting it out. */
const AI_SUMMARY_TIMEOUT_MS = 18_000_000;

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
  } catch (err) {
    if (controller.signal.aborted) {
      throw new Error(
        `summary generation timed out after ${AI_SUMMARY_TIMEOUT_MS / 60_000} min — ` +
        "the run keeps going on the agent daemon; reopen its thread graph " +
        "from Summary History to watch it finish",
      );
    }
    throw err;
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
// Run history — one row per planned deep-agent run (a llm.deep_agent_states
// thread); the Analysis Summary page's date-event strip source. The run's
// scope pick (the classification-nav subject that focused it) rides in from
// llm.deep_agent_identities — the code <-> thread_id link the runner's plan
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

/** One date bucket of the strip's first-phase read — GET
 *  /api/ai/summary-runs/dates: the per-day aggregates the DateEventStrip's
 *  dots render. A bucket with steps_total above the settled sum
 *  (completed + failed + skipped) has an in-flight (or crashed-unsettled)
 *  run on it — the live view's date-level "unsettled" signature. */
export interface AiSummaryRunDate {
  /** GMT operational day of the runs' first states (YYYY-MM-DD). */
  run_date: string;
  runs_total: number;
  steps_total: number;
  steps_completed: number;
  steps_failed: number;
  steps_skipped: number;
}

/** The classification-nav filter for the run-history reads — secType is
 *  the label name (index|etf|stock|industry, encoded API-side), code the
 *  subject id (security code or industry id); `codes` is its multi-pick
 *  form (repeated code params — the summary page's subject tags) and
 *  wins over a lone `code` when both ride. Either axis alone filters on
 *  that axis; both = the exact pick set; absent = every run. The pick is
 *  INCLUSIVE of market-wide runs: threads with no scope pick (no identity
 *  row — their scope_* fields come back null) cover every subject, so a
 *  scoped strip lists them alongside the pick's own runs. `date`
 *  (YYYY-MM-DD) narrows the run rows to ONE day — the strip's picked-dot
 *  read; the dates read never takes it. `threadId` is the Summary
 *  History's search-by-id lookup — a case-insensitive PREFIX the rows
 *  read matches server-side; the search sends it alone (no nav/date
 *  filters) so the lookup finds the run whatever the strip scopes to,
 *  and the dates read never takes it either. */
export interface AiSummaryRunFilters {
  secType?: string;
  code?: string;
  codes?: string[];
  date?: string;
  threadId?: string;
}

/** GET /api/ai/summary-runs/dates — the strip's first-phase read: one
 *  aggregate row per day that has runs (runs count + per-status step
 *  totals), most recent day first. No per-thread rows, no goal payload —
 *  the light read that renders the bar before any dot is picked. Same
 *  no-store fetch as fetchSummaryRuns (LIVE run state — the generation
 *  flow re-reads it every 3s while a run is in flight). */
export async function fetchSummaryRunDates(
  filters?: AiSummaryRunFilters,
  limit = 200,
): Promise<AiSummaryRunDate[]> {
  const qs = new URLSearchParams();
  if (filters?.secType) qs.set("sec_type", filters.secType);
  for (const code of filters?.codes ?? []) qs.append("code", code);
  if (filters?.code && !filters?.codes) qs.set("code", filters.code);
  qs.set("limit", String(limit));
  const res = await fetch(`/api/ai/summary-runs/dates?${qs.toString()}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`summary run dates fetch failed (HTTP ${res.status})`);
  }
  return (await res.json()) as AiSummaryRunDate[];
}

/** GET /api/ai/summary-runs — planned runs, most recent first; with
 *  filters.date the read narrows to that ONE day (the picked dot's
 *  per-thread rows — the strip's second phase) and with filters.threadId
 *  to the search-by-id prefix (the Summary History's search box — it
 *  rides alone, no nav/date filters). Plain no-store fetch (NOT the
 *  shared fetchJson): this is LIVE run state — the generation flow
 *  polls it while a run is in flight, and a 10-min-TTL cache entry
 *  captured before the run would keep serving the empty pre-run list for
 *  the whole run (the endpoint has no version source to invalidate it). */
export async function fetchSummaryRuns(
  filters?: AiSummaryRunFilters,
  limit = 200,
): Promise<AiSummaryRun[]> {
  const qs = new URLSearchParams();
  if (filters?.secType) qs.set("sec_type", filters.secType);
  for (const code of filters?.codes ?? []) qs.append("code", code);
  if (filters?.code && !filters?.codes) qs.set("code", filters.code);
  if (filters?.date) qs.set("date", filters.date);
  if (filters?.threadId) qs.set("thread_id", filters.threadId);
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
