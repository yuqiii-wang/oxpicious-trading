/** The clicked task's AGENT STEPS — the lazy-loading accordion under a
 *  task row. */
import { useEffect, useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Chip,
  CircularProgress,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import { Bolt, ExpandMore } from "@mui/icons-material";
import { fetchStateAgentSteps } from "@/lib/api-client/aiThreads";
import { AgentStepList } from "./stepRows";

/** The clicked task's AGENT STEPS — HIDDEN by default; clicking the
 *  section loads them once (spinner on the toggle line, uncached: a
 *  live run records new ones) and renders them under the task's
 *  input/output contexts. Four step types: inference = one LLM request
 *  (the former requests list — a task's rows are its llm_context_id
 *  chain, a top-level step narrowed to its tick window, a subagent
 *  child its whole chain); spawn = ONE spawned sub node collapsed
 *  (prompt in, final output out — CLICK the row's "sub node #N agent
 *  steps" affordance to load the child node's own steps inline);
 *  decision = one System One (jev) read and
 *  tokenize = one StructBERT segmentation op (llm.deep_agent_aux_steps
 *  — recorded off-context, displayed here only). Tasks from runs that
 *  predate the chain binding have no linkage and say so. */
export function TaskAgentSteps({
  threadId,
  stateId,
  hasContext,
}: {
  threadId: string;
  stateId: number;
  hasContext: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<Awaited<
    ReturnType<typeof fetchStateAgentSteps>
  > | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The endpoint's 404 (unknown task under the requested thread) comes
  // back as null — held as its OWN flag: storing it in `data` (null)
  // would leave data===null && error===null, the loading condition,
  // FOREVER — the endless "loading agent steps…" spinner.
  const [notFound, setNotFound] = useState(false);
  // The step list renders the LATEST rows only until expanded — a
  // busy task can hold 30+ recorded steps, and mounting every row's
  // markdown at once froze the panel. (The truncation toggle + the
  // step→row mapping live in the shared AgentStepList, stepRows.tsx —
  // an expanded spawn step reuses the same body for its child node.)
  useEffect(() => {
    if (!open || data !== null || error !== null || notFound) return;
    let live = true;
    fetchStateAgentSteps(threadId, stateId)
      .then((d) => {
        if (!live) return;
        if (d === null) setNotFound(true);
        else setData(d);
      })
      .catch((e: unknown) => {
        if (live) {
          setError(e instanceof Error ? e.message : String(e));
        }
      });
    return () => {
      live = false;
    };
  }, [open, data, error, notFound, threadId, stateId]);
  const loading = open && data === null && error === null && !notFound;
  const steps = data?.steps ?? [];
  const nInference = steps.filter((st) => st.step_type === 0).length;
  const nSpawn = steps.filter((st) => st.step_type === 1).length;
  const nFork = steps.filter((st) => st.step_type === 4).length;
  const nDecision = steps.filter((st) => st.step_type === 2).length;
  const nToken = steps.filter((st) => st.step_type === 3).length;

  const hasSteps = steps.length > 0;
  return (
    <Accordion
      expanded={open}
      onChange={(_, expanded) => setOpen(expanded)}
      sx={(theme) => ({
        mt: 0.75,
        bgcolor: "background.paper",
        border: 1,
        borderColor: hasSteps ? "primary.main" : "divider",
        borderRadius: "1.5px !important",
        // A task with recorded steps glows faintly in its family color —
        // the affordance that this bar opens onto recorded work.
        boxShadow: hasSteps
          ? `0 0 6px ${alpha(theme.palette.primary.main, theme.palette.mode === "dark" ? 0.35 : 0.18)}`
          : "none",
        "&:before": { display: "none" },
        // A task's context panel is dense caption rows — keep the
        // accordion flush instead of MUI's expanded 16px-margin jump.
        "&.Mui-expanded": { margin: 0 },
      })}
    >
      <AccordionSummary
        expandIcon={<ExpandMore sx={{ fontSize: 16 }} />}
        sx={(theme) => ({
          minHeight: 32,
          px: 1,
          bgcolor: hasSteps
            ? alpha(theme.palette.primary.main, theme.palette.mode === "dark" ? 0.1 : 0.04)
            : "transparent",
          "& .MuiAccordionSummary-content": { my: 0.25, alignItems: "center", gap: 1 },
        })}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.75, flexWrap: "wrap" }}>
          <Bolt
            sx={{ fontSize: 16, color: hasSteps ? "primary.main" : "text.disabled" }}
          />
          <Typography
            variant="caption"
            sx={{
              fontWeight: 700,
              letterSpacing: "0.02em",
              color: hasSteps ? "primary.main" : "text.secondary",
            }}
          >
            Agent steps
          </Typography>
          {loading && <CircularProgress size={12} />}
          {data !== null && hasSteps && (
            <>
              <Chip
                size="small"
                variant="outlined"
                color="primary"
                label={`${nInference} inference`}
                sx={{
                  height: 16,
                  fontSize: "0.65rem",
                  fontWeight: 700,
                  "& .MuiChip-label": { px: 0.75 },
                }}
              />
              <Chip
                size="small"
                variant="outlined"
                color="secondary"
                label={`${nSpawn} spawn`}
                sx={{
                  height: 16,
                  fontSize: "0.65rem",
                  fontWeight: 700,
                  "& .MuiChip-label": { px: 0.75 },
                }}
              />
              {nFork > 0 && (
                <Chip
                  size="small"
                  variant="outlined"
                  color="secondary"
                  label={`${nFork} fork`}
                  sx={{
                    height: 16,
                    fontSize: "0.65rem",
                    fontWeight: 700,
                    "& .MuiChip-label": { px: 0.75 },
                  }}
                />
              )}
              {nDecision > 0 && (
                <Chip
                  size="small"
                  variant="outlined"
                  color="warning"
                  label={`${nDecision} decision`}
                  sx={{
                    height: 16,
                    fontSize: "0.65rem",
                    fontWeight: 700,
                    "& .MuiChip-label": { px: 0.75 },
                  }}
                />
              )}
              {nToken > 0 && (
                <Chip
                  size="small"
                  variant="outlined"
                  color="success"
                  label={`${nToken} tokenize`}
                  sx={{
                    height: 16,
                    fontSize: "0.65rem",
                    fontWeight: 700,
                    "& .MuiChip-label": { px: 0.75 },
                  }}
                />
              )}
            </>
          )}
        </Box>
      </AccordionSummary>
      <AccordionDetails sx={{ px: 1, py: 0.5 }}>
        {/* MUI keeps collapsed AccordionDetails children MOUNTED (the
            former Collapse had unmountOnExit) — gate on `open` to keep
            the fetch-lazy mount: no premature empty-state render, and
            the first expand still triggers the one fetch. */}
        {open && (error !== null ? (
          <Typography variant="caption" color="error" sx={{ display: "block", pl: 0.5 }}>
            agent steps: {error}
          </Typography>
        ) : notFound ? (
          <Typography variant="caption" color="text.disabled" sx={{ display: "block", pl: 0.5 }}>
            no agent steps under this task — the state row was not found
            under the opened thread id
          </Typography>
        ) : loading ? (
          <Typography variant="caption" color="text.disabled" sx={{ display: "block", pl: 0.5 }}>
            loading agent steps…
          </Typography>
        ) : steps.length === 0 ? (
          <Typography variant="caption" color="text.disabled" sx={{ display: "block", pl: 0.5 }}>
            {hasContext
              ? "no agent steps recorded for this task"
              : "no step linkage — this run predates the request-chain binding"}
          </Typography>
        ) : (
          <AgentStepList steps={steps} threadId={threadId} />
        ))}
      </AccordionDetails>
    </Accordion>
  );
}
