/**
 * AnalysisItemsTree — the summary page's first left foldable, "Scopes":
 * the SUMMARY SUBJECTS section on top (the run's scope tags + their add
 * input — default-linked to the classification nav's multi-selected
 * items; see AnalysisSummaryPage's scope wiring), then — REPLACING the
 * former analysis-item checkbox tree (2026-11-11) — the SELECTED
 * TABLES as a nested tick tree over the KG's table corpus: one
 * tri-state row per SCHEMA (the group) with its tables under it, the
 * same nested tick hierarchy the former All/Commons/… tree ran. The
 * ticked tables ARE the run's scope: they ride the request as
 * analysis_items (one section per table) and as forced_tables (the KG
 * surface — kg_search / kg_expand / the data hints — serves only
 * them), both structurally filtered by the deep-agent serving layer.
 * The underlying list is the SHARED KG table corpus (useKgScopeTables
 * — one session fetch, the same data the Knowledge Graph config page's
 * dropdown renders). The selection starts ALL-TICKED: the page seeds
 * every corpus table once the fetch lands (a user tick beating the
 * seed stays), so the out-of-box run sections the whole corpus.
 *
 * The panel folds HORIZONTALLY: collapsed it is a slim vertical strip on
 * the far left (icon + rotated title + selected/total count), and a
 * click expands it to the RIGHT; the expanded header's chevron folds it
 * back. The panel starts EXPANDED (the fold state persists in
 * localStorage — usePanelFold). On narrow screens (xs) there is no room
 * to grow sideways, so the strip becomes a full-width bar and the fold
 * falls back to vertical.
 * The panel stretches to the summary card's height; its body scrolls.
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
import {
  kgNameSchema,
  useKgScopeTables,
} from "@/shared/components/kg-table-scope";
import { usePanelFold } from "./usePanelFold";

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
  /** The run's SELECTED TABLES (2026-11-11) — the nested tick tree's
   *  state: they ride the request as analysis_items AND as
   *  forced_tables (the KG surface serves only them). */
  forcedTables: string[];
  onChangeForcedTables: (next: string[]) => void;
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
    subjects,
    onRemoveSubject,
    onAddSubject,
    subjectSuggestions,
    subjectKind,
    forcedTables,
    onChangeForcedTables,
  } = props;
  // Starts expanded (first visit) — the header's chevron folds it into
  // a left strip, a strip click expands it rightward; the state persists.
  const [expanded, toggleExpanded] = usePanelFold("scopes", true);
  // All schema groups start expanded — nothing hides behind a second
  // collapsed level inside the open panel.
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  // The nested tick tree's universe: the SHARED KG table corpus
  // (schema groups; one session fetch, fail-soft to []).
  const { groups, ready } = useKgScopeTables();
  const allTables = groups.flatMap((g) => g.tables);
  const allCount = allTables.length;
  const selected = new Set(forcedTables);
  const allChecked = allCount > 0 && forcedTables.length === allCount;
  const someChecked = forcedTables.length > 0 && !allChecked;

  const toggleTable = (name: string, checked: boolean) => {
    const next = new Set(selected);
    if (checked) {
      next.add(name);
    } else {
      next.delete(name);
    }
    onChangeForcedTables([...next]);
  };
  const toggleSchema = (tables: string[], checked: boolean) => {
    const next = new Set(selected);
    for (const name of tables) {
      if (checked) {
        next.add(name);
      } else {
        next.delete(name);
      }
    }
    onChangeForcedTables([...next]);
  };
  const toggleAllTables = (checked: boolean) => {
    onChangeForcedTables(checked ? allTables : []);
  };

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
            <CenterFocusStrong fontSize="small" color="primary" sx={{ mr: 0.5 }} />
            <Typography variant="subtitle2" sx={{ fontWeight: 600, flexGrow: 1 }}>
              Scopes
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {forcedTables.length}/{allCount}
            </Typography>
            <IconButton
              size="small"
              aria-label="Collapse scopes"
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
            {/* Summary subjects — the scope tags + input bar (top of the
                panel: the scope pick governs the whole run, the table
                ticks below say which analyses it covers). */}
            <SubjectsSection
              subjects={subjects}
              onRemoveSubject={onRemoveSubject}
              onAddSubject={onAddSubject}
              subjectSuggestions={subjectSuggestions}
              subjectKind={subjectKind}
            />
            {/* The SELECTED TABLES tree — one tri-state row per schema
                group, its tables under it; the ticks ride the request as
                the run's analysis_items AND forced_tables. */}
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 0.5 }}>
              Ticked tables = the run's KG scope — kg_search / kg_expand /
              the data hints serve only them; each tick is also a summary
              section.
            </Typography>
            <Box sx={{ display: "flex", alignItems: "center" }}>
              <Checkbox
                size="small"
                checked={allChecked}
                indeterminate={someChecked}
                onChange={(e) => toggleAllTables(e.target.checked)}
                inputProps={{ "aria-label": "Select all tables" }}
                sx={{ py: 0.5 }}
              />
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                All tables
              </Typography>
              <Typography variant="caption" color="text.secondary" sx={{ ml: "auto" }}>
                {forcedTables.length}/{allCount}
              </Typography>
            </Box>
            {!ready && (
              <Typography variant="caption" color="text.secondary" sx={{ px: 4, py: 1 }}>
                loading the KG tables…
              </Typography>
            )}
            {ready && groups.length === 0 && (
              <Typography variant="caption" color="text.secondary" sx={{ px: 4, py: 1 }}>
                The KG table corpus is unavailable — check the Knowledge
                Graph tab.
              </Typography>
            )}
            {groups.map((group) => {
              const groupChecked =
                group.tables.length > 0 &&
                group.tables.every((t) => selected.has(t));
              const groupSome = group.tables.some((t) => selected.has(t));
              const isCollapsed = collapsed.has(group.schema);
              return (
                <Box key={group.schema}>
                  <Box sx={{ display: "flex", alignItems: "center" }}>
                    <Checkbox
                      size="small"
                      checked={groupChecked}
                      indeterminate={groupSome && !groupChecked}
                      onChange={(e) => toggleSchema(group.tables, e.target.checked)}
                      inputProps={{ "aria-label": `Select all ${group.schema} tables` }}
                      sx={{ py: 0.5 }}
                    />
                    <Typography
                      variant="body2"
                      onClick={() => toggleCollapsed(group.schema)}
                      sx={{ fontWeight: 600, cursor: "pointer", userSelect: "none", flexGrow: 1 }}
                    >
                      {group.schema}
                    </Typography>
                    <IconButton size="small" onClick={() => toggleCollapsed(group.schema)} sx={{ p: 0.25 }}>
                      {isCollapsed ? <ExpandMore fontSize="small" /> : <ExpandLess fontSize="small" />}
                    </IconButton>
                  </Box>
                  <Collapse in={!isCollapsed}>
                    <Box sx={{ pl: 3 }}>
                      {group.tables.map((name) => {
                        const bare = kgNameSchema(name)[1];
                        return (
                          <Box key={name} sx={{ display: "flex", alignItems: "center" }}>
                            <Checkbox
                              size="small"
                              checked={selected.has(name)}
                              onChange={(e) => toggleTable(name, e.target.checked)}
                              inputProps={{ "aria-label": name }}
                              sx={{ py: 0.25 }}
                            />
                            <Tooltip title={name}>
                              <Typography
                                variant="body2"
                                color="text.secondary"
                                noWrap
                                onClick={() => toggleTable(name, !selected.has(name))}
                                sx={{ cursor: "pointer", userSelect: "none", flexGrow: 1 }}
                              >
                                {bare}
                              </Typography>
                            </Tooltip>
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
              {forcedTables.length}/{allCount}
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
              {forcedTables.length}/{allCount}
            </Typography>
            <ExpandMore fontSize="small" color="action" />
          </Box>
        </>
      )}
    </Paper>
  );
}
