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
 *
 * Split into ./panelConstants (order tables + labels), ./PromptRow,
 * ./GroupHeader and ./EmptySlot.
 */
import { useState } from "react";
import {
  Box,
  CircularProgress,
  Collapse,
  IconButton,
  Paper,
  Radio,
  Typography,
} from "@mui/material";
import {
  ChevronLeft,
  ChevronRight,
  Description,
  ExpandMore,
  Extension,
  TextSnippet,
} from "@mui/icons-material";
import { promptIdentity, type AiPrompt } from "@/lib/api-client/aiSummary";
import { goalAppliesToScope } from "../constants";
import { usePanelFold } from "../usePanelFold";
import {
  SUB_TYPE_ORDER,
  TOOL_SUB_TYPE_LABELS,
  TOOL_SUB_TYPE_ORDER,
  TYPE_ORDER,
  subTypeTitle,
  typeTitle,
} from "./panelConstants";
import { PromptRow } from "./PromptRow";
import { GroupHeader } from "./GroupHeader";
import { EmptySlot } from "./EmptySlot";

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
  // Starts expanded (first visit) — the header's chevron folds it into
  // a left strip, a strip click expands it rightward; the state persists.
  const [expanded, toggleExpanded] = usePanelFold("prompts", true);
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
      onClick={expanded ? undefined : toggleExpanded}
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
                toggleExpanded();
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
