/**
 * AI Analysis Summary service — bridges POST /api/ai/summary to the
 * deep-agents FastAPI server (llm_agents.deep_agents.server, the
 * always-on daemon on 127.0.0.1:8766, watchdog-managed like the MCP
 * daemon).
 *
 * Transport: everything dynamic about the run — the selected analysis
 * items (enriched with their insight-registry page docs + metric
 * db-search recipes), the selected prompt keys, the classification-nav
 * scope picks (the multi-pick subject list), the optional focus
 * question — is ONE JSON object POSTed to
 * /run as the agent's structured data. The goal itself is static text;
 * it teaches the agent to resolve the prompt keys from llm.ai_prompts
 * with its own DB tools.
 *
 * Node's fetch (undici) caps headers/body waits at 300s — a deep-agent
 * run legitimately exceeds that — so this goes over node:http with an
 * overall deadline and TCP keep-alive pings (the WSL2 localhost relay
 * drops long-idle sockets, and the response only arrives at the end).
 *
 * Bounded by a PARALLEL-RUN CAP read from the daemon's service_config
 * (service_config/llm_agents/deep_agents/server.json
 * ``max_parallel_runs``, default 1): the daemon itself is
 * concurrent-safe (one MCP session per run, a shared read-only provider
 * transport), so several generates may run at once — a generate past
 * the cap resolves as at-cap (rejected fast, never queued: a run lands
 * in the 1–6 min range and queueing would blow the caller's deadline).
 * The cap is config, not code — raise it on the Service Config page.
 * A running run is cancellable: POST /stop to the daemon cancels its
 * in-flight run task (the langgraph thread winds down at its current
 * await, the thread rows settle with the stop), and the still-pending
 * /run responds stopped: true — surfaced to the UI as a stopped run,
 * not an error. The bridge's stop forwards an optional thread_id, so a
 * caller may cancel one run of several; the thread-less form (the UI's
 * single Stop button) cancels every active run.
 */
import { request } from "http";
import type { QueryResultRow } from "pg";
import { getClient, queryRows } from "../lib/db.js";
import { renderSqlView, sqlFrag } from "../lib/sql-views.js";
import { loadSqlWrite } from "./sql-writes.service.js";
import {
  maxParallelRunsFor,
  readDeepAgentLimits,
} from "./static-config.service.js";
import {
  listInsightPageDocs,
  listInsightTemplates,
  type InsightPageDoc,
  type InsightTemplateRow,
} from "./analysis-insights.service.js";
import { LABEL_CODE, labelDecodeSql } from "./label-codes.js";
import { listAiPrompts, type AiPromptRow } from "./ai-prompts.service.js";
import {
  listDocsWithLabels,
  type DocLabeledRow,
  type DocNoteEntry,
  type DocSeriesEntry,
} from "./docs.service.js";

/** The deep-agents FastAPI daemon (llm_agents/deep_agents/server.py). */
const DEEP_AGENTS_HOST = "127.0.0.1";
const DEEP_AGENTS_PORT = 8766;
/** The daemon's service name in the service_config/ tree — where the
 *  parallel-run cap lives (max_parallel_runs). */
const DEEP_AGENTS_SERVICE = "llm_agents.deep_agents.server";
/** Overall deadline for one run (the CLI's step loop makes this a
 *  backstop, not the normal bound) — 5 HOURS, matching the daemon's
 *  own run_timeout_sec budget (the step lane routes to finalize at the
 *  same wall clock, so the bridge never cuts a live run short; the
 *  client's abort sits at the same value too). */
const RUN_DEADLINE_MS = 5 * 60 * 60_000;

/** The optional-docs budget (the static.docs label contract's "too many
 *  prompts" line): a run carrying MORE effective prompts than this skips
 *  its loosely-matched optional docs entirely — bound-matched docs always
 *  ride, whatever the run weighs. */
const DOC_OPTIONAL_PROMPT_CAP = 6;

/** One selected analysis item as the route hands it over. */
export interface AiSummaryAnalysisItem {
  /** Stable category/item key, e.g. "commons/ma-spread". */
  key: string;
  label: string;
  description?: string;
  /** The item's insight-registry page identity when it has one. */
  insight?: { tab: string; page: string };
}

export interface AiSummaryRequest {
  analysisItems: AiSummaryAnalysisItem[];
  /** llm.ai_prompts prompt_key values (the skills + plan rows) — the
   *  agent fetches their instruction texts itself and applies them. */
  prompts?: string[];
  /** Run-scoped label overrides (the UI Labels editor's "just this run"
   *  saves) — prompt identity ("key|sub_type") → the row's effective
   *  label arrays for THIS run. Never persisted. */
  promptLabels?: Record<string, PromptLabelSets>;
  /** The classification-nav MULTI-PICK scope list — one entry per picked
   *  subject (the summary page's tag bar); empty/absent = market-wide.
   *  The legacy singular `scope` below folds in as a one-entry list. */
  scopes?: AiSummaryScope[];
  /** Legacy single-pick scope (one subject) — normalized into `scopes`. */
  scope?: AiSummaryScope;
  /** Optional focus instruction — the agent treats it as the user's ask. */
  question?: string;
  /** A SAVED PLAN TEMPLATE id (llm.deep_agent_plan_templates — the
   *  Thread Graph Save button's placeholder-templated plan): the run's
   *  plan node materializes the template's steps with THIS run's input
   *  args, bypassing the LLM planning session (fail-soft — an unusable
   *  template falls back to the normal proposal). */
  planTemplateId?: number;
}

/** A prompt row's two label arrays (the Labels editor's effective sets). */
export interface PromptLabelSets {
  bound: string[];
  optional: string[];
}

/** One run-matched static.docs row as it rides in the agent's data. */
export interface AiSummaryDocEntry {
  doc_id: string;
  title: string | null;
  intro: string | null;
  series: DocSeriesEntry[] | null;
  notes: DocNoteEntry[] | null;
}

/** The classification-nav pick that focuses the summary. */
export interface AiSummaryScope {
  secType: "industry" | "index" | "etf" | "stock";
  code?: string;
  industry?: string;
  name?: string;
}

export interface AiSummaryResult {
  success: boolean;
  answer: string;
  model?: string;
  steps?: number;
  nToolCalls?: number;
  /** The run's deep-agent thread id (llm.deep_agent_states) when the
   *  run planned + persisted; absent otherwise. */
  threadId?: string;
  /** The plan steps' final states (the thread snapshot) when planned. */
  plan?: AiSummaryPlanStep[];
  /** True when the run was cancelled via stopAnalysisSummary (the
   *  daemon settled the thread; answer is empty). */
  stopped?: boolean;
  /** Failure detail (spawn/HTTP/step-limit) when success is false. */
  stderrTail?: string;
}

/** One plan step's final state — a llm.deep_agent_states row the
 *  runner's finalize node snapshotted into the /run response. */
export interface AiSummaryPlanStep {
  state_id: number;
  ordinal: number;
  node: string;
  title: string;
  status: "pending" | "in_progress" | "completed" | "failed" | "skipped";
}

/** One metric spec as it rides in the agent's data (the registry's own
 *  identity + its db-search recipe — VALUES are the agent's job, it pulls
 *  them with the DB tools). */
interface AiSummaryMetricSpec {
  metric: string;
  label: string;
  unit: string;
  description: string;
  scope: string;
  db_keywords: Record<string, unknown>;
}

/** One analysis item as it rides in the agent's data. */
interface AiSummaryItemData {
  key: string;
  label: string;
  description?: string;
  insight?: {
    tab: string;
    page: string;
    title?: string;
    doc?: string | null;
    keywords?: string[];
    metrics: AiSummaryMetricSpec[];
  };
}

/** The /run body's `data` — the shape the goal text explains. */
interface AiSummaryAgentData {
  source: "analysis-summary";
  question?: string;
  /** The MULTI-PICK scope list (the classification nav's subject tags) —
   *  one entry per picked subject; absent = market-wide. */
  scopes?: AiSummaryScope[];
  analysis_items: AiSummaryItemData[];
  /** llm.ai_prompts prompt_key values (the skills + plan rows) — the
   *  deep agent's loading ladder resolves which load in full (plan rows
   *  + eager/forced/verdict skills) and which ride the prompt catalog
   *  as per-step by-key fetches. */
  prompts: string[];
  /** Run-scoped label overrides (prompt identity → effective arrays) —
   *  present only when the request carried session-scoped label edits. */
  prompt_labels?: Record<string, PromptLabelSets>;
  /** The run-matched static.docs rows (the bound exact label join
   *  always; loose optional matches only within DOC_OPTIONAL_PROMPT_CAP)
   *  — the authored UI-element semantics this run's prompts cover. */
  docs?: AiSummaryDocEntry[];
}

/** Static goal: tells each agent conversation what the structured data
 *  is, how the run's prompts arrive through the GRADUAL LOADING LADDER
 *  (loaded skills ride the message in full; the rest sit in the prompt
 *  catalog, fetched one row by prompt_key only when a step works that
 *  skill's domain — never wholesale), what the scope focus means, and
 *  what document to write. The run executes as a step graph
 *  (llm_agents/deep_agents/langgraph/runner.py): the plan node lays out research
 *  steps from this framing, each step runs as its OWN node/conversation
 *  carrying the earlier steps' findings, and the final write step
 *  composes ONE coherent markdown document — ticking is automatic
 *  (never call update_plan_step); a step reworks the not-yet-run plan
 *  via update_plan when its findings demand it. The summary's audience
 *  reads the CN-market knowledge base, so the default output language
 *  is Chinese; the data's `question` overrides per run. */
const SUMMARY_GOAL =
  "Generate the analysis summary described by the structured context. " +
  "The run's prompts (llm.ai_prompts rows picked in the data's " +
  "'prompts' array) arrive through the loading ladder: the ones this " +
  "run works under ride your message IN FULL (the [Loaded prompts] / " +
  "[Step-matched prompts] blocks; 'prompt_instructions' in the data — " +
  "apply them to the whole run), the rest sit in the PROMPT CATALOG " +
  "(prompt_key + one preview line, 'prompt_catalog' in the data) — " +
  "call load_prompt with ONE prompt_key the moment your work enters " +
  "that skill's domain (or fetch that one row from llm.ai_prompts " +
  "when your toolkit lacks the tool), never wholesale; " +
  "prompt_type 1 rows are approach instructions " +
  "(skills), and the 'prompt_labels' bound-label force-matching already " +
  "happened at plan time. When the data carries 'docs' (static.docs " +
  "rows: doc_id / title / intro / series / notes), " +
  "those are the authored description texts of the UI elements (charts, " +
  "panels, table columns) whose label scopes this run's prompts cover: " +
  "apply them as description context for the related analysis items — " +
  "they say what each lens plots and means, not values to quote. Their " +
  "intro / series / notes are {{param}} templates: read a placeholder " +
  "as a field name (the run carries no UI runtime values) and never " +
  "treat an unresolved template as a number. " +
  "The run walks a planned step graph: you are ONE research " +
  "step of it — work YOUR step only (its directive and the findings so " +
  "far ride in your instructions), make your answer a self-contained " +
  "finding with the numbers, and when you are the FINAL writing step, " +
  "compose ONE coherent markdown document from the findings: a ## " +
  "section per analysis item (level, recent change, statistical " +
  "rarity), followed by cross-item takeaways and risks. When your " +
  "findings change the remaining plan, call update_plan (add / revise " +
  "remove the not-yet-run steps; delegate_goal forks a step to a " +
  "subagent). " +
  "The data's 'scopes', when present, is the LIST of subject picks " +
  "that focus the WHOLE summary: each entry {sec_type, code} names a " +
  "security (index/etf/stock) and {sec_type: 'industry', industry} " +
  "names an industry entity — filter every metric query to the LISTED " +
  "subjects (equality on the recipe's code/sec_type columns; a metric " +
  "may report one row per subject), read each item's section as those " +
  "subjects' reading (a one-entry list behaves as a single subject), " +
  "and prefer cross-subject comparison over one-subject-at-a-time " +
  "repetition when several are listed (the display name rides in each " +
  "entry's 'name'); when 'scopes' is absent the summary is " +
  "market-wide (All Market — every metric query spans the whole " +
  "market, no subject filter). " +
  "Then, for each entry of analysis_items: when the item carries an " +
  "insight page, use its metric db_keywords recipes with the DB tools " +
  "(db_list_tables / db_describe_table / db_run_query, narrow aggregate " +
  "queries) to ground the item's registered metrics in their actual " +
  "values. Reply with the finding (or, for the writing step, the " +
  "markdown document) ONLY — no preamble, no working notes. The data's " +
  "'question' field, when present, is the user's focus instruction and " +
  "defines the answer language; write in Chinese when it is absent.";

/** One POST /run over node:http (undici's fetch would 300s-timeout on a
 *  response the server only sends when the whole run finishes). The
 *  response is fully buffered server-side, so the socket is SILENT for
 *  the whole run — a socket-inactivity timeout would just be a second,
 *  shorter deadline; the explicit deadline timer below is the only
 *  bound. */
function postRun(
  body: {
    goal: string;
    data: AiSummaryAgentData;
    max_steps: number;
    plan_template_id?: number | null;
  },
): Promise<{ status: number; text: string }> {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(body);
    const req = request(
      {
        host: DEEP_AGENTS_HOST,
        port: DEEP_AGENTS_PORT,
        path: "/run",
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(payload),
        },
      },
      (res) => {
        let text = "";
        res.on("data", (c: Buffer) => {
          text += c.toString();
        });
        res.on("end", () =>
          resolve({ status: res.statusCode ?? 0, text }),
        );
      },
    );
    const deadline = setTimeout(() => {
      req.destroy(new Error(`deep agent run exceeded ${RUN_DEADLINE_MS / 60000} min`));
    }, RUN_DEADLINE_MS);
    // Keep-alive pings: the WSL2 localhost relay is known to drop idle
    // TCP connections, and this socket is silent for the whole run.
    req.on("socket", (socket) => {
      socket.setKeepAlive(true, 30_000);
    });
    req.on("error", (err) => {
      clearTimeout(deadline);
      reject(err);
    });
    req.on("close", () => clearTimeout(deadline));
    req.end(payload);
  });
}

// ---------------------------------------------------------------------------
// Run-matched docs — static.docs joins the run's prompts through the
// shared bound_labels vocabulary (contract:
// database/sql/static/docs/00_schema.sql):
//   • bound_labels — a doc MUST ride when its bound_labels share any
//     exact (case-insensitive equal) phrase with the effective prompts'
//     bound_labels; the budget never drops a bound doc.
//   • optional_labels — loosely matched (case-insensitive substring hit
//     in the run's question or the effective prompts' label vocabulary)
//     and loaded ONLY while the run's effective prompt count stays
//     within DOC_OPTIONAL_PROMPT_CAP — too many prompts already
//     selected, the loose docs skip.
// The effective prompt set mirrors what the goal text teaches the agent:
// the picked keys plus the skills rows (prompt_type 1) whose bound
// labels substring-match the question; a request's prompt_labels
// override replaces the persisted arrays for that row.
// ---------------------------------------------------------------------------

/** The run's effective prompt rows — picked ∪ question-force-loaded
 *  skills rows, each carrying its run-effective label arrays. */
export function effectivePromptRows(
  registry: AiPromptRow[],
  pickedKeys: string[],
  question: string | undefined,
  overrides: Record<string, PromptLabelSets> | undefined,
): AiPromptRow[] {
  const picked = new Set(pickedKeys);
  const q = (question ?? "").toLowerCase();
  const out: AiPromptRow[] = [];
  for (const row of registry) {
    const override = overrides?.[`${row.prompt_key}|${row.prompt_sub_type}`];
    const bound = override?.bound ?? row.bound_labels;
    const optional = override?.optional ?? row.optional_labels;
    const forceLoaded =
      !picked.has(row.prompt_key) &&
      row.prompt_type === 1 &&
      q !== "" &&
      bound.some((label) => label !== "" && q.includes(label.toLowerCase()));
    if (picked.has(row.prompt_key) || forceLoaded) {
      out.push({ ...row, bound_labels: bound, optional_labels: optional });
    }
  }
  return out;
}

function toDocEntry(doc: DocLabeledRow): AiSummaryDocEntry {
  return {
    doc_id: doc.doc_id,
    title: doc.title,
    intro: doc.intro,
    series: doc.series,
    notes: doc.notes,
  };
}

/** The run-matched docs (bound first, then in-budget loose), doc_id
 *  order within each tier. */
export function matchDocsForRun(
  docs: DocLabeledRow[],
  prompts: AiPromptRow[],
  question: string | undefined,
): AiSummaryDocEntry[] {
  const promptBound = new Set(
    prompts.flatMap((p) =>
      (p.bound_labels ?? []).map((label) => label.toLowerCase()),
    ),
  );
  const promptLoose = prompts
    .flatMap((p) => [
      ...(p.bound_labels ?? []),
      ...(p.optional_labels ?? []),
    ])
    .map((label) => label.toLowerCase())
    .filter((label) => label !== "");
  const q = (question ?? "").toLowerCase();

  const looseIds = new Set<string>();
  const winners: AiSummaryDocEntry[] = [];
  for (const doc of docs) {
    if (
      (doc.bound_labels ?? []).some(
        (label) => label !== "" && promptBound.has(label.toLowerCase()),
      )
    ) {
      winners.push(toDocEntry(doc));
      continue;
    }
    if (
      q !== "" &&
      (doc.optional_labels ?? []).some(
        (label) =>
          label !== "" &&
          (q.includes(label.toLowerCase()) ||
            promptLoose.some((hay) => hay.includes(label.toLowerCase()))),
      )
    ) {
      looseIds.add(doc.doc_id);
    }
  }
  if (prompts.length <= DOC_OPTIONAL_PROMPT_CAP) {
    for (const doc of docs) {
      if (looseIds.has(doc.doc_id)) winners.push(toDocEntry(doc));
    }
  }
  return winners;
}

/** Enrich one item with its insight-registry page doc + metric specs
 *  (both 10-min-TTL cached by analysis-insights.service). */
function enrichItem(
  item: AiSummaryAnalysisItem,
  templates: InsightTemplateRow[],
  pageDocs: InsightPageDoc[],
): AiSummaryItemData {
  const out: AiSummaryItemData = {
    key: item.key,
    label: item.label,
    description: item.description,
  };
  if (!item.insight) return out;
  out.insight = {
    tab: item.insight.tab,
    page: item.insight.page,
    metrics: templates
      .filter(
        (t) => t.tab === item.insight!.tab && t.page === item.insight!.page,
      )
      .map((t) => ({
        metric: t.metric,
        label: t.label,
        unit: t.unit ?? "",
        description: t.description ?? "",
        scope: t.spec?.scope ?? "security",
        db_keywords: t.db_keywords ?? {},
      })),
  };
  const doc = pageDocs.find(
    (d) => d.tab === item.insight!.tab && d.page === item.insight!.page,
  );
  if (doc) {
    out.insight.title = doc.title;
    out.insight.doc = doc.doc;
    out.insight.keywords = doc.keywords;
  }
  return out;
}

/** In-flight generate count against the config cap (max_parallel_runs
 *  of the daemon's service_config entry, default 1) — checked+incremented
 *  synchronously at generate entry, decremented in the finally, so the
 *  admission test is race-free within the single-threaded event loop. */
let activeRuns = 0;

/** The configured parallel-generation cap (re-read per generate — the
 *  static-config read is mtime-cached, so a Service Config page save
 *  takes effect on the next click without a bridge restart). */
function maxParallelSummaryRuns(): number {
  return maxParallelRunsFor(DEEP_AGENTS_SERVICE);
}

// ---------------------------------------------------------------------------
// Run history — the Analysis Summary page's date-event strip source, read in
// TWO phases (the strip renders fast; the heavy per-thread rows wait for a
// pick):
//   1. DATES (listSummaryRunDates — GET /summary-runs/dates): one aggregate
//      row per GMT operational day that has runs — the run count + per-status
//      step totals the DateEventStrip's dots render. No per-thread grouping,
//      no plan-goal JSONB extraction, no identity join unless the nav pick is
//      present.
//   2. DAY ROWS (listSummaryRuns with a date — GET /summary-runs?date=): the
//      picked day's per-thread rows (goal, per-thread counts, times, scope),
//      one indexed gmt-day range on llm.deep_agent_states
//      (ix_deep_agent_states_gmt_day).
// The day bucket is the GMT operational day of the thread's first state (the
// same boundary as llm.llm_requests.date — a new day starts at Beijing
// 08:00). The run's scope pick (the classification-nav subject that focused
// it) rides in from llm.deep_agent_identities — the code <-> thread_id link
// the runner's plan node writes (llm_agents/deep_agents/planning/store.py),
// so the nav-filtered read is one indexed lookup, not a states/contexts scan.
// Uncached — a just-finished generation must appear immediately.
// ---------------------------------------------------------------------------

/** One date bucket of the strip's first-phase read — exactly what the
 *  DateEventStrip's dot renders (no per-thread rows: those come from the
 *  picked day's listSummaryRuns({ date }) read). `steps_*` aggregate every
 *  state of every run that day; a bucket with steps_total above the settled
 *  sum has an in-flight (or crashed-unsettled) run on it. */
export interface SummaryRunDateRow extends QueryResultRow {
  /** GMT operational day (YYYY-MM-DD). */
  run_date: string;
  runs_total: number;
  steps_total: number;
  steps_completed: number;
  steps_failed: number;
  steps_skipped: number;
}

/** One planned deep-agent run for the run-history strip. */
export interface SummaryRunRow extends QueryResultRow {
  thread_id: string;
  /** GMT operational day of the run's first state (YYYY-MM-DD). */
  run_date: string;
  /** The plan's goal (llm.deep_agent_contexts kind='plan') when planned. */
  goal: string | null;
  /** All thread states — plan steps + spawned-subagent child states. */
  steps_total: number;
  steps_completed: number;
  steps_failed: number;
  steps_skipped: number;
  /** First state's created_at, Shanghai-local text. */
  first_at: string;
  /** Last settle time, Shanghai-local text (null when nothing settled). */
  last_at: string | null;
  /** The run's scope pick when scoped (null = market-wide / unpersisted). */
  scope_sec_type: string | null;
  scope_code: string | null;
  scope_name: string | null;
}

/** The nav filter for the run-history reads — the classification-nav
 *  pick. secTypeCode arrives already encoded (the route maps the label
 *  name via label-codes.ts); codes is the MULTI-PICK subject set (the
 *  summary page's tag list — a run scoped to ANY of them matches; the
 *  legacy single `code` folds in as a one-entry set). Either axis alone
 *  filters on that axis; both = the exact pick. `date` (YYYY-MM-DD, the
 *  GMT operational day) narrows the per-thread rows to ONE day — the
 *  strip's second-phase read; the dates read omits it. `threadId` is the
 *  Summary History's search-by-id lookup — a case-insensitive PREFIX
 *  match (the runner's ids are uuid4().hex, so a pasted full id is the
 *  exact case and a typed prefix narrows); the UI sends it alone, but if
 *  nav filters ride too they AND-combine like any other pair. */
export interface SummaryRunFilters {
  secTypeCode?: number | null;
  code?: string | null;
  codes?: string[] | null;
  date?: string | null;
  threadId?: string | null;
}

/** The WHERE parts shared by the two read phases: the day predicate (an
 *  exact match on the indexed gmt-day expression) and the nav pick (an
 *  EXISTS on the identities link). The pick is INCLUSIVE of market-wide
 *  runs: a thread with NO identity row never carried a scope pick, and a
 *  market-wide summary covers every subject — so a scoped strip shows
 *  the pick's runs plus the unscoped ones (badge marks them in the UI). */
function summaryRunFilterConds(
  params: unknown[],
  filters: SummaryRunFilters,
  includeDate: boolean,
): string {
  const conds: string[] = [];
  if (includeDate && filters.date != null) {
    params.push(filters.date);
    conds.push(
      `((s.created_at AT TIME ZONE 'GMT')::date) = $${params.length}::date`,
    );
  }
  const codes =
    filters.codes !== undefined && filters.codes !== null
      ? filters.codes.filter((c) => c !== "")
      : filters.code !== undefined && filters.code !== null && filters.code !== ""
        ? [filters.code]
        : [];
  const identityConds: string[] = [];
  if (filters.secTypeCode != null) {
    params.push(filters.secTypeCode);
    identityConds.push(`i.sec_type = $${params.length}`);
  }
  if (codes.length > 0) {
    params.push(codes);
    identityConds.push(`i.code = ANY($${params.length})`);
  }
  if (identityConds.length > 0) {
    conds.push(`(EXISTS (SELECT 1 FROM llm.deep_agent_identities i
              WHERE i.thread_id = COALESCE(s.super_thread_id, s.thread_id)
                AND ${identityConds.join(" AND ")})
              OR NOT EXISTS (SELECT 1 FROM llm.deep_agent_identities m
              WHERE m.thread_id = COALESCE(s.super_thread_id, s.thread_id)))`);
  }
  // The search-by-id prefix — LIKE-specials escaped so a pasted id is
  // taken literally (the ids are hex, but the input is user text). Any
  // CHAIN member's id matches its run: the root matches directly (the
  // members share it as super_thread_id) and a member id matches through
  // an EXISTS over the run's threads — so pasting ANY of the run's three
  // thread ids opens the one run, whose row (and the Thread Graph's
  // header) carries the SUPER id.
  if (filters.threadId != null && filters.threadId !== "") {
    params.push(filters.threadId.replace(/[\\%_]/g, "\\$&") + "%");
    const p = `$${params.length}`;
    conds.push(
      `(COALESCE(s.super_thread_id, s.thread_id) ILIKE ${p} ` +
        `OR EXISTS (SELECT 1 FROM llm.deep_agent_states m ` +
        `WHERE COALESCE(m.super_thread_id, m.thread_id) = ` +
        `COALESCE(s.super_thread_id, s.thread_id) AND m.thread_id ILIKE ${p}))`,
    );
  }
  return conds.length > 0 ? `WHERE ${conds.join(" AND ")}` : "";
}

/** Phase 1 — the strip's date buckets, most recent day first. ONE ROW
 *  PER RUN: the three-graph chain's threads group under the chain key
 *  (COALESCE(super_thread_id, thread_id) — legacy runs are their own
 *  key). */
export async function listSummaryRunDates(
  limit = 200,
  filters: Omit<SummaryRunFilters, "date"> = {},
): Promise<SummaryRunDateRow[]> {
  const params: unknown[] = [];
  const where = summaryRunFilterConds(params, filters, false);
  // The SELECT lives in the sql view
  // data_viz/api/services/ai-summary/run_dates (where_clause rides a
  // fragment; its params stay caller-assembled; LIMIT is a clamped
  // literal fragment).
  return queryRows<SummaryRunDateRow>(
    renderSqlView("data_viz/api/services/ai-summary/run_dates", {
      where_clause: sqlFrag(where),
      limit: sqlFrag(`${limit}`),
    }).sql,
    params,
  );
}

export async function listSummaryRuns(
  limit = 200,
  filters: SummaryRunFilters = {},
): Promise<SummaryRunRow[]> {
  const params: unknown[] = [];
  const where = summaryRunFilterConds(params, filters, true);
  // The SELECT lives in the sql view data_viz/api/services/ai-summary/runs
  // (where_clause / plan-kind / decode fragments; params caller-assembled).
  return queryRows<SummaryRunRow>(
    renderSqlView("data_viz/api/services/ai-summary/runs", {
      where_clause: sqlFrag(where),
      plan_kind: sqlFrag(`${LABEL_CODE.agent_context_kind.plan}`),
      scope_sec_type_decode: sqlFrag(labelDecodeSql("sec_type", "i.sec_type")),
      limit: sqlFrag(`${limit}`),
    }).sql,
    params,
  );
}

/** The one-thread delete outcome ("missing" = no states row for the id,
 *  "running" = the daemon still reports the thread active). */
export type DeleteSummaryRunResult = "deleted" | "missing" | "running";

/** DELETE one RUN'S WHOLE CHAIN — the Summary History dropdown's
 *  per-entry delete (any member id resolves the run; the entry's id is
 *  the chain root). The runs-prune purge order
 *  (09_deep_agent_runs_prune.sql) and plan_store.purge_super_thread,
 *  applied to EVERY chain member in ONE transaction: each thread's
 *  llm.llm_requests chains FIRST (a request's chain_id is the node's
 *  llm_context_id — resolved from the states, the store's node items
 *  and the context registry while the linking rows still exist;
 *  monitor.external_resource_audit is process-level with no thread
 *  linkage and stays), then states (the self-FK cascades
 *  spawned-subagent children), the thread's contexts, its identity
 *  link, its deep-agent store items (namespace[1:2] carries the thread
 *  id) and the framework-managed langgraph checkpoint rows (all keyed
 *  by thread_id). A thread the daemon still reports ACTIVE is refused
 *  ("running") — the in-flight generation must settle (or be stopped)
 *  before its history entry can go; a crashed run's stuck-open thread
 *  is NOT active and deletes fine (exactly the junk the auto-prune,
 *  which never purges open threads, can never reach). */
export async function deleteSummaryRun(
  threadId: string,
): Promise<DeleteSummaryRunResult> {
  const active = await listActiveSummaryRuns();
  if (active.some((r) => r.thread_id === threadId)) return "running";
  const client = await getClient();
  try {
    await client.query("BEGIN");
    // Resolve the run's WHOLE CHAIN (the three threads share the root's
    // super_thread_id; legacy runs resolve to themselves). The SELECT
    // lives in the sql view
    // data_viz/api/services/ai-summary/delete_chain_members.
    const members = await client.query<{ thread_id: string }>(
      renderSqlView(
        "data_viz/api/services/ai-summary/delete_chain_members",
        { thread_id: threadId },
      ).sql,
      [threadId],
    );
    const chain = members.rows.map((r) => r.thread_id);
    // The probe SELECT lives in the sql view
    // data_viz/api/services/ai-summary/run_state_count (rendered here so
    // it keeps running through the delete transaction's own client).
    const probeView = renderSqlView(
      "data_viz/api/services/ai-summary/run_state_count",
      { thread_id: threadId },
    );
    const probe = await client.query<{ n: number }>(probeView.sql, probeView.params);
    if (chain.length === 0 || (probe.rows[0]?.n ?? 0) === 0) {
      await client.query("ROLLBACK");
      return "missing";
    }
    // The per-member DELETEs live in the sql write actions
    // database/sql_writes/data_viz/api/<table>/delete_for_thread — the
    // runs-prune purge order (09_deep_agent_runs_prune.sql) and
    // plan_store.purge_super_thread applied to EVERY chain member in ONE
    // transaction: each thread's llm.llm_requests chains FIRST (a
    // request's chain_id is the node's llm_context_id — resolved from the
    // states, the store's node items and the context registry while the
    // linking rows still exist; monitor.external_resource_audit is
    // process-level with no thread linkage and stays), then states (the
    // self-FK cascades spawned-subagent children), the thread's contexts,
    // its identity link, its deep-agent store items (namespace[1:2]
    // carries the thread id) and the framework-managed langgraph
    // checkpoint rows (all keyed by thread_id).
    for (const member of chain) {
      await client.query(
        loadSqlWrite("data_viz", "api", "llm_requests", "delete_for_thread"),
        [member],
      );
      await client.query(
        loadSqlWrite("data_viz", "api", "deep_agent_states", "delete_for_thread"),
        [member],
      );
      await client.query(
        loadSqlWrite("data_viz", "api", "deep_agent_contexts", "delete_for_thread"),
        [member],
      );
      await client.query(
        loadSqlWrite("data_viz", "api", "deep_agent_identities", "delete_for_thread"),
        [member],
      );
      await client.query(
        loadSqlWrite("data_viz", "api", "deep_agent_store_items", "delete_for_thread"),
        [["deep_agent", member]],
      );
      await client.query(
        loadSqlWrite("data_viz", "api", "langgraph_checkpoints", "delete_for_thread"),
        [member],
      );
      await client.query(
        loadSqlWrite("data_viz", "api", "langgraph_checkpoint_blobs", "delete_for_thread"),
        [member],
      );
      await client.query(
        loadSqlWrite("data_viz", "api", "langgraph_checkpoint_writes", "delete_for_thread"),
        [member],
      );
    }
    await client.query("COMMIT");
    return "deleted";
  } catch (err) {
    await client.query("ROLLBACK");
    throw err;
  } finally {
    client.release();
  }
}

export async function generateAnalysisSummary(
  payload: AiSummaryRequest,
): Promise<AiSummaryResult> {
  const cap = maxParallelSummaryRuns();
  if (activeRuns >= cap) {
    return {
      success: false,
      answer: "",
      stderrTail:
        `${activeRuns} summary generation(s) already running ` +
        `(max_parallel_runs ${cap}) — try again when one finishes`,
    };
  }
  activeRuns++;
  try {
    const [templates, pageDocs, registry, labeledDocs] = await Promise.all([
      listInsightTemplates(),
      listInsightPageDocs(),
      listAiPrompts(),
      listDocsWithLabels(),
    ]);
    const runDocs = matchDocsForRun(
      labeledDocs,
      effectivePromptRows(
        registry,
        payload.prompts ?? [],
        payload.question,
        payload.promptLabels,
      ),
      payload.question,
    );
    // The scope list — the multi-pick tags as sent, or the legacy single
    // pick folded in as a one-entry list; deduped on (secType, id) so a
    // re-sent tag never double-links the thread.
    const scopes = [
      ...new Map(
        (payload.scopes ?? (payload.scope ? [payload.scope] : [])).map(
          (s) => [`${s.secType}:${s.code ?? s.industry ?? ""}`, s],
        ),
      ).values(),
    ];
    const data: AiSummaryAgentData = {
      source: "analysis-summary",
      question: payload.question,
      ...(scopes.length > 0 ? { scopes } : {}),
      analysis_items: payload.analysisItems.map((item) =>
        enrichItem(item, templates, pageDocs),
      ),
      prompts: payload.prompts ?? [],
      // Run-scoped label overrides ride only when the session has some —
      // absent keeps the agent data to the persisted registry state.
      ...(payload.promptLabels !== undefined &&
      Object.keys(payload.promptLabels).length > 0
        ? { prompt_labels: payload.promptLabels }
        : {}),
      // The run-matched UI docs ride only when the label join found some
      // — same absent-means-persisted-state shape as prompt_labels.
      ...(runDocs.length > 0 ? { docs: runDocs } : {}),
    };

    let res: { status: number; text: string };
    try {
      // The per-conversation step budget comes from the static
      // deep_agent_config (the Run Limits panel edits it): a full
      // 13-item selection needs many db queries and the agent batches
      // several tool calls per step — a 12-step budget only fits one
      // or two items. A plan_template_id rides along when the run
      // starts from a saved template (zero planning LLM calls).
      res = await postRun({
        goal: SUMMARY_GOAL,
        data,
        max_steps: readDeepAgentLimits().max_conversation_steps,
        plan_template_id: payload.planTemplateId ?? null,
      });
    } catch (err) {
      return {
        success: false,
        answer: "",
        stderrTail:
          `deep-agents server unreachable or timed out (${String(err)}) — ` +
          `is ${DEEP_AGENTS_HOST}:${DEEP_AGENTS_PORT} up?`,
      };
    }
    if (res.status !== 200) {
      return {
        success: false,
        answer: "",
        stderrTail: `deep agent failed (HTTP ${res.status}): ${res.text.slice(-500)}`,
      };
    }
    let parsed: {
      answer?: string;
      steps?: number;
      n_tool_calls?: number;
      model?: string;
      thread_id?: string;
      stopped?: boolean;
      plan?: { state_id?: number; ordinal?: number; node?: string;
               title?: string; status?: string }[];
      detail?: string;
    };
    try {
      parsed = JSON.parse(res.text);
    } catch {
      return {
        success: false,
        answer: "",
        stderrTail: `deep agent returned non-JSON: ${res.text.slice(-300)}`,
      };
    }
    const PLAN_STATUSES = new Set([
      "pending", "in_progress", "completed", "failed", "skipped",
    ]);
    const plan = (Array.isArray(parsed.plan) ? parsed.plan : []).filter(
      (s): s is AiSummaryPlanStep =>
        typeof s.state_id === "number" &&
        typeof s.ordinal === "number" &&
        typeof s.node === "string" &&
        typeof s.title === "string" &&
        typeof s.status === "string" &&
        PLAN_STATUSES.has(s.status),
    );
    // Stopped run: the daemon cancelled it per POST /stop and settled
    // the thread — success transport-wise (the empty answer is the stop,
    // not a failure); the settled plan still rides along so the UI can
    // show how far the run got.
    if (parsed.stopped === true) {
      return {
        success: true,
        stopped: true,
        answer: "",
        threadId:
          typeof parsed.thread_id === "string" ? parsed.thread_id : undefined,
        plan: plan.length > 0 ? plan : undefined,
      };
    }
    if (typeof parsed.answer !== "string" || parsed.answer === "") {
      return {
        success: false,
        answer: "",
        stderrTail: parsed.detail ?? "deep agent reported no answer",
      };
    }
    return {
      success: true,
      answer: parsed.answer,
      model: typeof parsed.model === "string" ? parsed.model : undefined,
      steps: typeof parsed.steps === "number" ? parsed.steps : undefined,
      nToolCalls:
        typeof parsed.n_tool_calls === "number" ? parsed.n_tool_calls : undefined,
      threadId: typeof parsed.thread_id === "string" ? parsed.thread_id : undefined,
      plan: plan.length > 0 ? plan : undefined,
    };
  } finally {
    activeRuns--;
  }
}

/** One POST /stop's outcome (mirrors the daemon's StopResponse). */
export interface StopSummaryResult {
  /** How many in-flight runs were actually cancelled (0 = none). */
  stopped: number;
  thread_ids: string[];
}

/** One in-flight daemon run (mirrors the daemon's ActiveRun). */
export interface ActiveSummaryRun {
  thread_id: string;
  started_at: number;
}

/** GET the daemon's /active — the in-flight run(s)' thread ids. The
 *  UI's live-view poll reads this THROUGH the bridge (the daemon is
 *  WSL-local): it opens the thread graph the moment the run registers,
 *  before the plan phase persists any rows. Daemon down → empty. */
export async function listActiveSummaryRuns(): Promise<ActiveSummaryRun[]> {
  try {
    const res = await fetch(
      `http://${DEEP_AGENTS_HOST}:${DEEP_AGENTS_PORT}/active`,
      { signal: AbortSignal.timeout(5_000) },
    );
    if (!res.ok) return [];
    const parsed = (await res.json()) as { active?: ActiveSummaryRun[] };
    return Array.isArray(parsed.active) ? parsed.active : [];
  } catch {
    return [];
  }
}

/** POST /stop to the daemon — cancel in-flight summary run(s). With a
 *  thread_id, only that run; without, EVERY active run (the pre-parallel
 *  contract the UI's single Stop button still uses — with the bridge
 *  admitting several parallel runs, "every active" is all of them).
 *  The still-pending generateAnalysisSummary for a cancelled run then
 *  resolves with stopped. stopped === 0 means nothing was running. */
export async function stopAnalysisSummary(
  threadId?: string,
): Promise<StopSummaryResult> {
  const res = await fetch(
    `http://${DEEP_AGENTS_HOST}:${DEEP_AGENTS_PORT}/stop`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(
        threadId ? { thread_id: threadId } : {},
      ),
    },
  );
  if (!res.ok) {
    throw new Error(`deep-agents /stop failed (HTTP ${res.status})`);
  }
  return (await res.json()) as StopSummaryResult;
}
