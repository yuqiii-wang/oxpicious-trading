/**
 * AnalysisItemsTree — the summary page's first left foldable, "Scopes":
 * the SUMMARY SUBJECTS section on top (the run's scope tags + their add
 * input — default-linked to the classification nav's multi-selected
 * items; see AnalysisSummaryPage's scope wiring), then the analysis
 * items as a nested checkbox list grouped by their top-nav category
 * (Commons / Derivatives / Composites / Signals).
 *
 * The panel folds HORIZONTALLY: collapsed it is a slim vertical strip on
 * the far left (icon + rotated title + selected/total count), and a
 * click expands it to the RIGHT; the expanded header's chevron folds it
 * back. On narrow screens (xs) there is no room to grow sideways, so the
 * strip becomes a full-width bar and the fold falls back to vertical.
 * The panel stretches to the summary card's height; its body scrolls.
 * Inside, every item starts CHECKED ("select all" default). Each category
 * row is a tri-state checkbox (checked = all items, indeterminate = some)
 * that also toggles the group open/closed via the chevron; the master
 * "All" row on top spans every category. Item keys are
 * `categorySlug/itemSlug` so the selection set is ready to feed the
 * summary generation when it lands. Every item row carries its own Ask AI
 * "?" (AnalysisItemAskButton) that opens the shared ask modal scoped to
 * that analysis.
 */
import { useState } from "react";
import {
  Autocomplete,
  createFilterOptions,
  Box,
  Chip,
  Checkbox,
  Collapse,
  IconButton,
  Paper,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import {
  CenterFocusStrong,
  ChevronLeft,
  ChevronRight,
  ExpandLess,
  ExpandMore,
} from "@mui/icons-material";
import AnalysisItemAskButton from "./AnalysisItemAskButton";
import {
  ALL_ANALYSIS_ITEM_KEYS,
  ANALYSIS_CATEGORIES,
  analysisItemKey,
  type AnalysisCategoryEntry,
} from "./constants";

/** Folded strip width vs expanded panel width (px, sm+). */
const STRIP_WIDTH = 46;
const PANEL_WIDTH = 300;

/** The stock tree carries thousands of items — the default match set
 *  stays bounded so the popper never renders it whole. */
const defaultOptionFilter = createFilterOptions<{
  code: string;
  name: string;
}>();
const SUBJECT_OPTION_CAP = 60;

/** One summary-subject tag — a code (or industry id) + its display name
 *  when the loaded trees carry one. */
export interface ScopeSubjectEntry {
  code: string;
  name?: string;
}

export interface AnalysisItemsTreeProps {
  /** Checked leaf keys (`categorySlug/itemSlug`); all-checked by default. */
  selectedKeys: Set<string>;
  onToggleItem: (key: string, checked: boolean) => void;
  onToggleCategory: (category: AnalysisCategoryEntry, checked: boolean) => void;
  onToggleAll: (checked: boolean) => void;
  /** The run's scope subjects — DEFAULT-LINKED to the classification
   *  nav's multi-selected items; the input bar below adds more (typed
   *  codes resolve their names from the trees when they carry them). */
  subjects: ScopeSubjectEntry[];
  onRemoveSubject: (code: string) => void;
  /** A committed add (the input bar's Enter/pick) — the page dedupes. */
  onAddSubject: (code: string) => void;
  /** The input bar's suggestion list — the loaded trees' items. */
  subjectSuggestions: Array<{ code: string; name: string }>;
  /** The subject kind label (Index / ETF / Stock / Industry) for the
   *  input hint; absent in the "all" sec_type (no subjects then). */
  subjectKind?: string;
}

/** The subjects section — the scope tags + their add input. Kept as a
 *  subcomponent so the input state resets with the section, not with the
 *  panel's fold. */
function SubjectsSection({
  subjects,
  onRemoveSubject,
  onAddSubject,
  subjectSuggestions,
  subjectKind,
}: {
  subjects: ScopeSubjectEntry[];
  onRemoveSubject: (code: string) => void;
  onAddSubject: (code: string) => void;
  subjectSuggestions: Array<{ code: string; name: string }>;
  subjectKind?: string;
}) {
  const [input, setInput] = useState("");
  const has = (code: string) =>
    subjects.some((s) => s.code.toUpperCase() === code.toUpperCase());
  const commitInput = (raw: string) => {
    const code = raw.trim();
    if (code === "" || has(code)) {
      setInput("");
      return;
    }
    setInput("");
    onAddSubject(code);
  };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 0.5,
        pb: 1,
        mb: 1,
        borderBottom: 1,
        borderColor: "divider",
      }}
    >
      <Typography variant="caption" color="text.secondary">
        Summary subjects{subjectKind ? ` · ${subjectKind}` : ""} — linked to
        the classification nav picks; empty = market-wide
      </Typography>
      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5, alignItems: "center" }}>
        {subjects.map((s) => (
          <Chip
            key={s.code}
            label={s.name ? `${s.code} ${s.name}` : s.code}
            size="small"
            color="primary"
            variant="outlined"
            onDelete={() => onRemoveSubject(s.code)}
          />
        ))}
        {subjects.length === 0 && (
          // The default placeholder tag — DISABLED (nothing to remove):
          // an empty pick IS the market-wide run, the same label the
          // backend's planning framing and prompt templates carry.
          <Tooltip title="Market-wide — no subject filter; the run covers the whole market">
            <Chip
              label="All Market"
              size="small"
              disabled
              variant="outlined"
              sx={{ fontStyle: "italic" }}
            />
          </Tooltip>
        )}
        <Autocomplete
          freeSolo
          size="small"
          disableClearable
          options={subjectSuggestions.filter((o) => !has(o.code))}
          getOptionLabel={(o) => (typeof o === "string" ? o : `${o.code} ${o.name}`)}
          filterOptions={(options, state) =>
            defaultOptionFilter(options, state).slice(0, SUBJECT_OPTION_CAP)
          }
          inputValue={input}
          onInputChange={(_, value) => setInput(value)}
          onChange={(_, value) => {
            const code =
              typeof value === "string" ? value.trim() : value?.code ?? "";
            if (code !== "") commitInput(code);
          }}
          sx={{ minWidth: 150, flexGrow: 1, maxWidth: 240 }}
          renderInput={(params) => (
            <TextField
              {...params}
              placeholder="Pick or type a code…"
              variant="standard"
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  commitInput(input);
                }
              }}
            />
          )}
        />
      </Box>
    </Box>
  );
}

export default function AnalysisItemsTree(props: AnalysisItemsTreeProps) {
  const {
    selectedKeys,
    onToggleItem,
    onToggleCategory,
    onToggleAll,
    subjects,
    onRemoveSubject,
    onAddSubject,
    subjectSuggestions,
    subjectKind,
  } = props;
  // The panel starts folded into a left strip — a click expands it
  // rightward; the expanded header's chevron folds it back.
  const [expanded, setExpanded] = useState(false);
  // All categories start expanded — nothing hides behind a second
  // collapsed level inside the open panel.
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const allCount = ALL_ANALYSIS_ITEM_KEYS.length;
  const allChecked = selectedKeys.size === allCount;
  const someChecked = selectedKeys.size > 0 && !allChecked;

  const toggleCollapsed = (slug: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) {
        next.delete(slug);
      } else {
        next.add(slug);
      }
      return next;
    });
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
            <CenterFocusStrong fontSize="small" color="primary" sx={{ mr: 0.5 }} />
            <Typography variant="subtitle2" sx={{ fontWeight: 600, flexGrow: 1 }}>
              Scopes
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {selectedKeys.size}/{allCount}
            </Typography>
            <IconButton
              size="small"
              aria-label="Collapse scopes"
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
            {/* Summary subjects — the scope tags + input bar (top of the
                panel: the scope pick governs the whole run, the item
                checkboxes below say which analyses it covers). */}
            <SubjectsSection
              subjects={subjects}
              onRemoveSubject={onRemoveSubject}
              onAddSubject={onAddSubject}
              subjectSuggestions={subjectSuggestions}
              subjectKind={subjectKind}
            />
            {/* Master row — spans every category. */}
            <Box sx={{ display: "flex", alignItems: "center" }}>
              <Checkbox
                size="small"
                checked={allChecked}
                indeterminate={someChecked}
                onChange={(e) => onToggleAll(e.target.checked)}
                inputProps={{ "aria-label": "Select all analyses" }}
                sx={{ py: 0.5 }}
              />
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                All
              </Typography>
              <Typography variant="caption" color="text.secondary" sx={{ ml: "auto" }}>
                {selectedKeys.size}/{allCount}
              </Typography>
            </Box>
            {ANALYSIS_CATEGORIES.map((category) => {
              const checkedCount = category.items.filter((item) =>
                selectedKeys.has(analysisItemKey(category, item)),
              ).length;
              const categoryAll = checkedCount === category.items.length;
              const isCollapsed = collapsed.has(category.slug);
              return (
                <Box key={category.slug}>
                  <Box sx={{ display: "flex", alignItems: "center" }}>
                    <Checkbox
                      size="small"
                      checked={categoryAll}
                      indeterminate={checkedCount > 0 && !categoryAll}
                      onChange={(e) => onToggleCategory(category, e.target.checked)}
                      inputProps={{ "aria-label": `Select all ${category.title} analyses` }}
                      sx={{ py: 0.5 }}
                    />
                    <Typography
                      variant="body2"
                      onClick={() => toggleCollapsed(category.slug)}
                      sx={{ fontWeight: 600, cursor: "pointer", userSelect: "none", flexGrow: 1 }}
                    >
                      {category.title}
                    </Typography>
                    <IconButton size="small" onClick={() => toggleCollapsed(category.slug)} sx={{ p: 0.25 }}>
                      {isCollapsed ? <ExpandMore fontSize="small" /> : <ExpandLess fontSize="small" />}
                    </IconButton>
                  </Box>
                  <Collapse in={!isCollapsed}>
                    <Box sx={{ pl: 3 }}>
                      {category.items.map((item) => {
                        const key = analysisItemKey(category, item);
                        return (
                          <Box key={key} sx={{ display: "flex", alignItems: "center" }}>
                            <Checkbox
                              size="small"
                              checked={selectedKeys.has(key)}
                              onChange={(e) => onToggleItem(key, e.target.checked)}
                              inputProps={{ "aria-label": item.title }}
                              sx={{ py: 0.25 }}
                            />
                            <Typography
                              variant="body2"
                              color="text.secondary"
                              noWrap
                              onClick={() => onToggleItem(key, !selectedKeys.has(key))}
                              sx={{ cursor: "pointer", userSelect: "none", flexGrow: 1 }}
                            >
                              {item.title}
                            </Typography>
                            <AnalysisItemAskButton category={category} item={item} />
                          </Box>
                        );
                      })}
                    </Box>
                  </Collapse>
                </Box>
              );
            })}
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
            <CenterFocusStrong fontSize="small" color="primary" />
            <Typography
              component="div"
              sx={{ writingMode: "vertical-rl", fontWeight: 600, fontSize: 13 }}
            >
              Scopes
            </Typography>
            <Typography
              component="div"
              variant="caption"
              color="text.secondary"
              sx={{ writingMode: "vertical-rl" }}
            >
              {selectedKeys.size}/{allCount}
            </Typography>
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
            <CenterFocusStrong fontSize="small" color="primary" sx={{ mr: 0.5 }} />
            <Typography variant="subtitle2" sx={{ fontWeight: 600, flexGrow: 1 }}>
              Scopes
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {selectedKeys.size}/{allCount}
            </Typography>
            <ExpandMore fontSize="small" color="action" />
          </Box>
        </>
      )}
    </Paper>
  );
}
