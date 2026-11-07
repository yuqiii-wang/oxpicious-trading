/**
 * AI plan-template service — SAVED LANGGRAPH TEMPLATES (renamed from
 * deep_agent_plan_templates 2026-11-06, the three-graph refactor): a
 * finished run's plan JSON, copied into
 * llm.deep_agent_langgraph_templates as a TYPE-PLAN row (the
 * static.label_dim 'langgraph_type' domain: 0 plan | 1 custom |
 * 2 result — the seeded default-result-wave row defines the result
 * graph's review seats; the picker shows every type) with the
 * run-specific VARIABLE parts replaced by {{key}} placeholders, so a
 * later generate can start from it and SKIP the LLM planning session
 * entirely (the daemon's plan session materializes the template with
 * the new run's input args — llm_agents/deep_agents/planning/
 * templates.py; DDL database/sql/llm/11_deep_agent_langgraph_
 * templates.sql).
 *
 * The placeholder vocabulary is the planner's template_args contract
 * (llm_agents/deep_agents/planning/planner.py): {{question}} the run's
 * focus question, {{code}} / {{name}} / {{sec_type}} the
 * classification-nav scope pick. The SAVE side (here) derives the
 * source run's values from the plan payload's 'args' block — the runner
 * stamps it into every kind 'plan' context at create_thread — falling
 * back to llm.deep_agent_identities for runs older than the feature
 * (code/name/sec_type only: the question is not recoverable and stays
 * literal). Substitution is longest-value-first over every string leaf
 * of the steps array, so a subject named inside the question
 * placeholders too.
 *
 *   GET    /api/ai/plan-templates           (routes/ai/plan-templates.ts)
 *   POST   /api/ai/plan-templates  {threadId, name}
 *   DELETE /api/ai/plan-templates/:templateId
 *
 * Save/list/delete are direct DB reads/writes (no daemon round-trip —
 * only the REUSE side runs in the daemon, as the /run request's
 * plan_template_id; only type-plan rows are loadable). Deleting the
 * source run keeps its templates: the row's thread_id is provenance
 * only (no FK, by design).
 */
import { queryRows } from "./db.service.js";
import { querySqlView, sqlFrag } from "../lib/sql-views.js";
import { loadSqlWrite } from "./sql-writes.service.js";
import { LABEL_CODE, labelDecodeSql } from "./label-codes.js";
import type { QueryResultRow } from "pg";

/** One saved LangGraph template — the generate form's picker row. */
export interface AiPlanTemplateRow {
  template_id: number;
  /** Display name — UNIQUE; saving under an existing name overwrites. */
  name: string;
  /** Which of the run's three graphs the template drives — the
   *  static.label_dim 'langgraph_type' code (always 0 plan here: the
   *  picker's rows are the saved plans this feature saves — the list
   *  filters type = 0 because only type-plan rows are loadable). */
  type: number;
  /** The source run's thread id (provenance only). */
  thread_id: string | null;
  /** The SOURCE run's template args ({question, code, name, sec_type} —
   *  what the placeholders replaced; never rendered at reuse time). */
  args: Record<string, unknown>;
  /** The templated plan payload {"steps": [...]} with {{key}} placeholders. */
  plan_json: { steps?: unknown[] } & Record<string, unknown>;
  n_steps: number;
  /** Which {{key}} placeholders actually occur in plan_json (computed on
   *  read — the picker's caption, the save dialog's confirmation). */
  placeholders: string[];
  created_at: string;
  updated_at: string;
}

/** A JSON-ish tree leaf-walker's value — the plan payload's shape. */
type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

/** The {{key}} placeholder syntax (mirrors planner._TEMPLATE_ARG_RE). */
const PLACEHOLDER_RE = /\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}/g;

/** Which placeholders occur in a templated plan's string leaves. */
function collectPlaceholders(value: Json, into: Set<string>): void {
  if (typeof value === "string") {
    for (const m of value.matchAll(PLACEHOLDER_RE)) into.add(m[1]);
  } else if (Array.isArray(value)) {
    for (const item of value) collectPlaceholders(item, into);
  } else if (value !== null && typeof value === "object") {
    for (const item of Object.values(value)) collectPlaceholders(item, into);
  }
}

/** Replace the source run's variable values with {{key}} placeholders
 *  over every string leaf, longest value first (a longer value — the
 *  question naming the subject — must win before the subject's own
 *  shorter code/name could partially match inside it). Records which
 *  keys actually landed. */
function templatize(
  value: Json,
  pairs: [key: string, val: string][],
  used: Set<string>,
): Json {
  if (typeof value === "string") {
    let out = value;
    for (const [key, val] of pairs) {
      if (out.includes(val)) {
        out = out.split(val).join(`{{${key}}}`);
        used.add(key);
      }
    }
    return out;
  }
  if (Array.isArray(value)) {
    return value.map((item) => templatize(item, pairs, used));
  }
  if (value !== null && typeof value === "object") {
    const out: { [key: string]: Json } = {};
    for (const [k, v] of Object.entries(value)) out[k] = templatize(v, pairs, used);
    return out;
  }
  return value;
}

/** The row as the API serves it (placeholders computed from plan_json). */
function toRow(raw: AiPlanTemplateRow & QueryResultRow): AiPlanTemplateRow {
  const found = new Set<string>();
  collectPlaceholders(raw.plan_json as Json, found);
  return { ...raw, placeholders: [...found].sort() };
}

/** GET the saved plan templates, name order — the generate form's picker. */
export async function listPlanTemplates(): Promise<AiPlanTemplateRow[]> {
  // The SELECT lives in the sql view
  // data_viz/api/services/ai-plan-template/list_templates.
  const rows = await querySqlView<AiPlanTemplateRow & QueryResultRow>(
    "data_viz/api/services/ai-plan-template/list_templates",
  );
  return rows.map(toRow);
}

/** POST's outcome — the saved row plus which placeholders the plan's
 *  variable parts became. */
export type SavePlanTemplateResult =
  | { ok: true; template: AiPlanTemplateRow }
  | { ok: false; error: string };

/** POST — save one finished run's plan as a placeholder template.
 *
 *  Reads the thread's kind 'plan' context payload (the plan as the
 *  runner persisted it) plus the run's template args (the payload's
 *  'args' block, identities fallback for older runs), replaces the
 *  variable values with {{question}} / {{code}} / {{name}} placeholders
 *  (sec_type is deliberately NOT templated — the bare word "index"
 *  would mangle every title it occurs in), and upserts under the name
 *  (UNIQUE — saving under an existing name overwrites it). */
export async function savePlanTemplate(
  threadId: string,
  name: string,
): Promise<SavePlanTemplateResult> {
  const trimmed = name.trim();
  if (trimmed === "") return { ok: false, error: "template name is empty" };
  if (trimmed.length > 120) {
    return { ok: false, error: "template name exceeds 120 chars" };
  }
  // The SELECT lives in the sql view
  // data_viz/api/services/ai-plan-template/plan_context.
  const planRows = await querySqlView<{ payload: Record<string, unknown> }>(
    "data_viz/api/services/ai-plan-template/plan_context",
    {
      thread_id: threadId,
      plan_kind_code: sqlFrag(String(LABEL_CODE.agent_context_kind.plan)),
    },
  );
  const payload = planRows[0]?.payload;
  const steps = payload?.steps;
  if (payload === undefined || !Array.isArray(steps) || steps.length === 0) {
    return {
      ok: false,
      error: `thread '${threadId}' has no persisted plan to template`,
    };
  }
  // The source run's template args: the payload's 'args' block (runner-
  // stamped since the templates feature), identities as the fallback for
  // older runs (code/name/sec_type — the question is not recoverable).
  const payloadArgs =
    payload.args !== null && typeof payload.args === "object" &&
    !Array.isArray(payload.args)
      ? (payload.args as Record<string, unknown>)
      : {};
  // The SELECT lives in the sql view
  // data_viz/api/services/ai-plan-template/thread_identity.
  const idRows = await querySqlView<{
    sec_type: string | null;
    code: string;
    name: string | null;
  }>(
    "data_viz/api/services/ai-plan-template/thread_identity",
    {
      thread_id: threadId,
      sec_type_decode: sqlFrag(labelDecodeSql("sec_type", "sec_type")),
    },
  );
  const identity = idRows[0] ?? null;
  const argStr = (key: string): string => {
    const fromPayload = payloadArgs[key];
    if (typeof fromPayload === "string" && fromPayload.trim() !== "") {
      return fromPayload;
    }
    if (key === "code") return identity?.code ?? "";
    if (key === "name") return identity?.name ?? "";
    if (key === "sec_type") return identity?.sec_type ?? "";
    return "";
  };
  const args: Record<string, string> = {
    question: argStr("question"),
    sec_type: argStr("sec_type"),
    code: argStr("code"),
    name: argStr("name"),
  };
  // Longest value first; skip values too short to substitute safely (a
  // 1-char code would shred every word containing that letter).
  const pairs = (["question", "name", "code"] as const)
    .map((key) => [key, args[key]] as [string, string])
    .filter(([, val]) => val.length >= 2)
    .sort((a, b) => b[1].length - a[1].length);
  const used = new Set<string>();
  const planJson = { ...payload, steps: templatize(steps, pairs, used) };
  // The write lives in the sql write action
  // database/sql_writes/data_viz/api/deep_agent_langgraph_templates/
  // save_plan_template.
  const rows = await queryRows<AiPlanTemplateRow & QueryResultRow>(
    loadSqlWrite("data_viz", "api", "deep_agent_langgraph_templates", "save_plan_template"),
    [trimmed, LABEL_CODE.langgraph_type.plan, threadId,
     JSON.stringify(args), JSON.stringify(planJson), steps.length],
  );
  return { ok: true, template: toRow(rows[0]) };
}

/** DELETE — remove one saved template ("missing" when no row had the id). */
export async function deletePlanTemplate(
  templateId: number,
): Promise<"deleted" | "missing"> {
  // The write lives in the sql write action
  // database/sql_writes/data_viz/api/deep_agent_langgraph_templates/delete.
  const rows = await queryRows<{ template_id: number }>(
    loadSqlWrite("data_viz", "api", "deep_agent_langgraph_templates", "delete"),
    [templateId],
  );
  return rows.length > 0 ? "deleted" : "missing";
}
