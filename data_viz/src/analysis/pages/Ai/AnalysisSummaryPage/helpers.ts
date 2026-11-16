/** The AnalysisSummaryPage's pure predicates + normalizers. */
import type { SecNavSecType } from "@/shared/components/sec-nav";
import type {
  AiPrompt,
  AiSummaryRun,
  AiSummaryRunDate,
  PromptLabelSets,
} from "@/lib/api-client/aiSummary";

/** itemKind per sec_type (the SecNavShell mapping); "all" has no L3 row. */
export function itemKindFor(
  secType: SecNavSecType,
): "ETF" | "Index" | "Stock" | "Industry" | undefined {
  return secType === "etf"
    ? "ETF"
    : secType === "index"
      ? "Index"
      : secType === "industry"
        ? "Industry"
        : secType === "stock"
          ? "Stock"
          : undefined;
}

/** The picker's selectable prompt types — the skills approach
 *  instructions and the plan prompt (system rows stay unmanaged). */
export function isSelectablePrompt(prompt: AiPrompt): boolean {
  return prompt.prompt_type_label === "skills" || prompt.prompt_type_label === "plan";
}

/** The tool-type rows — the model-callable tools' schema descriptions:
 *  the runner resolves the active rows automatically at every run start
 *  (tools.base's override contract), so they are picker VIEW/EDIT rows,
 *  never selected into the request. */
export function isToolPrompt(prompt: AiPrompt): boolean {
  return prompt.prompt_type_label === "tool";
}

/** Normalize one typed label the way the server saves it (trimmed, the
 *  optional group lowercased) so a run-only label matches its later
 *  permanent form. */
export function normalizeLabel(label: string, group: keyof PromptLabelSets): string {
  const trimmed = label.trim();
  return group === "optional" ? trimmed.toLowerCase() : trimmed;
}

/** A run row that is still live — the server-computed `is_live` flag
 *  (any member state open OR the chain root's store namespace holding a
 *  running node item — the chain-orchestrator node:super spans the run),
 *  falling back to the old count heuristic for an older payload without
 *  the flag (the runner inserts the plan rows pending at run start;
 *  finalize settles the leftovers either way). */
export function hasUnsettledSteps(r: AiSummaryRun): boolean {
  if (typeof r.is_live === "boolean") return r.is_live;
  return r.steps_total > r.steps_completed + r.steps_failed + r.steps_skipped;
}

/** The same signature at the DATE-BUCKET level (the phase-1 aggregates):
 *  a bucket whose steps are not all settled has an in-flight (or
 *  crashed-unsettled) run on that day — the fallback open's candidate
 *  detector, over rows the page never fetches wholesale anymore. */
export function dateUnsettled(d: AiSummaryRunDate): boolean {
  return d.steps_total > d.steps_completed + d.steps_failed + d.steps_skipped;
}
