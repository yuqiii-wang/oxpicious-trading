/**
 * AI plan-template client — the saved LangGraph PLAN templates (the
 * type-0 rows of llm.deep_agent_langgraph_templates): a finished run's plan JSON,
 * placeholder-templated by the server ({{question}} / {{code}} /
 * {{name}} — the planner template_args vocabulary) so a later generate
 * can start from it and SKIP the LLM planning node (the plan
 * materializes from the template with the new run's input args, zero
 * planning LLM calls).
 *
 * Save: the Thread Graph's Save button (a settled run with a persisted
 * plan). Reuse: the Analysis Summary page's template picker beside
 * Generate — generateAnalysisSummary's planTemplateId.
 */

/** One saved plan template — GET /api/ai/plan-templates rows. */
export interface AiPlanTemplate {
  template_id: number;
  /** Display name — UNIQUE server-side; saving under an existing name
   *  overwrites it. */
  name: string;
  /** The static.label_dim 'langgraph_type' code — 0 plan (the loadable
   *  saved plans), 1 custom (reserved), 2 result (the seeded
   *  review-wave definition; the picker filters to plan rows). */
  type: number;
  /** The source run's thread id (provenance only — a template outlives
   *  its run; deleting the run keeps the template). */
  thread_id: string | null;
  /** The SOURCE run's template args ({question, code, name, sec_type})
   *  — what the placeholders replaced; never rendered at reuse. */
  args: Record<string, unknown>;
  /** The templated plan payload {"steps": [...]} with {{key}} placeholders. */
  plan_json: { steps?: unknown[] } & Record<string, unknown>;
  n_steps: number;
  /** Which {{key}} placeholders occur in plan_json (computed server-side
   *  on read — the picker's caption, the save dialog's confirmation). */
  placeholders: string[];
  created_at: string;
  updated_at: string;
}

/** GET /api/ai/plan-templates — the picker rows, name order. */
export async function fetchPlanTemplates(): Promise<AiPlanTemplate[]> {
  const res = await fetch("/api/ai/plan-templates", { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`plan templates fetch failed (HTTP ${res.status})`);
  }
  return (await res.json()) as AiPlanTemplate[];
}

/** POST /api/ai/plan-templates {threadId, name} — the Thread Graph Save
 *  button: template the settled run's plan and upsert under the name.
 *  Throws the server's error message (no plan to template, bad name). */
export async function savePlanTemplate(
  threadId: string,
  name: string,
): Promise<AiPlanTemplate> {
  const res = await fetch("/api/ai/plan-templates", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ threadId, name }),
  });
  const body = (await res.json().catch(() => null)) as
    | AiPlanTemplate
    | { error?: string }
    | null;
  if (!res.ok) {
    throw new Error(
      (body as { error?: string } | null)?.error ??
        `plan template save failed (HTTP ${res.status})`,
    );
  }
  return body as AiPlanTemplate;
}

/** DELETE /api/ai/plan-templates/:templateId — the picker's delete. */
export async function deletePlanTemplate(templateId: number): Promise<void> {
  const res = await fetch(
    `/api/ai/plan-templates/${encodeURIComponent(String(templateId))}`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as
      | { error?: string }
      | null;
    throw new Error(
      body?.error ?? `plan template delete failed (HTTP ${res.status})`,
    );
  }
}
