/** The clicked task's AGENT STEPS — the lazy-loading accordion under a
 *  task row, with the UI fork/spawn actions (2026-11-15). */
import { useCallback, useEffect, useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Button,
  Chip,
  CircularProgress,
  TextField,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import { Bolt, CallSplit, ExpandMore, PlayArrow } from "@mui/icons-material";
import {
  fetchNodeAgentSteps,
  fetchStateAgentSteps,
  runNodeFork,
  runNodeForkOnNode,
  runNodeSpawn,
  runNodeSpawnOnNode,
} from "@/lib/api-client/aiThreads";
import { AgentStepList } from "./stepRows";

/** The inline goal composer behind both node actions — a two-row
 *  textarea plus run/cancel; `kind` names the lane on the button. */
function NodeActionComposer({
  kind,
  placeholder,
  busy,
  onSubmit,
  onCancel,
}: {
  kind: "spawn" | "fork";
  placeholder: string;
  busy: boolean;
  onSubmit: (goal: string) => void;
  onCancel: () => void;
}) {
  const [goal, setGoal] = useState("");
  const valid = goal.trim().length > 0;
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 0.75,
        mt: 0.75,
        border: 1,
        borderColor: "divider",
        borderRadius: 1,
        p: 0.75,
      }}
    >
      <TextField
        size="small"
        multiline
        minRows={2}
        fullWidth
        autoFocus
        placeholder={placeholder}
        value={goal}
        onChange={(e) => setGoal(e.target.value)}
        sx={{
          "& .MuiInputBase-input": { fontSize: "0.75rem" },
        }}
      />
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.75 }}>
        <Button
          size="small"
          variant="contained"
          color={kind === "spawn" ? "secondary" : "primary"}
          disabled={!valid || busy}
          startIcon={
            kind === "spawn" ? (
              <PlayArrow sx={{ fontSize: 15 }} />
            ) : (
              <CallSplit sx={{ fontSize: 15 }} />
            )
          }
          onClick={() => valid && onSubmit(goal.trim())}
          sx={{ textTransform: "none", fontSize: "0.72rem" }}
        >
          {kind === "spawn" ? "spawn child node" : "fork from this step"}
        </Button>
        <Button
          size="small"
          variant="text"
          disabled={busy}
          onClick={onCancel}
          sx={{ textTransform: "none", fontSize: "0.72rem" }}
        >
          cancel
        </Button>
        {busy && <CircularProgress size={12} />}
      </Box>
    </Box>
  );
}

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
 *  predate the chain binding have no linkage and say so.
 *
 *  THE UI NODE ACTIONS (2026-11-15): a SPAWN bar sits above the first
 *  step (a fresh-context child node under this task's state row — the
 *  goal prompt fires the daemon's node action) and every inference row
 *  carries a FORK affordance (a continuation child from that step's
 *  recorded conversation). Both run as background daemon tasks and call
 *  `onActionDone` so the graph refreshes onto the new child node.
 *  A `nodeKey` mount (the state-less runner-lane seats — the output
 *  node) is read-only: there is no state row to parent a child from. */
export function TaskAgentSteps({
  threadId,
  stateId,
  nodeKey,
  hasContext,
  defaultOpen = false,
  onActionDone,
}: {
  threadId: string;
  /** The task's state row (the spawn/fork child's parent). Absent on a
   *  `nodeKey` mount — the state-less seats ride the daemon's
   *  node-keyed routes (plan-row parent). */
  stateId?: number;
  /** The runner-lane node name (the node-keyed fetch for the seats
   *  with no state row). */
  nodeKey?: string;
  hasContext: boolean;
  /** Open (and fetch) on mount — the clicked spawn/fork node's context
   *  panel mounts this as THE node's expansion, so its agent steps show
   *  without a second click (2026-11-13). */
  defaultOpen?: boolean;
  /** Fired after a successful spawn/fork — the graph's refresh onto
   *  the new child node. */
  onActionDone?: () => void;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [data, setData] = useState<Awaited<
    ReturnType<typeof fetchStateAgentSteps>
  > | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The endpoint's 404 (unknown task under the requested thread) comes
  // back as null — held as its OWN flag: storing it in `data` (null)
  // would leave data===null && error===null, the loading condition,
  // FOREVER — the endless "loading agent steps…" spinner.
  const [notFound, setNotFound] = useState(false);
  // The UI node actions' state: the open composer (null = closed, "top"
  // = the spawn bar, a request id = that inference row's fork composer),
  // the in-flight flag and the last action's error/result line.
  const [composer, setComposer] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [actionMsg, setActionMsg] = useState<
    { ok: boolean; text: string } | null
  >(null);
  // The step list renders the LATEST rows only until expanded — a
  // busy task can hold 30+ recorded steps, and mounting every row's
  // markdown at once froze the panel. (The truncation toggle + the
  // step→row mapping live in the shared AgentStepList, stepRows.tsx —
  // an expanded spawn step reuses the same body for its child node.)
  useEffect(() => {
    if (!open || data !== null || error !== null || notFound) return;
    let live = true;
    const fetch = nodeKey !== undefined
      ? fetchNodeAgentSteps(threadId, nodeKey)
      : fetchStateAgentSteps(threadId, stateId ?? -1);
    fetch
      .then((d) => {
        if (!live) return;
        if (d === null) setNotFound(true);
        else setData(d as Awaited<ReturnType<typeof fetchStateAgentSteps>>);
      })
      .catch((e: unknown) => {
        if (live) {
          setError(e instanceof Error ? e.message : String(e));
        }
      });
    return () => {
      live = false;
    };
  }, [open, data, error, notFound, threadId, stateId, nodeKey]);

  const handleSpawn = useCallback(
    async (goal: string) => {
      setActionBusy(true);
      setActionMsg(null);
      try {
        const result = stateId !== undefined
          ? await runNodeSpawn(threadId, stateId, goal)
          : await runNodeSpawnOnNode(threadId, nodeKey ?? "", goal);
        setActionMsg({
          ok: true,
          text: result.state_id !== null
            ? stateId !== undefined
              ? `child node #${result.state_id} spawned — running on the daemon; the canvas shows it under this node`
              : `child node #${result.state_id} spawned — a run-level delegation; the canvas shows it as a spawn spur under the plan node`
            : "spawn registered",
        });
        setComposer(null);
        onActionDone?.();
      } catch (e) {
        setActionMsg({
          ok: false,
          text: e instanceof Error ? e.message : String(e),
        });
      } finally {
        setActionBusy(false);
      }
    },
    [threadId, stateId, nodeKey, onActionDone],
  );

  const handleFork = useCallback(
    async (goal: string) => {
      if (composer === null || composer === "top") {
        return;
      }
      setActionBusy(true);
      setActionMsg(null);
      try {
        const result = stateId !== undefined
          ? await runNodeFork(threadId, stateId, goal, composer)
          : await runNodeForkOnNode(threadId, nodeKey ?? "", goal, composer);
        setActionMsg({
          ok: true,
          text: result.state_id !== null
            ? stateId !== undefined
              ? `fork child node #${result.state_id} created — it continues this conversation from step #${composer}`
              : `fork child node #${result.state_id} created — it continues the seat's conversation from step #${composer}; the canvas shows it as a fork spur under the plan node`
            : "fork registered",
        });
        setComposer(null);
        onActionDone?.();
      } catch (e) {
        setActionMsg({
          ok: false,
          text: e instanceof Error ? e.message : String(e),
        });
      } finally {
        setActionBusy(false);
      }
    },
    [threadId, stateId, nodeKey, composer, onActionDone],
  );

  // The per-row fork affordance — stable so the memoized request rows
  // keep their identity across renders.
  // THE ACTIONS' GATE: state-backed tasks carry spawn (under this
  // task's state row) + per-step fork (continuing the recorded
  // conversation); a nodeKey mount (the state-less runner-lane seats —
  // the output node) carries BOTH through the daemon's node-keyed
  // routes: the spawn parents under the chain root's plan row, the fork
  // rides the seat's OWN chain (the fork point request is validated on
  // it) with the same plan-row parent.
  const spawnEnabled = stateId !== undefined || nodeKey !== undefined;
  const forkEnabled = stateId !== undefined || nodeKey !== undefined;

  const handleForkRequest = useCallback((requestId: string) => {
    setComposer(requestId);
  }, []);

  const forkAffordance = forkEnabled
    ? {
        forkRequest: composer !== null && composer !== "top" ? composer : null,
        forkBusy: actionBusy,
        onForkRequest: handleForkRequest,
        onForkSubmit: handleFork,
        onForkCancel: () => setComposer(null),
      }
    : undefined;

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
        {open && (
          <>
            {spawnEnabled && (
              // THE SPAWN BAR — above the first step: one fresh-context
              // child. State-backed tasks spawn under this task's state
              // row; a nodeKey mount (the state-less runner-lane seats)
              // spawns at the run level — the daemon parents the child
              // under the chain root's plan row.
              <Box sx={{ mb: 0.75 }}>
                {composer === "top" ? (
                  <NodeActionComposer
                    kind="spawn"
                    placeholder={
                      stateId !== undefined
                        ? "the child's self-contained goal (fresh context — it sees the run's goal, scopes and prompts, not this conversation)"
                        : "the child's self-contained goal — a run-level delegation (fresh context; the canvas shows it as a spawn spur under the plan node)"
                    }
                    busy={actionBusy}
                    onSubmit={handleSpawn}
                    onCancel={() => setComposer(null)}
                  />
                ) : (
                  <Button
                    size="small"
                    variant="outlined"
                    color="secondary"
                    startIcon={<PlayArrow sx={{ fontSize: 15 }} />}
                    onClick={() => setComposer("top")}
                    sx={{ textTransform: "none", fontSize: "0.7rem" }}
                  >
                    {stateId !== undefined
                      ? "spawn child node — a fresh-context delegation under this node"
                      : "spawn child node — a run-level fresh-context delegation"}
                  </Button>
                )}
              </Box>
            )}
            {error !== null ? (
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
              <AgentStepList
                steps={steps}
                threadId={threadId}
                fork={forkAffordance}
              />
            )}
            {actionMsg !== null && (
              <Typography
                variant="caption"
                color={actionMsg.ok ? "secondary.main" : "error"}
                sx={{ display: "block", pl: 0.5, mt: 0.5, whiteSpace: "pre-wrap" }}
              >
                {actionMsg.text}
              </Typography>
            )}
          </>
        )}
      </AccordionDetails>
    </Accordion>
  );
}
