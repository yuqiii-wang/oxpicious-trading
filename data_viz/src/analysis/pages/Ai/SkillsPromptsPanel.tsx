/**
 * SkillsPromptsPanel — the summary page's second left foldable,
 * "Prompts": the prompt registry picker (the selectable text.ai_prompts
 * rows via GET /api/ai/prompts, grouped into foldable per-type sections —
 * skills approach instructions, then the plan prompt; the picked
 * prompt_key values ride in the generation request; the deep agent
 * fetches their instruction texts itself; titles kept in several
 * versions (same title + type, one prompt_key per version) show
 * " · v{n}") and the System Prompts slot,
 * which stays EMPTY until prompt management lands.
 *
 * The checkbox ticks a prompt into the generation; clicking its row
 * (besides ticking) OPENS the prompt — the page's main panel swaps to the
 * prompt's content view (PromptContent) with an Edit/Save pair in place
 * of the Generate button; clicking the open row again closes it.
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
  CircularProgress,
  Collapse,
  IconButton,
  Paper,
  Typography,
} from "@mui/material";
import {
  ChevronLeft,
  ChevronRight,
  Description,
  ExpandLess,
  ExpandMore,
  Extension,
  TextSnippet,
} from "@mui/icons-material";
import type { AiPrompt } from "@/lib/api-client/aiSummary";

interface SkillsPromptsPanelProps {
  prompts: AiPrompt[];
  loading: boolean;
  selectedPrompts: Set<string>;
  onTogglePrompt: (key: string, checked: boolean) => void;
  /** The opened prompt (the main panel shows its content), null = none. */
  openPromptKey: string | null;
  onOpenPrompt: (key: string | null) => void;
}

/** Folded strip width vs expanded panel width (px, sm+). */
const STRIP_WIDTH = 46;
const PANEL_WIDTH = 300;

/** Group display order — skills first, then plan; unknown types trail. */
const TYPE_ORDER = ["skills", "plan"];

/** Title-case a prompt_type_label for the group header. */
function typeTitle(label: string): string {
  return label.charAt(0).toUpperCase() + label.slice(1);
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
  loading,
  selectedPrompts,
  onTogglePrompt,
  openPromptKey,
  onOpenPrompt,
}: SkillsPromptsPanelProps) {
  // The panel starts folded into a left strip — a click expands it
  // rightward; the expanded header's chevron folds it back.
  const [expanded, setExpanded] = useState(false);
  // Per-type groups all start expanded — nothing hides behind a second
  // collapsed fold inside the open panel.
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const totalChecked = prompts.filter((p) => selectedPrompts.has(p.prompt_key)).length;

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

  const toggleCollapsed = (label: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(label)) {
        next.delete(label);
      } else {
        next.add(label);
      }
      return next;
    });
  };

  const toggleGroup = (rows: AiPrompt[], checked: boolean) => {
    for (const row of rows) {
      if (selectedPrompts.has(row.prompt_key) !== checked) {
        onTogglePrompt(row.prompt_key, checked);
      }
    }
  };

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
                  Loading skills…
                </Typography>
              </Box>
            ) : prompts.length === 0 ? (
              <EmptySlot
                icon={<Extension fontSize="small" />}
                title="Skills"
                note="No active skills in text.ai_prompts."
              />
            ) : (
              groups.map(({ label, rows }) => {
                const checkedCount = rows.filter((row) =>
                  selectedPrompts.has(row.prompt_key),
                ).length;
                const allChecked = checkedCount === rows.length;
                const isCollapsed = collapsed.has(label);
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
                return (
                  <Box key={label}>
                    <Box sx={{ display: "flex", alignItems: "center" }}>
                      <Checkbox
                        size="small"
                        checked={allChecked}
                        indeterminate={checkedCount > 0 && !allChecked}
                        onChange={(e) => toggleGroup(rows, e.target.checked)}
                        inputProps={{ "aria-label": `Select all ${label} prompts` }}
                        sx={{ py: 0.5 }}
                      />
                      <Typography
                        variant="body2"
                        onClick={() => toggleCollapsed(label)}
                        sx={{ fontWeight: 600, cursor: "pointer", userSelect: "none", flexGrow: 1 }}
                      >
                        {typeTitle(label)}
                      </Typography>
                      <Typography variant="caption" color="text.secondary" sx={{ mr: 0.5 }}>
                        {checkedCount}/{rows.length}
                      </Typography>
                      <IconButton size="small" onClick={() => toggleCollapsed(label)} sx={{ p: 0.25 }}>
                        {isCollapsed ? <ExpandMore fontSize="small" /> : <ExpandLess fontSize="small" />}
                      </IconButton>
                    </Box>
                    <Collapse in={!isCollapsed}>
                      <Box sx={{ pl: 2, display: "flex", flexDirection: "column", gap: 0.5 }}>
                        {rows.map((prompt) => {
                          const isOpen = openPromptKey === prompt.prompt_key;
                          return (
                            <Box
                              key={prompt.prompt_key}
                              sx={{
                                display: "flex",
                                alignItems: "flex-start",
                                gap: 0.5,
                                borderRadius: 1,
                                px: 0.5,
                                bgcolor: isOpen ? "action.selected" : "transparent",
                                "&:hover": { bgcolor: isOpen ? "action.selected" : "action.hover" },
                              }}
                            >
                              <Checkbox
                                size="small"
                                sx={{ p: 0.5, mt: 0.5 }}
                                checked={selectedPrompts.has(prompt.prompt_key)}
                                onChange={(e) => onTogglePrompt(prompt.prompt_key, e.target.checked)}
                              />
                              {/* The row's click target — opens the prompt's
                                  content in the main panel (again = closes). */}
                              <Box
                                onClick={() => onOpenPrompt(isOpen ? null : prompt.prompt_key)}
                                sx={{ minWidth: 0, py: 0.5, cursor: "pointer", flexGrow: 1 }}
                              >
                                <Typography variant="body2" sx={{ fontWeight: 600 }}>
                                  {prompt.title}
                                  {multiVersionTitles.has(prompt.title) && (
                                    <Box component="span" sx={{ fontWeight: 400, color: "text.secondary" }}>
                                      {" "}· v{prompt.version}
                                    </Box>
                                  )}
                                </Typography>
                                <Typography variant="caption" color="text.secondary">
                                  {prompt.note ?? prompt.instruction}
                                </Typography>
                              </Box>
                            </Box>
                          );
                        })}
                      </Box>
                    </Collapse>
                  </Box>
                );
              })
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
              <Extension fontSize="inherit" /> Skills are approach instructions
              the deep agent resolves from the text.ai_prompts registry; the
              plan row is laid out and followed before writing.
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
