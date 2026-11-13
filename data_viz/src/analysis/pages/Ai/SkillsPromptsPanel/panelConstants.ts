/** The SkillsPromptsPanel's display-order tables, friendly labels and
 *  title helpers. */
/** Group display order — plan first (the goal + orchestration picks
 *  drive the run's shape), then skills; unknown types trail. */
export const TYPE_ORDER = ["plan", "skills"];

/** Sub-type display order inside a type group; '' (no split) trails.
 *  The last two are the PLAN-only sub types (goal before orchestration):
 *  goal = the study-scope directives (ONE radio choice — the runner
 *  defaults by scope when none is picked), orchestration = the plan-shape
 *  rows (multi-pick checkboxes). Skills carry neither. */
export const SUB_TYPE_ORDER = [
  "db_query",
  "online_search",
  "biz_guide",
  "backend_tech",
  "text_corpus",
  "goal",
  "orchestration",
];

/** Friendly sub_type labels; unknowns title-case their raw value. */
export const SUB_TYPE_LABELS: Record<string, string> = {
  db_query: "DB query",
  online_search: "Online search",
  biz_guide: "Biz guides",
  backend_tech: "Backend tech",
  text_corpus: "Text corpus",
  goal: "Plan goal",
  orchestration: "Plan orchestration",
};

/** Tool-section sub_type display order — the seed's kit/domain flow
 *  (sandbox tools, delegation, the plan lanes, the thread search, the
 *  MCP catalog rows); unknowns trail. */
export const TOOL_SUB_TYPE_ORDER = [
  "sandbox",
  "delegation",
  "plan_main",
  "plan_spawn",
  "plan",
  "mcp",
];

/** Friendly tool sub_type labels; unknowns title-case their raw value. */
export const TOOL_SUB_TYPE_LABELS: Record<string, string> = {
  sandbox: "Sandbox",
  delegation: "Delegation",
  plan_main: "Plan (main lane)",
  plan_spawn: "Plan (spawn)",
  plan: "Thread search",
  mcp: "MCP",
};

/** Title-case a prompt_type_label for the group header. */
export function typeTitle(label: string): string {
  return label.charAt(0).toUpperCase() + label.slice(1);
}

/** Label a prompt_sub_type for the sub-group header ('' = General). */
export function subTypeTitle(sub: string, labels: Record<string, string> = SUB_TYPE_LABELS): string {
  if (!sub) return "General";
  if (labels[sub]) return labels[sub];
  return sub
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

/** How many label tags a row shows before the "+n" overflow. */
export const ROW_LABEL_TAGS = 4;
