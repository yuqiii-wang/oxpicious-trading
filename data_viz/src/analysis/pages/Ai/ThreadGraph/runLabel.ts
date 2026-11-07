/**
 * runLabel — one run's dropdown/picker label: its operational date +
 * first-state time, a scope badge (label + code from the identity link;
 * runs with no scope pick badge "market-wide" — scoped strips include
 * them), a goal tail, and a running marker while the thread is unsettled
 * (steps outstanding beyond completed/failed/skipped).
 */
import type { AiSummaryRun } from "@/lib/api-client/aiThreads";

export function runLabel(run: AiSummaryRun): string {
  const scope = run.scope_code
    ? ` · ${run.scope_sec_type ?? "scope"} ${run.scope_code}`
    : " · market-wide";
  const goal = run.goal ? ` · ${run.goal.slice(0, 52)}${run.goal.length > 52 ? "…" : ""}` : "";
  const active = run.steps_total > run.steps_completed + run.steps_failed + run.steps_skipped;
  return `${run.run_date} ${run.first_at.slice(11, 16)}${scope}${goal}${active ? " · running" : ""}`;
}
