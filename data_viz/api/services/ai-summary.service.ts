/**
 * AI Analysis Summary service — bridges POST /api/ai/summary to the
 * deep-agents FastAPI server (llm_agents.deep_agents.server, the
 * always-on daemon on 127.0.0.1:8766, watchdog-managed like the MCP
 * daemon).
 *
 * Transport: everything dynamic about the run — the selected analysis
 * items (enriched with their insight-registry page docs + metric
 * db-search recipes), the selected prompt keys, the classification-nav
 * scope pick, the optional focus question — is ONE JSON object POSTed to
 * /run as the agent's structured data. The goal itself is static text;
 * it teaches the agent to resolve the prompt keys from text.ai_prompts
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
import { maxParallelRunsFor } from "./static-config.service.js";
import {
  listInsightPageDocs,
  listInsightTemplates,
  type InsightPageDoc,
  type InsightTemplateRow,
} from "./analysis-insights.service.js";
import { LABEL_CODE, labelDecodeSql } from "./label-codes.js";

/** The deep-agents FastAPI daemon (llm_agents/deep_agents/server.py). */
const DEEP_AGENTS_HOST = "127.0.0.1";
const DEEP_AGENTS_PORT = 8766;
/** The daemon's service name in the service_config/ tree — where the
 *  parallel-run cap lives (max_parallel_runs). */
const DEEP_AGENTS_SERVICE = "llm_agents.deep_agents.server";
/** Overall deadline for one run (the CLI's step loop makes this a
 *  backstop, not the normal bound — runs land in the 1–6 min range). */
const RUN_DEADLINE_MS = 15 * 60_000;

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
  /** text.ai_prompts prompt_key values (the skills + plan rows) — the
   *  agent fetches their instruction texts itself and applies them. */
  prompts?: string[];
  /** The classification-nav scope pick (absent = market-wide). */
  scope?: AiSummaryScope;
  /** Optional focus instruction — the agent treats it as the user's ask. */
  question?: string;
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
  /** The run's deep-agent thread id (text.deep_agent_states) when the
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

/** One plan step's final state — a text.deep_agent_states row the
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
  scope?: AiSummaryScope;
  analysis_items: AiSummaryItemData[];
  /** text.ai_prompts prompt_key values (resolved by the agent; the
   *  skills + plan rows). */
  prompts: string[];
}

/** Static goal: tells each agent conversation what the structured data
 *  is, that the prompts list names text.ai_prompts rows (skills rows
 *  prompt_type 1) it must fetch itself and apply, what the scope focus
 *  means, and what document to write. The run executes as a step graph
 *  (llm_agents/deep_agents/graph/runner.py): the plan node lays out research
 *  steps from this framing, each step runs as its OWN node/conversation
 *  carrying the earlier steps' findings, and the final write step
 *  composes ONE coherent markdown document — ticking is automatic
 *  (never call update_plan_step); a step reworks the not-yet-run plan
 *  via update_plan when its findings demand it. The summary's audience
 *  reads the CN-market knowledge base, so the default output language
 *  is Chinese; the data's `question` overrides per run. */
const SUMMARY_GOAL =
  "Generate the analysis summary described by the structured context. " +
  "First resolve the prompts: the data's 'prompts' array lists prompt_key " +
  "values of the table text.ai_prompts — fetch their instruction texts " +
  "with one narrow query (SELECT prompt_key, prompt_type, instruction FROM " +
  "text.ai_prompts WHERE is_active AND prompt_type = 1) and apply them " +
  "to the whole run: prompt_type 1 rows are approach instructions " +
  "(skills). The run walks a planned step graph: you are ONE research " +
  "step of it — work YOUR step only (its directive and the findings so " +
  "far ride in your instructions), make your answer a self-contained " +
  "finding with the numbers, and when you are the FINAL writing step, " +
  "compose ONE coherent markdown document from the findings: a ## " +
  "section per analysis item (level, recent change, statistical " +
  "rarity), followed by cross-item takeaways and risks. When your " +
  "findings change the remaining plan, call update_plan (add / revise " +
  "remove the not-yet-run steps; delegate_goal forks a step to a " +
  "subagent). " +
  "The data's 'scope', when present, focuses the WHOLE summary on ONE " +
  "subject: {sec_type, code} names a security (index/etf/stock) and " +
  "{sec_type: 'industry', industry} names an industry entity — filter " +
  "every metric query to that subject (equality on the recipe's " +
  "code/sec_type columns; the display name rides in 'name'), and read " +
  "each item's section as THIS subject's reading; when scope is absent " +
  "the summary is market-wide. " +
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
  body: { goal: string; data: AiSummaryAgentData; max_steps: number },
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
// Run history — the Analysis Summary page's date-event strip source: one row
// per planned deep-agent run (a text.deep_agent_states thread; runs without
// a plan phase create no thread and never surface here). Times render as
// Shanghai text (the QA list pattern); the day bucket is the GMT operational
// day of the thread's first state (the same boundary as
// text.llm_requests.date — a new day starts at Beijing 08:00). The run's
// scope pick (the classification-nav subject that focused it) rides in from
// text.deep_agent_identities — the code <-> thread_id link the runner's plan
// node writes (llm_agents/deep_agents/planning/store.py), so the nav-filtered
// read below is one indexed lookup, not a states/contexts scan. Uncached —
// the table is tiny and a just-finished generation must appear immediately.
// ---------------------------------------------------------------------------

/** One planned deep-agent run for the run-history strip. */
export interface SummaryRunRow extends QueryResultRow {
  thread_id: string;
  /** GMT operational day of the run's first state (YYYY-MM-DD). */
  run_date: string;
  /** The plan's goal (text.deep_agent_contexts kind='plan') when planned. */
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

/** The nav filter for the run-history read — the classification-nav pick.
 *  secTypeCode arrives already encoded (the route maps the label name via
 *  label-codes.ts); code is the subject id (security code or industry id).
 *  Either alone filters on that one axis; both = the exact pick. */
export interface SummaryRunFilters {
  secTypeCode?: number | null;
  code?: string | null;
}

export async function listSummaryRuns(
  limit = 200,
  filters: SummaryRunFilters = {},
): Promise<SummaryRunRow[]> {
  const params: unknown[] = [limit];
  const conds: string[] = [];
  if (filters.secTypeCode != null) {
    params.push(filters.secTypeCode);
    conds.push(`i.sec_type = $${params.length}`);
  }
  if (filters.code != null) {
    params.push(filters.code);
    conds.push(`i.code = $${params.length}`);
  }
  // EXISTS (not a join): one identity per thread by design, but EXISTS
  // keeps the aggregation shape identical with and without the filter.
  const where =
    conds.length > 0
      ? `WHERE EXISTS (SELECT 1 FROM text.deep_agent_identities i
              WHERE i.thread_id = s.thread_id AND ${conds.join(" AND ")})`
      : "";
  return queryRows<SummaryRunRow>(
    `SELECT s.thread_id,
            ((s.created_at AT TIME ZONE 'GMT')::date)::text  AS run_date,
            MIN(p.payload ->> 'goal')                        AS goal,
            COUNT(*)::int                                    AS steps_total,
            COUNT(*) FILTER (WHERE s.status = 'completed')::int AS steps_completed,
            COUNT(*) FILTER (WHERE s.status = 'failed')::int    AS steps_failed,
            COUNT(*) FILTER (WHERE s.status = 'skipped')::int   AS steps_skipped,
            (MIN(s.created_at) AT TIME ZONE 'Asia/Shanghai')::text AS first_at,
            (MAX(s.finished_at) AT TIME ZONE 'Asia/Shanghai')::text AS last_at,
            i.code                                           AS scope_code,
            i.name                                           AS scope_name,
            ${labelDecodeSql("sec_type", "i.sec_type")}      AS scope_sec_type
       FROM text.deep_agent_states s
       LEFT JOIN text.deep_agent_contexts p
              ON p.thread_id = s.thread_id
             AND p.kind = ${LABEL_CODE.agent_context_kind.plan}
       LEFT JOIN LATERAL (
              SELECT sec_type, code, name
                FROM text.deep_agent_identities
               WHERE thread_id = s.thread_id
               LIMIT 1
       ) i ON true
       ${where}
      GROUP BY s.thread_id, 2, i.sec_type, i.code, i.name
      ORDER BY 2 DESC, MIN(s.created_at) DESC
      LIMIT $1`,
    params,
  );
}

/** The one-thread delete outcome ("missing" = no states row for the id,
 *  "running" = the daemon still reports the thread active). */
export type DeleteSummaryRunResult = "deleted" | "missing" | "running";

/** DELETE one run's thread — the Summary History dropdown's per-entry
 *  delete. The runs-prune purge order (11_deep_agent_runs_prune.sql) and
 *  plan_store.purge_thread, scoped to ONE thread_id in ONE transaction:
 *  the thread's text.llm_requests chains FIRST (a request's chain_id is
 *  the node's llm_context_id — resolved from the states, the store's
 *  node items and the context registry while the linking rows still
 *  exist; monitor.external_resource_audit is process-level with no
 *  thread linkage and stays), then states (the self-FK cascades
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
    const probe = await client.query<{ n: number }>(
      "SELECT COUNT(*)::int AS n FROM text.deep_agent_states WHERE thread_id = $1",
      [threadId],
    );
    if ((probe.rows[0]?.n ?? 0) === 0) {
      await client.query("ROLLBACK");
      return "missing";
    }
    await client.query(
      `DELETE FROM text.llm_requests
        WHERE chain_id IN (
              SELECT s.llm_context_id FROM text.deep_agent_states s
               WHERE s.thread_id = $1 AND s.llm_context_id IS NOT NULL
              UNION
              SELECT i.value ->> 'llm_context_id'
                FROM text.deep_agent_store_items i
               WHERE i.namespace[1:2] = ARRAY['deep_agent', $1]::text[]
                 AND i.value ->> 'llm_context_id' IS NOT NULL
              UNION
              SELECT i.key FROM text.deep_agent_store_items i
               WHERE i.namespace[1:3] = ARRAY['deep_agent', $1, 'contexts']::text[])`,
      [threadId],
    );
    await client.query(
      "DELETE FROM text.deep_agent_states WHERE thread_id = $1",
      [threadId],
    );
    await client.query(
      "DELETE FROM text.deep_agent_contexts WHERE thread_id = $1",
      [threadId],
    );
    await client.query(
      "DELETE FROM text.deep_agent_identities WHERE thread_id = $1",
      [threadId],
    );
    await client.query(
      "DELETE FROM text.deep_agent_store_items WHERE namespace[1:2] = $1::text[]",
      [["deep_agent", threadId]],
    );
    for (const table of [
      "langgraph.checkpoints",
      "langgraph.checkpoint_blobs",
      "langgraph.checkpoint_writes",
    ]) {
      await client.query(`DELETE FROM ${table} WHERE thread_id = $1`, [threadId]);
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
    const [templates, pageDocs] = await Promise.all([
      listInsightTemplates(),
      listInsightPageDocs(),
    ]);
    const data: AiSummaryAgentData = {
      source: "analysis-summary",
      question: payload.question,
      scope: payload.scope,
      analysis_items: payload.analysisItems.map((item) =>
        enrichItem(item, templates, pageDocs),
      ),
      prompts: payload.prompts ?? [],
    };

    let res: { status: number; text: string };
    try {
      // 40 steps: a full 13-item selection needs many db queries and the
      // agent batches several tool calls per step — the CLI default of 12
      // only fits one or two items.
      res = await postRun({ goal: SUMMARY_GOAL, data, max_steps: 40 });
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
