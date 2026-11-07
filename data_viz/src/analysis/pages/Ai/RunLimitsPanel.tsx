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
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Collapse,
  IconButton,
  Paper,
  TextField,
  Typography,
} from "@mui/material";
import {
  ChevronLeft,
  ChevronRight,
  ExpandMore,
  InfoOutlined,
  Tune,
} from "@mui/icons-material";
import {
  fetchDeepAgentLimits,
  saveDeepAgentLimits,
  type DeepAgentLimits,
} from "@/lib/api-client/config";

/** Folded strip width vs expanded panel width (px, sm+). */
const STRIP_WIDTH = 46;
const PANEL_WIDTH = 316;

/** One editable limit: the payload key, its bound and its one-line
 *  help (the label's tooltip). Ranges mirror the route's validation
 *  table (data_viz/api/routes/config/deep-agent.ts) and limits.py::from_doc. */
interface LimitFieldDef {
  key: NumericLimitKey;
  label: string;
  min: number;
  max: number | null;
  /** null = unbounded (empty input); only subagentTimeoutSec today. */
  nullable?: boolean;
  help: string;
}

type NumericLimitKey = {
  [K in keyof DeepAgentLimits]: DeepAgentLimits[K] extends number | null
    ? K
    : never;
}[keyof DeepAgentLimits];

/** Display order — grouped the way a run spends: the conversation
 *  budget, the plan tree that multiplies it, the subagent fan-out,
 *  the per-request size caps, the prompt/payload budgets. */
const LIMIT_GROUPS: Array<{ title: string; fields: LimitFieldDef[] }> = [
  {
    title: "Conversation steps",
    fields: [
      {
        key: "maxConversationSteps",
        label: "Max steps / conversation",
        min: 1,
        max: 1000,
        help: "The per-conversation hop budget (one hop = one LLM turn that may batch tool calls). The default the bridge and server send for a run.",
      },
      {
        key: "minConversationSteps",
        label: "Clamp floor",
        min: 1,
        max: 64,
        help: "Whatever a run asks for, one conversation keeps at least this many hops.",
      },
      {
        key: "maxStepsCap",
        label: "Clamp ceiling",
        min: 1,
        max: 1000,
        help: "Keeps one conversation from eating the whole run. A delegated fork gets +8 headroom under this same ceiling.",
      },
      {
        key: "recursionLimit",
        label: "Recursion limit",
        min: 25,
        max: 100000,
        help: "The LangGraph superstep bound for one graph invocation: plan + step hops + replans + finalize + result.",
      },
      {
        key: "runTimeoutSec",
        label: "Run timeout (s)",
        min: 60,
        max: null,
        help: "The run's TOTAL wall-clock budget (default 5h = 18000s): the step lane checks it every hop and routes to finalize at the budget — a clean settle instead of grinding forever. The bridge's and the browser's deadlines sit at the same value.",
      },
      {
        key: "llmRequestTimeoutSec",
        label: "LLM request timeout (s)",
        min: 5,
        max: null,
        help: "The per-LLM-request wall-clock budget (default 300s): the provider's retry ladder terminates at the budget. An overrun means the input is too heavy for one call — the harness terminates it and refactors the input into smaller tasks (the planner's slimmed init, the fork pre-planner), never a same-payload retry. This bounds ONE individual LLM request, not a node: a step node runs many requests across its tool turns and may take far longer than this value — nodes are bounded by the conversation step budget and the run by run timeout instead.",
      },
    ],
  },
  {
    title: "Plan tree",
    fields: [
      {
        key: "maxPlanNodes",
        label: "Max planned steps",
        min: 1,
        max: 200,
        help: "How many steps the planner's plan may hold (extra steps are truncated). Quoted in the planning prompt.",
      },
      {
        key: "maxDelegatedSteps",
        label: "Max delegated steps",
        min: 0,
        max: 200,
        help: "How many planned steps may carry a fork marker (each fork is a fresh subagent conversation).",
      },
      {
        key: "planRepairRounds",
        label: "Plan repair rounds",
        min: 0,
        max: 10,
        help: "The planning session's propose → correct round-trips per batch before the run degrades to the no-plan execute lane.",
      },
      {
        key: "planProposalBatches",
        label: "Plan proposal batches",
        min: 1,
        max: 24,
        help: "The batched proposal protocol's round cap: the first call proposes the first few steps, later rounds propose the next batch over the nodes so far — each a small LLM call that persists immediately, so planning never hangs on one giant reply.",
      },
      {
        key: "maxTotalPlanSteps",
        label: "Max total plan steps",
        min: 1,
        max: 1000,
        help: "The replan guard: update_plan / external adds may not grow the tree past this.",
      },
      {
        key: "maxForkChildren",
        label: "Max fork children",
        min: 0,
        max: 200,
        help: "How many sub-nodes one fork may add under itself.",
      },
    ],
  },
  {
    title: "Subagents",
    fields: [
      {
        key: "maxParallelDelegatedSteps",
        label: "Fork wave width",
        min: 1,
        max: 64,
        help: "How many delegated steps of one hop run concurrently (the rest queue behind the slots).",
      },
      {
        key: "maxConcurrentSubagents",
        label: "Concurrent subagents / agent",
        min: 1,
        max: 64,
        help: "The per-agent spawn semaphore — the parent's own fan-out cap (propagates to every child).",
      },
      {
        key: "maxSpawnDepth",
        label: "Spawn depth",
        min: 0,
        max: 8,
        help: "The spawn-chain depth cap: an agent AT the depth carries no spawn tool (0 disables delegation).",
      },
      {
        key: "subagentTimeoutSec",
        label: "Subagent timeout (s)",
        min: 1,
        max: null,
        nullable: true,
        help: "Wall clock per spawned child; empty = unbounded (the historical behavior).",
      },
    ],
  },
  {
    title: "LLM request budget",
    fields: [
      {
        key: "maxOutputTokens",
        label: "Max output tokens / request",
        min: 256,
        max: null,
        help: "The per-request output ceiling — THE runaway guard. Loose by design: a finding is ~2-4k tokens.",
      },
      {
        key: "maxOutputRepairs",
        label: "Output repair rounds",
        min: 0,
        max: 5,
        help: "Bounded correction turns when a conversation's final answer fails structural validation.",
      },
    ],
  },
  {
    title: "Findings bridge",
    fields: [
      {
        key: "findingMaxChars",
        label: "Per-finding chars",
        min: 50,
        max: null,
        help: "One step's finding is capped per line before it joins the bridge.",
      },
      {
        key: "findingsBlockMaxChars",
        label: "Findings block chars",
        min: 100,
        max: null,
        help: "The whole block the next step sees; older findings age to title lines past it.",
      },
    ],
  },
  {
    title: "Payload guard",
    fields: [
      {
        key: "payloadEntryMaxChars",
        label: "Entry guard chars",
        min: 200,
        max: null,
        help: "A structured-context subtree whose serialized size passes this ships as metadata + preview instead of full content (the _guarded stub names the shape and previews a taste of it).",
      },
      {
        key: "payloadGuardChars",
        label: "Whole-block gate chars",
        min: 0,
        max: null,
        help: "After per-entry guarding, the whole data block is trimmed (largest entries first) down to this gate — a budget against many medium entries.",
      },
      {
        key: "payloadPreviewChars",
        label: "Preview chars / value",
        min: 100,
        max: null,
        help: "How much of one elided value's content the preview keeps.",
      },
      {
        key: "payloadPreviewItems",
        label: "Preview items / container",
        min: 1,
        max: 50,
        help: "How many elements/entries an elided array or object previews.",
      },
      {
        key: "toolResultMaxChars",
        label: "Tool result chars",
        min: 500,
        max: null,
        help: "One tool result's text, head+tail capped before it enters the transcript — a wide db_run_query dump (500 rows is a row cap, not a byte cap) would otherwise ride every later turn of the hop.",
      },
    ],
  },
  {
    title: "Prompt loading",
    fields: [
      {
        key: "promptEagerRows",
        label: "Eager rows",
        min: 0,
        max: 100,
        help: "A skills selection at or under this many rows loads fully without the planner's verdict.",
      },
      {
        key: "promptEagerChars",
        label: "Eager chars",
        min: 0,
        max: null,
        help: "…or a matched set whose full texts fit this budget also loads eagerly.",
      },
    ],
  },
  {
    title: "Supervisor repair lane",
    fields: [
      {
        key: "supervisorPollIntervalSec",
        label: "Poll interval (s)",
        min: 2,
        max: 60,
        help: "How often the supervisor session (the backend poller beside the graph) re-reads the settled node states while a run executes.",
      },
      {
        key: "supervisorRepairGraceSec",
        label: "Repair window (s)",
        min: 0,
        max: 600,
        help: "On a failed step the lane pauses this long for the supervisor's verdict (retry / ignore). 0 disables repair — the lane never waits. Must cover the intervention call plus any retry throttle.",
      },
      {
        key: "supervisorMaxNodeRetries",
        label: "Retries / node",
        min: 0,
        max: 3,
        help: "How many times ONE failed step may be re-run on the supervisor's retry verdict.",
      },
      {
        key: "supervisorMaxRepairsPerRun",
        label: "Repairs / run",
        min: 0,
        max: 6,
        help: "The run-wide cap on re-opens — past it every further failure falls through to no-action.",
      },
      {
        key: "supervisorRetryThrottleSec",
        label: "Rate-limit cooldown (s)",
        min: 0,
        max: 300,
        help: "A retry on a rate-limit-class failure waits this long INSIDE the repair window before the hop re-runs — an immediate re-run would re-hit the hot account.",
      },
    ],
  },
];

const ALL_FIELDS = LIMIT_GROUPS.flatMap((g) => g.fields);

/** The numeric entry as input strings (the TextField draft). */
function draftOf(limits: DeepAgentLimits): Record<NumericLimitKey, string> {
  const draft = {} as Record<NumericLimitKey, string>;
  for (const f of ALL_FIELDS) {
    const value = limits[f.key];
    draft[f.key] = value === null ? "" : String(value);
  }
  return draft;
}

export default function RunLimitsPanel() {
  // Folded-strip pattern, same as the Prompts panel beside it.
  const [expanded, setExpanded] = useState(false);
  const [limits, setLimits] = useState<DeepAgentLimits | null>(null);
  const [draft, setDraft] = useState<Record<NumericLimitKey, string> | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The one field whose explanation is expanded (click the info mark
  // to toggle; one open at a time keeps the panel calm).
  const [openHelp, setOpenHelp] = useState<NumericLimitKey | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    fetchDeepAgentLimits()
      .then((res) => {
        if (!alive) return;
        setLimits(res.limits);
        setDraft(draftOf(res.limits));
      })
      .catch((err) => alive && setError(String(err)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  const dirty = useMemo(() => {
    if (!limits || !draft) return false;
    return ALL_FIELDS.some((f) => {
      const current = limits[f.key];
      const text = draft[f.key].trim();
      if (f.nullable) {
        return text === "" ? current !== null : Number(text) !== current;
      }
      return text === "" || Number(text) !== current;
    });
  }, [limits, draft]);

  const save = async () => {
    if (!limits || !draft) return;
    setSaving(true);
    setError(null);
    try {
      const payload = { ...limits };
      for (const f of ALL_FIELDS) {
        const text = draft[f.key].trim();
        (payload[f.key] as number | null) =
          text === "" && f.nullable ? null : Number(text);
      }
      const saved = await saveDeepAgentLimits(payload);
      setLimits(saved.limits);
      setDraft(draftOf(saved.limits));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
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
            <Tune fontSize="small" color="primary" sx={{ mr: 0.5 }} />
            <Typography variant="subtitle2" sx={{ fontWeight: 600, flexGrow: 1 }}>
              Run Limits
            </Typography>
            <IconButton
              size="small"
              aria-label="Collapse run limits"
              onClick={(e) => {
                e.stopPropagation();
                setExpanded(false);
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
                      <Box key={f.key} sx={{ py: 0.25 }}>
                        <Box
                          sx={{
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "space-between",
                            gap: 0.5,
                          }}
                        >
                          <Typography
                            variant="body2"
                            noWrap
                            sx={{ flexGrow: 1, minWidth: 0 }}
                          >
                            {f.label}
                          </Typography>
                          {/* The info mark — click toggles the field's
                              explanation inline (cheaper and more
                              deliberate than a hover tooltip). */}
                          <IconButton
                            size="small"
                            aria-label={`About ${f.label}`}
                            aria-expanded={openHelp === f.key}
                            onClick={(e) => {
                              e.stopPropagation();
                              setOpenHelp(
                                openHelp === f.key ? null : f.key,
                              );
                            }}
                            sx={{
                              p: 0.25,
                              flexShrink: 0,
                              color: openHelp === f.key
                                ? "primary.main"
                                : "action.active",
                            }}
                          >
                            <InfoOutlined sx={{ fontSize: 15 }} />
                          </IconButton>
                          <TextField
                            size="small"
                            type="number"
                            value={draft[f.key]}
                            onChange={(e) =>
                              setDraft({ ...draft, [f.key]: e.target.value })
                            }
                            error={
                              (() => {
                                const text = draft[f.key].trim();
                                if (text === "") return !f.nullable;
                                const n = Number(text);
                                return !Number.isFinite(n) || n < f.min ||
                                  (f.max !== null && n > f.max);
                              })()
                            }
                            inputProps={{
                              min: f.min,
                              max: f.max ?? undefined,
                              step: "any",
                              "aria-label": f.label,
                            }}
                            sx={{ width: 92, flexShrink: 0 }}
                          />
                        </Box>
                        <Collapse in={openHelp === f.key}>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            sx={{
                              display: "block",
                              px: 0.5,
                              pt: 0.25,
                              whiteSpace: "normal",
                            }}
                          >
                            {f.help}
                          </Typography>
                        </Collapse>
                      </Box>
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
                  Caps one run's LLM spend — steps, forks, request size.
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
