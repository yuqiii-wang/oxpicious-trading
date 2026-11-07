/**
 * SkillsPromptsPanel — the summary page's second left foldable,
 * "Prompts": the prompt registry picker (the llm.ai_prompts rows via
 * GET /api/ai/prompts, nested into foldable sections — the per-type
 * groups (the Plan section with the larger type title, then the skills
 * approach instructions) and, inside a type, per-sub_type folds (a skill's
 * domain: DB query / online search / biz guides; the Plan section's
 * goal fold — the study-scope directives — is ONE mutually exclusive
 * radio choice topped by an Auto entry, none picked = the runner
 * defaults by the run's scope, whose radios enable/disable reactively
 * to the scope pick — the codes-family rows need named codes, the
 * market-family rows the empty pick = all codes → the whole market,
 * and a pick the scope rules out falls back to Auto — while its
 * orchestration fold — the
 * plan-shape rows — stays multi-pick checkboxes); the picked prompt_key
 * values ride in the generation request; the deep agent fetches their
 * instruction texts itself; titles kept in several versions (same
 * title + type, one prompt_key per version) show " · v{n}") plus the
 * Tools section —
 * the tool-type rows (the model-callable tools' schema descriptions):
 * NOT selectable (the runner resolves the active rows automatically at
 * every run start and overrides the tools' built-in descriptions with
 * them), but click-to-open view/edit in the main panel like any prompt,
 * nested by the tool kit/domain sub types (sandbox / delegation /
 * plan lanes / thread search / mcp). Row identity is the composite
 * prompt_key|prompt_sub_type (prompt_key alone is not unique —
 * update_plan has one row per lane). Each row shows its EXISTING labels
 * as tags (bound force-load keywords tinted, optional search vocabulary
 * plain, "+n" past four) with a "+" button that opens the prompt in the
 * main panel focused on its labels input — the chip editing itself
 * (add/remove, permanent vs run-only) lives in the open prompt view's
 * Labels section. Finally the System Prompts slot, which stays EMPTY
 * until prompt management lands.
 *
 * EVERY fold level is a structured click-to-expand: the folded panel
 * strip expands rightward, each type group's title/chevron toggles the
 * group, each sub_type header's title/chevron toggles the sub-group —
 * the checkboxes select, never fold. A row click (besides ticking)
 * OPENS the prompt — the page's main panel swaps to the prompt's
 * content view (PromptContent) with an Edit/Save pair in place of the
 * Generate button; clicking the open row again closes it.
 *
 * The panel folds HORIZONTALLY like the Scopes foldable beside it:
 * collapsed it is a slim vertical strip (icon + rotated title +
 * picked/total count) that expands to the RIGHT on a click; the expanded
 * header's chevron folds it back. On narrow screens (xs) the strip
 * becomes a full-width bar and the fold falls back to vertical. The
 * panel stretches to the summary card's height; its body scrolls.
 */
import { useState } from "react";
import {
  Box,
  Checkbox,
  Chip,
  CircularProgress,
  Collapse,
  IconButton,
  Paper,
  Radio,
  Typography,
} from "@mui/material";
import {
  Add,
  ChevronLeft,
  ChevronRight,
  Description,
  ExpandLess,
  ExpandMore,
  Extension,
  TextSnippet,
} from "@mui/icons-material";
import { promptIdentity, type AiPrompt } from "@/lib/api-client/aiSummary";
import { goalAppliesToScope } from "./constants";

interface SkillsPromptsPanelProps {
  /** The SELECTABLE rows (skills + plan) — checked keys ride the
   *  generation request; the deep agent fetches their texts itself. */
  prompts: AiPrompt[];
  /** The tool-type rows — view/edit only, never selectable (applied
   *  automatically by the runner each run). */
  toolPrompts: AiPrompt[];
  loading: boolean;
  selectedPrompts: Set<string>;
  onTogglePrompt: (key: string, checked: boolean) => void;
  /** The opened prompt's identity (promptIdentity — key|sub_type; the
   *  main panel shows its content), null = none. */
  openPromptKey: string | null;
  onOpenPrompt: (identity: string | null) => void;
  /** The row's "+" label button — opens the prompt in the main panel
   *  focused on its labels input. */
  onAddLabel: (identity: string) => void;
  /** Whether the run's scope pick NAMES codes (the scope tag bar is
   *  non-empty) — an empty pick reads all codes → the whole market.
   *  Drives the Plan-goal radios' reactive enable/disable: the
   *  codes-family goals need named codes, the market-family goals the
   *  empty pick. */
  scopeHasCodes: boolean;
}

/** Folded strip width vs expanded panel width (px, sm+). */
const STRIP_WIDTH = 46;
const PANEL_WIDTH = 300;

/** Group display order — plan first (the goal + orchestration picks
 *  drive the run's shape), then skills; unknown types trail. */
const TYPE_ORDER = ["plan", "skills"];

/** Sub-type display order inside a type group; '' (no split) trails.
 *  The last two are the PLAN-only sub types (goal before orchestration):
 *  goal = the study-scope directives (ONE radio choice — the runner
 *  defaults by scope when none is picked), orchestration = the plan-shape
 *  rows (multi-pick checkboxes). Skills carry neither. */
const SUB_TYPE_ORDER = [
  "db_query",
  "online_search",
  "biz_guide",
  "backend_tech",
  "text_corpus",
  "goal",
  "orchestration",
];

/** Friendly sub_type labels; unknowns title-case their raw value. */
const SUB_TYPE_LABELS: Record<string, string> = {
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
const TOOL_SUB_TYPE_ORDER = [
  "sandbox",
  "delegation",
  "plan_main",
  "plan_fork",
  "plan",
  "mcp",
];

/** Friendly tool sub_type labels; unknowns title-case their raw value. */
const TOOL_SUB_TYPE_LABELS: Record<string, string> = {
  sandbox: "Sandbox",
  delegation: "Delegation",
  plan_main: "Plan (main lane)",
  plan_fork: "Plan (fork)",
  plan: "Thread search",
  mcp: "MCP",
};

/** Title-case a prompt_type_label for the group header. */
function typeTitle(label: string): string {
  return label.charAt(0).toUpperCase() + label.slice(1);
}

/** Label a prompt_sub_type for the sub-group header ('' = General). */
function subTypeTitle(sub: string, labels: Record<string, string> = SUB_TYPE_LABELS): string {
  if (!sub) return "General";
  if (labels[sub]) return labels[sub];
  return sub
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

/** How many label tags a row shows before the "+n" overflow. */
const ROW_LABEL_TAGS = 4;

/** One prompt row — the checkbox (selectable rows only; tool rows have
 *  none) + the click target that opens the prompt in the main panel
 *  (again = closes). *exclusive* renders a RADIO instead of the checkbox
 *  (the plan-goal sub type: mutually exclusive — the panel clears the
 *  siblings on select, and the sub-group's Auto entry is the deselected
 *  state). *disabled* (goal rows out of scope: codes-family vs
 *  market-family against the current pick) grays the radio only — the
 *  row itself stays click-to-open for view/edit — and shows
 *  *disabledNote* under the caption. Beneath the caption the row shows
 *  its EXISTING labels as tags (bound = tinted, optional = plain; "+n"
 *  past four) with a "+" add button that opens the prompt focused on
 *  the labels input. */
function PromptRow({
  prompt,
  showVersionSuffix,
  selectable = true,
  exclusive = false,
  disabled = false,
  disabledNote,
  selected,
  opened,
  onToggle,
  onOpen,
  onAddLabel,
}: {
  prompt: AiPrompt;
  showVersionSuffix: boolean;
  selectable?: boolean;
  exclusive?: boolean;
  disabled?: boolean;
  disabledNote?: string;
  selected: boolean;
  opened: boolean;
  onToggle?: (checked: boolean) => void;
  onOpen: () => void;
  onAddLabel?: () => void;
}) {
  // Bound tags first (the force-load vocabulary), optional after; the
  // rest collapse into "+n".
  const tags = [
    ...prompt.bound_labels.map((label) => ({ label, bound: true })),
    ...prompt.optional_labels.map((label) => ({ label, bound: false })),
  ];
  const visibleTags = tags.slice(0, ROW_LABEL_TAGS);
  const overflow = tags.length - visibleTags.length;
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "flex-start",
        gap: 0.5,
        borderRadius: 1,
        px: 0.5,
        bgcolor: opened ? "action.selected" : "transparent",
        "&:hover": { bgcolor: opened ? "action.selected" : "action.hover" },
      }}
    >
      {selectable && (
        exclusive ? (
          <Radio
            size="small"
            name="plan-goal-scope"
            sx={{ p: 0.5, mt: 0.5 }}
            checked={selected}
            disabled={disabled}
            onChange={() => onToggle?.(true)}
            inputProps={{ "aria-label": `Select ${prompt.title} as the plan goal` }}
          />
        ) : (
          <Checkbox
            size="small"
            sx={{ p: 0.5, mt: 0.5 }}
            checked={selected}
            onChange={(e) => onToggle?.(e.target.checked)}
          />
        )
      )}
      <Box onClick={onOpen} sx={{ minWidth: 0, py: 0.5, cursor: "pointer", flexGrow: 1 }}>
        <Typography variant="body2" sx={{ fontWeight: 600 }}>
          {prompt.title}
          {showVersionSuffix && (
            <Box component="span" sx={{ fontWeight: 400, color: "text.secondary" }}>
              {" "}· v{prompt.version}
            </Box>
          )}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {prompt.preview || prompt.note || prompt.instruction}
        </Typography>
        {disabled && disabledNote !== undefined && (
          <Typography variant="caption" sx={{ display: "block", fontStyle: "italic" }}>
            {disabledNote}
          </Typography>
        )}
        {tags.length > 0 && (
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.25, alignItems: "center", mt: 0.25 }}>
            {visibleTags.map(({ label, bound }) => (
              <Chip
                key={label}
                label={label}
                size="small"
                variant="outlined"
                color={bound ? "primary" : "default"}
                sx={{
                  height: 17,
                  fontSize: 10,
                  "& .MuiChip-label": { px: 0.6 },
                }}
              />
            ))}
            {overflow > 0 && (
              <Typography variant="caption" color="text.secondary">
                +{overflow}
              </Typography>
            )}
          </Box>
        )}
      </Box>
      {onAddLabel && (
        <IconButton
          size="small"
          aria-label={`Add label to ${prompt.title}`}
          title="Add label"
          onClick={(e) => {
            // Not an open — just jump to the labels input.
            e.stopPropagation();
            onAddLabel();
          }}
          sx={{ p: 0.25, mt: 0.5 }}
        >
          <Add fontSize="small" />
        </IconButton>
      )}
    </Box>
  );
}

/** A group's (or sub-group's) header: the select-all checkbox
 *  (selectable groups only — the Tools group and the plan type group
 *  show a count instead: the Tools count is plain rows, the plan count
 *  is picked/total via *showSelectionCount*, since its goal sub type is
 *  a radio set a select-all cannot drive), the click-to-fold title —
 *  type-level titles larger (subtitle2) than the sub-group captions —
 *  the picked/total count and the chevron. */
function GroupHeader({
  label,
  rows,
  selectedKeys,
  collapsed,
  onToggleCollapsed,
  onToggleAll,
  subGroup,
  selectable = true,
  showSelectionCount = false,
}: {
  label: string;
  rows: AiPrompt[];
  selectedKeys?: Set<string>;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  onToggleAll?: (checked: boolean) => void;
  subGroup?: boolean;
  selectable?: boolean;
  showSelectionCount?: boolean;
}) {
  const checkedCount = rows.filter((row) => selectedKeys?.has(row.prompt_key) ?? false).length;
  const allChecked = selectable && checkedCount === rows.length;
  const countText = selectable || showSelectionCount
    ? `${checkedCount}/${rows.length}`
    : `${rows.length}`;
  return (
    <Box sx={{ display: "flex", alignItems: "center" }}>
      {selectable && (
        <Checkbox
          size="small"
          checked={allChecked}
          indeterminate={checkedCount > 0 && !allChecked}
          onChange={(e) => onToggleAll?.(e.target.checked)}
          inputProps={{ "aria-label": `Select all ${label} prompts` }}
          sx={{ py: 0.5 }}
        />
      )}
      <Typography
        variant={subGroup ? "caption" : "subtitle2"}
        onClick={onToggleCollapsed}
        sx={{ fontWeight: 600, cursor: "pointer", userSelect: "none", flexGrow: 1 }}
      >
        {label}
      </Typography>
      <Typography variant="caption" color="text.secondary" sx={{ mr: 0.5 }}>
        {countText}
      </Typography>
      <IconButton size="small" onClick={onToggleCollapsed} sx={{ p: 0.25 }}>
        {collapsed ? <ExpandMore fontSize="small" /> : <ExpandLess fontSize="small" />}
      </IconButton>
    </Box>
  );
}

function EmptySlot({ icon, title, note }: { icon: React.ReactNode; title: string; note: string }) {
  return (
    <Box
      sx={{
        p: 2,
        border: "1px dashed",
        borderColor: "divider",
        borderRadius: 1,
        color: "text.secondary",
        display: "flex",
        alignItems: "center",
        gap: 1,
      }}
    >
      {icon}
      <Box sx={{ minWidth: 0 }}>
        <Typography variant="body2" sx={{ fontWeight: 600 }}>
          {title}
        </Typography>
        <Typography variant="caption">{note}</Typography>
      </Box>
    </Box>
  );
}

export default function SkillsPromptsPanel({
  prompts,
  toolPrompts,
  loading,
  selectedPrompts,
  onTogglePrompt,
  openPromptKey,
  onOpenPrompt,
  onAddLabel,
  scopeHasCodes,
}: SkillsPromptsPanelProps) {
  // The panel starts folded into a left strip — a click expands it
  // rightward; the expanded header's chevron folds it back.
  const [expanded, setExpanded] = useState(false);
  // Folded type groups and sub_type sub-groups, both keyed sets —
  // type groups by their label, sub-groups by "<type>:<sub_type>"
  // (the Tools section's folds by "tool:<sub_type>"). Every level
  // starts expanded.
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [collapsedSub, setCollapsedSub] = useState<Set<string>>(new Set());

  const totalChecked = prompts.filter((p) => selectedPrompts.has(p.prompt_key)).length;

  const toggleCollapsed = (set: Set<string>, key: string, setter: (next: Set<string>) => void) => {
    const next = new Set(set);
    if (next.has(key)) {
      next.delete(key);
    } else {
      next.add(key);
    }
    setter(next);
  };

  // Foldable per-type groups, in TYPE_ORDER then first-row order.
  const groups = TYPE_ORDER
    .map((label) => ({
      label,
      rows: prompts.filter((p) => p.prompt_type_label === label),
    }))
    .filter((group) => group.rows.length > 0)
    .concat(
      prompts
        .filter((p) => !TYPE_ORDER.includes(p.prompt_type_label))
        .map((p) => ({ label: p.prompt_type_label, rows: [p] })),
    );

  /** The type group's sub_type sub-groups, in SUB_TYPE_ORDER then
   *  first-row order; a group whose rows carry no sub_types (plan)
   *  yields a single '' group — the caller renders it flat. */
  const subGroupsOf = (rows: AiPrompt[]) =>
    SUB_TYPE_ORDER
      .map((sub) => ({ sub, rows: rows.filter((p) => p.prompt_sub_type === sub) }))
      .filter((group) => group.rows.length > 0)
      .concat(
        rows
          .filter((p) => !SUB_TYPE_ORDER.includes(p.prompt_sub_type))
          .map((p) => ({ sub: p.prompt_sub_type, rows: [p] })),
      );

  const toggleGroup = (rows: AiPrompt[], checked: boolean) => {
    for (const row of rows) {
      if (selectedPrompts.has(row.prompt_key) !== checked) {
        onTogglePrompt(row.prompt_key, checked);
      }
    }
  };

  /** The plan-goal radio set: exactly ONE of *subRows*' keys selected
   *  (key "" = Auto — none picked, the runner defaults by the run's
   *  scope: codes named → study-codes, All Market → study-market-codes).
   *  Selecting a member clears the siblings in the same pass, so the
   *  set stays exclusive even if the request carried several. */
  const selectGoal = (subRows: AiPrompt[], key: string) => {
    for (const row of subRows) {
      const want = row.prompt_key === key;
      if (selectedPrompts.has(row.prompt_key) !== want) {
        onTogglePrompt(row.prompt_key, want);
      }
    }
  };

  /** The Plan-goal disabled radios' shared reason (every row the scope
   *  rules out shows the same line): codes-family rows need named
   *  codes, market-family rows the empty pick — which this UI reads as
   *  all codes → the whole market. */
  const goalDisabledNote = scopeHasCodes
    ? "needs an empty pick (all codes — whole market)"
    : "needs named codes — the pick is empty (all codes)";

  // The Tools section's sub_type folds, in TOOL_SUB_TYPE_ORDER then
  // first-row order (unknown kit/domains trail).
  const toolSubGroups = TOOL_SUB_TYPE_ORDER
    .map((sub) => ({ sub, rows: toolPrompts.filter((p) => p.prompt_sub_type === sub) }))
    .filter((group) => group.rows.length > 0)
    .concat(
      toolPrompts
        .filter((p) => !TOOL_SUB_TYPE_ORDER.includes(p.prompt_sub_type))
        .map((p) => ({ sub: p.prompt_sub_type, rows: [p] })),
    );

  // Titles kept in several versions show their revision no. — the same
  // rule as the selectable groups.
  const toolMultiVersionTitles = new Set(
    Object.entries(
      toolPrompts.reduce<Record<string, number>>((acc, row) => {
        acc[row.title] = (acc[row.title] ?? 0) + 1;
        return acc;
      }, {}),
    )
      .filter(([, n]) => n > 1)
      .map(([title]) => title),
  );

  /** A Tools row — click-to-open view/edit, no checkbox (the runner
   *  resolves the active tool rows itself each run; picking is moot). */
  const renderToolRow = (prompt: AiPrompt) => (
    <PromptRow
      key={promptIdentity(prompt)}
      prompt={prompt}
      showVersionSuffix={toolMultiVersionTitles.has(prompt.title)}
      selectable={false}
      selected={false}
      opened={openPromptKey === promptIdentity(prompt)}
      onOpen={() =>
        onOpenPrompt(
          openPromptKey === promptIdentity(prompt) ? null : promptIdentity(prompt),
        )
      }
      onAddLabel={() => onAddLabel(promptIdentity(prompt))}
    />
  );

  return (
    <Paper
      variant="outlined"
      onClick={expanded ? undefined : () => setExpanded(true)}
      sx={{
        width: { xs: "100%", sm: expanded ? PANEL_WIDTH : STRIP_WIDTH },
        flexShrink: 0,
        minWidth: 0,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        cursor: expanded ? "default" : "pointer",
        transition: (theme) =>
          theme.transitions.create("width", {
            duration: theme.transitions.duration.complex,
          }),
      }}
    >
      {expanded ? (
        <>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 0.5,
              px: 1.5,
              py: 0.75,
              borderBottom: 1,
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            <TextSnippet fontSize="small" color="primary" sx={{ mr: 0.5 }} />
            <Typography variant="subtitle2" sx={{ fontWeight: 600, flexGrow: 1 }}>
              Prompts
            </Typography>
            {prompts.length > 0 && (
              <Typography variant="caption" color="text.secondary">
                {totalChecked}/{prompts.length}
              </Typography>
            )}
            <IconButton
              size="small"
              aria-label="Collapse prompts"
              onClick={(e) => {
                e.stopPropagation();
                setExpanded(false);
              }}
              sx={{ p: 0.25 }}
            >
              <ChevronLeft fontSize="small" />
            </IconButton>
          </Box>
          <Box sx={{ flex: 1, minHeight: 0, overflowY: "auto", p: 1 }}>
            {loading ? (
              <Box sx={{ display: "flex", alignItems: "center", gap: 1, p: 1 }}>
                <CircularProgress size={16} />{" "}
                <Typography variant="caption" color="text.secondary">
                  Loading prompts…
                </Typography>
              </Box>
            ) : prompts.length === 0 ? (
              <EmptySlot
                icon={<Extension fontSize="small" />}
                title="Skills"
                note="No active skills in llm.ai_prompts."
              />
            ) : (
              groups.map(({ label, rows }) => {
                const isCollapsed = collapsed.has(label);
                const isPlan = label === "plan";
                // Titles kept in several versions show their revision no.
                // (" · v2") — a rework is a new prompt_key row with the same
                // title + type and version+1; single-version titles stay plain.
                const multiVersionTitles = new Set(
                  Object.entries(
                    rows.reduce<Record<string, number>>((acc, row) => {
                      acc[row.title] = (acc[row.title] ?? 0) + 1;
                      return acc;
                    }, {}),
                  )
                    .filter(([, n]) => n > 1)
                    .map(([title]) => title),
                );
                const renderRow = (prompt: AiPrompt, exclusive = false) => (
                  <PromptRow
                    key={promptIdentity(prompt)}
                    prompt={prompt}
                    showVersionSuffix={multiVersionTitles.has(prompt.title)}
                    exclusive={exclusive}
                    disabled={exclusive && !goalAppliesToScope(prompt, scopeHasCodes)}
                    disabledNote={goalDisabledNote}
                    selected={selectedPrompts.has(prompt.prompt_key)}
                    opened={openPromptKey === promptIdentity(prompt)}
                    onToggle={
                      exclusive
                        ? () => selectGoal(rows.filter((r) => r.prompt_sub_type === "goal"), prompt.prompt_key)
                        : (checked) => onTogglePrompt(prompt.prompt_key, checked)
                    }
                    onOpen={() =>
                      onOpenPrompt(
                        openPromptKey === promptIdentity(prompt)
                          ? null
                          : promptIdentity(prompt),
                      )
                    }
                    onAddLabel={() => onAddLabel(promptIdentity(prompt))}
                  />
                );
                const subGroups = subGroupsOf(rows);
                const nested = subGroups.length > 1 || subGroups[0]?.sub !== "";
                return (
                  <Box key={label}>
                    <GroupHeader
                      label={typeTitle(label)}
                      rows={rows}
                      selectedKeys={selectedPrompts}
                      collapsed={isCollapsed}
                      onToggleCollapsed={() =>
                        toggleCollapsed(collapsed, label, setCollapsed)
                      }
                      // The plan group's header shows a picked/total count
                      // instead of a select-all: its goal sub type is a
                      // RADIO set (one of the four, or Auto) a select-all
                      // cannot drive.
                      selectable={!isPlan}
                      showSelectionCount={isPlan}
                      onToggleAll={isPlan ? undefined : (checked) => toggleGroup(rows, checked)}
                    />
                    <Collapse in={!isCollapsed}>
                      {nested ? (
                        // Sub_type folds inside the type group — one
                        // nest level per distinct sub_type, each its own
                        // structured click-to-expand.
                        <Box sx={{ pl: 1.5, display: "flex", flexDirection: "column" }}>
                          {subGroups.map(({ sub, rows: subRows }) => {
                            const subKey = `${label}:${sub}`;
                            const isSubCollapsed = collapsedSub.has(subKey);
                            // The plan-goal sub type is a mutually exclusive
                            // radio choice: no select-all, an Auto entry
                            // above the rows (the deselected state), each
                            // row a radio whose selection clears siblings.
                            const exclusive = isPlan && sub === "goal";
                            const autoSelected = exclusive
                              ? !subRows.some((p) => selectedPrompts.has(p.prompt_key))
                              : false;
                            return (
                              <Box key={subKey}>
                                <GroupHeader
                                  label={subTypeTitle(sub)}
                                  rows={subRows}
                                  selectedKeys={selectedPrompts}
                                  collapsed={isSubCollapsed}
                                  onToggleCollapsed={() =>
                                    toggleCollapsed(collapsedSub, subKey, setCollapsedSub)
                                  }
                                  selectable={!exclusive}
                                  showSelectionCount={exclusive}
                                  onToggleAll={exclusive ? undefined : (checked) => toggleGroup(subRows, checked)}
                                  subGroup
                                />
                                <Collapse in={!isSubCollapsed}>
                                  <Box
                                    sx={{
                                      pl: 1.5,
                                      display: "flex",
                                      flexDirection: "column",
                                      gap: 0.5,
                                    }}
                                  >
                                    {exclusive ? (
                                      <>
                                        <Box
                                          onClick={() => selectGoal(subRows, "")}
                                          sx={{
                                            display: "flex",
                                            alignItems: "flex-start",
                                            gap: 0.5,
                                            borderRadius: 1,
                                            px: 0.5,
                                            cursor: "pointer",
                                            "&:hover": { bgcolor: "action.hover" },
                                          }}
                                        >
                                          <Radio
                                            size="small"
                                            name="plan-goal-scope"
                                            sx={{ p: 0.5, mt: 0.5 }}
                                            checked={autoSelected}
                                            onChange={() => selectGoal(subRows, "")}
                                            inputProps={{ "aria-label": "Auto plan goal" }}
                                          />
                                          <Box sx={{ minWidth: 0, py: 0.5 }}>
                                            <Typography variant="body2" sx={{ fontWeight: 600 }}>
                                              Auto — by scope
                                            </Typography>
                                            <Typography variant="caption" color="text.secondary">
                                              {scopeHasCodes
                                                ? "none picked — codes are named, the runner defaults to the named codes"
                                                : "none picked — empty pick = all codes, the runner defaults to the whole-market sweep"}
                                            </Typography>
                                          </Box>
                                        </Box>
                                        {subRows.map((p) => renderRow(p, true))}
                                      </>
                                    ) : (
                                      subRows.map((p) => renderRow(p))
                                    )}
                                  </Box>
                                </Collapse>
                              </Box>
                            );
                          })}
                        </Box>
                      ) : (
                        <Box sx={{ pl: 2, display: "flex", flexDirection: "column", gap: 0.5 }}>
                          {subGroups[0].rows.map((p) => renderRow(p))}
                        </Box>
                      )}
                    </Collapse>
                  </Box>
                );
              })
            )}
            {toolSubGroups.length > 0 && (
              // The tool-type rows: view/edit only — no select-all (the
              // runner resolves the active rows automatically each run).
              <Box sx={{ mt: 1 }}>
                <GroupHeader
                  label="Tools"
                  rows={toolPrompts}
                  collapsed={collapsed.has("tool")}
                  selectable={false}
                  onToggleCollapsed={() => toggleCollapsed(collapsed, "tool", setCollapsed)}
                />
                <Collapse in={!collapsed.has("tool")}>
                  <Box sx={{ pl: 1.5, display: "flex", flexDirection: "column" }}>
                    {toolSubGroups.map(({ sub, rows: subRows }) => {
                      const subKey = `tool:${sub}`;
                      const isSubCollapsed = collapsedSub.has(subKey);
                      return (
                        <Box key={subKey}>
                          <GroupHeader
                            label={subTypeTitle(sub, TOOL_SUB_TYPE_LABELS)}
                            rows={subRows}
                            collapsed={isSubCollapsed}
                            selectable={false}
                            subGroup
                            onToggleCollapsed={() =>
                              toggleCollapsed(collapsedSub, subKey, setCollapsedSub)
                            }
                          />
                          <Collapse in={!isSubCollapsed}>
                            <Box
                              sx={{
                                pl: 1.5,
                                display: "flex",
                                flexDirection: "column",
                                gap: 0.5,
                              }}
                            >
                              {subRows.map(renderToolRow)}
                            </Box>
                          </Collapse>
                        </Box>
                      );
                    })}
                  </Box>
                </Collapse>
              </Box>
            )}
            <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5, mt: 1.5 }}>
              <EmptySlot
                icon={<Description fontSize="small" />}
                title="System Prompts"
                note="No system prompt configured yet."
              />
            </Box>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "flex", alignItems: "center", gap: 0.5, mt: 1 }}
            >
              <Extension fontSize="inherit" /> The Plan section splits
              into Plan goal — the study-scope directives, ONE radio
              choice per run (Auto = the runner defaults by scope) — and
              Plan orchestration, the plan-shape rows laid out and
              followed before writing. Skills are approach instructions
              the deep agent resolves from the llm.ai_prompts registry,
              grouped by sub type (DB query / online search / biz guides —
              one biz guide per analysis scope). Tools are the model-callable tools'
              schema descriptions — resolved automatically on every run, so
              they are here to view and edit, not to pick.
            </Typography>
          </Box>
        </>
      ) : (
        <>
          {/* Folded strip (sm+) — vertical title; the chevron points the
              way the panel expands (rightward). */}
          <Box
            sx={{
              display: { xs: "none", sm: "flex" },
              flexDirection: "column",
              alignItems: "center",
              gap: 1,
              py: 1.5,
              flex: 1,
            }}
          >
            <TextSnippet fontSize="small" color="primary" />
            <Typography
              component="div"
              sx={{ writingMode: "vertical-rl", fontWeight: 600, fontSize: 13 }}
            >
              Prompts
            </Typography>
            {prompts.length > 0 && (
              <Typography
                component="div"
                variant="caption"
                color="text.secondary"
                sx={{ writingMode: "vertical-rl" }}
              >
                {totalChecked}/{prompts.length}
              </Typography>
            )}
            <ChevronRight fontSize="small" color="action" sx={{ mt: "auto" }} />
          </Box>
          {/* Folded bar on xs — narrow screens fold vertically instead. */}
          <Box
            sx={{
              display: { xs: "flex", sm: "none" },
              alignItems: "center",
              gap: 1,
              px: 1.5,
              py: 1,
            }}
          >
            <TextSnippet fontSize="small" color="primary" sx={{ mr: 0.5 }} />
            <Typography variant="subtitle2" sx={{ fontWeight: 600, flexGrow: 1 }}>
              Prompts
            </Typography>
            {prompts.length > 0 && (
              <Typography variant="caption" color="text.secondary">
                {totalChecked}/{prompts.length}
              </Typography>
            )}
            <ExpandMore fontSize="small" color="action" />
          </Box>
        </>
      )}
    </Paper>
  );
}
