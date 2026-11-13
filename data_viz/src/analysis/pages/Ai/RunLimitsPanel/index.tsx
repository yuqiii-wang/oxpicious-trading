/**
 * RunLimitsPanel — the summary page's third left foldable, "Run
 * Limits": the deep agent's run-budget config (deep_agent_config.json
 * via GET/PUT /api/config/deep-agent) — how many and how large the LLM
 * requests of one run may be. Conversation step caps, the LangGraph
 * recursion bound, the plan-tree caps, the subagent fan-out, the
 * per-request output ceiling, the findings-bridge and eager-prompt
 * budgets, and the payload-guard caps (oversized data ships as
 * metadata + preview) — every value the harness used to hardcode
 * (llm_agents/deep_agents/limits.py now re-reads the file per run).
 *
 * A save takes effect on the NEXT run — no daemon restart; the
 * folded-panel pattern (strip ↔ expanded) and the body scroll mirror
 * the Prompts panel beside it. The panel is self-contained: it fetches
 * on mount and PUTs the whole entry on save (the route validates every
 * field's range and answers 400 with the offending field).
 *
 * Split into ./limitFields (the field definitions), ./useRunLimits
 * (the fetch/save state) and ./LimitFieldRow (one editable row).
 */
import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  IconButton,
  Paper,
  Typography,
} from "@mui/material";
import {
  ChevronLeft,
  ChevronRight,
  ExpandMore,
  Tune,
} from "@mui/icons-material";
import { usePanelFold } from "../usePanelFold";
import { LIMIT_GROUPS, draftOf, type NumericLimitKey } from "./limitFields";
import { useRunLimits } from "./useRunLimits";
import { LimitFieldRow } from "./LimitFieldRow";

/** Folded strip width vs expanded panel width (px, sm+). */
const STRIP_WIDTH = 46;
const PANEL_WIDTH = 316;

export default function RunLimitsPanel() {
  // Folded-strip pattern, same as the Prompts panel beside it — starts
  // folded (config, not the run workflow), the state persists.
  const [expanded, toggleExpanded] = usePanelFold("run-limits", false);
  const {
    limits,
    draft,
    setDraft,
    loading,
    saving,
    error,
    setError,
    dirty,
    save,
  } = useRunLimits();
  // The one field whose explanation is expanded (click the info mark
  // to toggle; one open at a time keeps the panel calm).
  const [openHelp, setOpenHelp] = useState<NumericLimitKey | null>(null);

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
            <Tune fontSize="small" color="primary" sx={{ mr: 0.5 }} />
            <Typography variant="subtitle2" sx={{ fontWeight: 600, flexGrow: 1 }}>
              Run Limits
            </Typography>
            <IconButton
              size="small"
              aria-label="Collapse run limits"
              onClick={(e) => {
                e.stopPropagation();
                toggleExpanded();
              }}
              sx={{ p: 0.25 }}
            >
              <ChevronLeft fontSize="small" />
            </IconButton>
          </Box>
          <Box sx={{ flex: 1, minHeight: 0, overflowY: "auto", p: 1.5 }}>
            {error !== null && (
              <Alert
                severity="error"
                onClose={() => setError(null)}
                sx={{ mb: 1.5 }}
              >
                {error}
              </Alert>
            )}
            {loading || !limits || !draft ? (
              <Box sx={{ display: "flex", alignItems: "center", gap: 1, p: 1 }}>
                <CircularProgress size={16} />{" "}
                <Typography variant="caption" color="text.secondary">
                  Loading limits…
                </Typography>
              </Box>
            ) : (
              LIMIT_GROUPS.map((group) => (
                <Box key={group.title} sx={{ mb: 1.5 }}>
                  <Typography
                    variant="caption"
                    sx={{
                      fontWeight: 600,
                      color: "text.secondary",
                      textTransform: "uppercase",
                      letterSpacing: 0.5,
                    }}
                  >
                    {group.title}
                  </Typography>
                  <Box sx={{ display: "flex", flexDirection: "column", mt: 0.5 }}>
                    {group.fields.map((f) => (
                      <LimitFieldRow
                        key={f.key}
                        field={f}
                        value={draft[f.key]}
                        onChange={(value) =>
                          setDraft({ ...draft, [f.key]: value })
                        }
                        helpOpen={openHelp === f.key}
                        onToggleHelp={() =>
                          setOpenHelp(openHelp === f.key ? null : f.key)
                        }
                      />
                    ))}
                  </Box>
                </Box>
              ))
            )}
            {!loading && limits && (
              <>
                <Box sx={{ display: "flex", gap: 1, mt: 0.5 }}>
                  <Button
                    size="small"
                    variant="contained"
                    disabled={!dirty || saving}
                    onClick={(e) => {
                      e.stopPropagation();
                      void save();
                    }}
                  >
                    {saving ? "Saving…" : "Save"}
                  </Button>
                  <Button
                    size="small"
                    disabled={!dirty || saving}
                    onClick={(e) => {
                      e.stopPropagation();
                      setDraft(draftOf(limits));
                    }}
                  >
                    Reset
                  </Button>
                </Box>
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ display: "block", mt: 1.5 }}
                >
                  Caps one run's LLM spend — steps, spawns, request size.
                  Saved to deep_agent_config.json (git-reviewed); the
                  daemon re-reads it per run, so a save applies to the
                  NEXT run without a restart.
                </Typography>
                {limits.updatedAt && (
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ display: "block", mt: 0.5 }}
                  >
                    Updated {limits.updatedAt}
                  </Typography>
                )}
              </>
            )}
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
            <Tune fontSize="small" color="primary" />
            <Typography
              component="div"
              sx={{ writingMode: "vertical-rl", fontWeight: 600, fontSize: 13 }}
            >
              Run Limits
            </Typography>
            {dirty && (
              <Typography
                component="div"
                variant="caption"
                color="warning.main"
                sx={{ writingMode: "vertical-rl" }}
              >
                unsaved
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
            <Tune fontSize="small" color="primary" sx={{ mr: 0.5 }} />
            <Typography variant="subtitle2" sx={{ fontWeight: 600, flexGrow: 1 }}>
              Run Limits
            </Typography>
            <ExpandMore fontSize="small" color="action" />
          </Box>
        </>
      )}
    </Paper>
  );
}
