/**
 * ThreadGraphView — the deep-agent run observability view, merged INTO
 * the Analysis Summary page's result panel behind the LangGraph/Result
 * toggle (formerly a "Thread Graph" tab, then a card beneath the
 * Summary History strip — now a bare chart in the panel: the toggle
 * labels the view, so the card chrome is gone and the "LangGraph
 * thread" heading + the run's FULL thread id (monospace, with a copy
 * button) + status legend + live chip lead the section). The
 * runner (llm_agents/deep_agents/langgraph/runner.py) compiles its LangGraph
 * StateGraph with AsyncPostgresSaver, one run = one thread keyed by a
 * uuid-hex thread_id; the view maps that graph to a node canvas and
 * walks it live:
 *
 *   • watch the progress — the graph polls GET /api/ai/threads/:threadId
 *     every 3s while any state is pending/in_progress and settles when
 *     the run does; the canvas draws the graph's own TOPOLOGY ONLY
 *     (graphModel.ts — the LangGraph/LangSmith observability contract):
 *     the runner chain START → plan → execute → result →
 *     END, each node wearing its lifecycle status and its sublabel
 *     carrying the node's SHARED context id + call counts (one node one context:
 *     the node's whole execution — execute's entire agent loop across
 *     every step — runs as ONE conversation). The plan steps are TASKS
 *     the execute node walks, not topology: they never render as
 *     canvas circles;
 *   • the OPTIMISTIC planning window — a generation in flight whose
 *     thread detail hasn't landed yet (the daemon registers the thread
 *     only once POST /run reaches it; the planning rows persist a beat
 *     later) draws buildPendingThreadGraphModel's START → plan
 *     (in_progress "planning…") → result → END canvas at once, so the
 *     Generate click's feedback is a running planning node, never an
 *     empty placeholder;
 *   • click a node — ONLY that node's own view appears beneath the
 *     graph, never another node's: its CONTEXTS first (the runner-lane
 *     state line — status · model · call counts · elapsed wall time ·
 *     token consumption in k (total, in · out, from the context's
 *     llm_requests chain rollup); the node's own llm-context registry
 *     entries — "N llm requests under this one context", each wearing
 *     its token consumption; and for the plan node the persisted plan
 *     JSON, its artifact) and, for EXECUTE alone, its TASK list — the
 *     plan steps its one conversation walks, each row stamped with that
 *     context (StatusChip status, never canvas circles; a step row
 *     expands its spawned subagents). plan owns no tasks — its view
 *     stays its contexts only. DEFAULT VIEW: with NO node clicked, the
 *     SUPERVISOR's context shows (the SUPERVISOR WATCHDOG — an
 *     independent agent beside the graph, not a node of it: idle on
 *     healthy runs, ONE bounded intervention when a root-path node
 *     fails; the canvas draws it as ONE free-floating circle above the
 *     lane over result with NO edge to the graph): its lane state, its
 *     one context and the kind supervisor-review document when one
 *     exists (the failure diagnosis + plan/topology verdict). Clicking
 *     a task row toggles its
 *     persisted INPUT/OUTPUT payloads (the step-input / step-result /
 *     subagent-result contexts — what the task was given and what it
 *     recorded) AND its recorded LLM REQUESTS/RESPONSES — hidden by
 *     default behind an outlined "LLM requests" chip that loads them
 *     on first click (spinner while in flight; GET
 *     /threads/:id/states/:stateId/llm-requests — llm.llm_requests
 *     rows attributed by chain: the task's llm_context_id, a top-level
 *     step narrowed to its start/finish tick window; a subagent child
 *     owns its whole chain). Each request row defaults to the CONTENT
 *     view — the semantic data only (the final assistant message, the
 *     call's NEW request messages; chained requests store the shared
 *     conversation once) — with a content/raw toggle; raw shows the
 *     stored JSONB payloads as pretty JSON. Tasks without linkage say
 *     so. No side panel: the payloads render inline under the row.
 *
 * onSettled fires when the watched run transitions live → settled — the
 * parent refreshes its run history so the strip's dot counts jump.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Box,
  Chip,
  CircularProgress,
  Collapse,
  IconButton,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import { Bolt, Check, ContentCopy, ExpandLess, ExpandMore } from "@mui/icons-material";
import {
  BaseChart,
  useChartThemeMode,
} from "@/shared/charts/base-chart";
import {
  buildPendingThreadGraphModel,
  buildThreadGraphModel,
  isOutputNodeId,
  itemsUnderNodeId,
  segmentIndexOfNodeId,
} from "./graphModel";
import { buildThreadGraphOption } from "./threadGraphOption";
import StatusChip from "./StatusChip";
import RunHeartbeat from "./RunHeartbeat";
import SaveTemplateButton from "./SaveTemplateButton";
import { useThreadDetail } from "./useThreadDetail";
import {
  fetchStateLlmRequests,
  type AgentLlmUsage,
  type AgentStateLlmRequest,
} from "@/lib/api-client/aiThreads";
import MarkdownView from "@/shared/markdown/MarkdownView";

/** Seconds → compact elapsed text ("42.3s" / "1m 12s" / "1h 03m"). */
function formatElapsedS(s: number | null): string {
  if (s === null || !Number.isFinite(s)) return "—";
  if (s < 60) return `${s.toFixed(1)}s`;
  if (s < 3600) {
    return `${Math.floor(s / 60)}m ${String(Math.floor(s % 60)).padStart(2, "0")}s`;
  }
  return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
}

/** Token count → the k unit ("18.3k" — the Contexts panel's consumption
 *  scale; sub-k counts keep the one-decimal k form). */
function formatTokensK(n: number | null): string {
  return n === null ? "—" : `${(n / 1000).toFixed(1)}k`;
}

/** The node/context consumption tail — "12.4k tok (8.1k in · 4.3k out)"
 *  off the context's llm_usage rollup; empty when nothing recorded. */
function usageTail(usage: AgentLlmUsage | null): string {
  if (usage === null) return "";
  return ` · ${formatTokensK(usage.total_tokens)} tok`
    + ` (${formatTokensK(usage.input_tokens)} in`
    + ` · ${formatTokensK(usage.output_tokens)} out)`;
}

interface ThreadGraphViewProps {
  /** The thread to draw (null = nothing picked — placeholder only). */
  threadId: string | null;
  /** Keep polling while the thread's rows haven't persisted yet — a
   *  generation in flight registers its thread id before its plan
   *  phase lands any rows (404 = not yet, not gone). */
  expectLive?: boolean;
  /** Fired when the watched run transitions live → settled. */
  onSettled?: () => void;
  /** Bumped by the AI event channel on every daemon state event for
   *  this thread — each bump refetches the detail immediately instead
   *  of waiting out the 3s poll (the poll stays the fallback). */
  refreshSignal?: number;
  /** Fires when the run's plan was saved as a template (the Save plan
   *  button) — the parent refreshes its template picker. */
  onPlanTemplateSaved?: () => void;
}

/** One input/output block of an expanded task row: the context
 *  payload's text — a single {"note"|"answer"|"error": string} payload
 *  renders as the bare string (the tick notes / subagent results),
 *  anything else as pretty JSON. `scroll` caps the height (the plan
 *  JSON can run long) instead of stretching the panel. */
function ContextBlock({
  label,
  payload,
  scroll = false,
}: {
  label: string;
  payload: unknown;
  scroll?: boolean;
}) {
  let text: string;
  if (payload !== null && typeof payload === "object" && !Array.isArray(payload)) {
    const entries = Object.entries(payload as Record<string, unknown>).filter(
      ([, v]) => v !== null && v !== undefined,
    );
    text =
      entries.length === 1 && typeof entries[0][1] === "string"
        ? entries[0][1]
        : JSON.stringify(payload, null, 2);
  } else {
    text = JSON.stringify(payload, null, 2);
  }
  return (
    <Box sx={{ borderLeft: 2, borderColor: "divider", pl: 1.25, py: 0.25 }}>
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
      <Typography
        variant="body2"
        sx={{
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          ...(scroll ? { maxHeight: 280, overflowY: "auto" } : {}),
        }}
      >
        {text}
      </Typography>
    </Box>
  );
}

/** Monospace pre-render shared by the tool-argument bodies (pretty JSON
 *  fallback and the extracted dominant field alike). */
const argsPreSx = {
  margin: 0,
  fontFamily: "monospace",
  fontSize: "0.75rem",
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
} as const;

/** The content-view rendering of one tool call's arguments. The
 *  DOMINANT string field is the call's semantic payload — the written
 *  file's content (sandbox_write_file), the SQL (db_run_query), the
 *  delegated goal (spawn_subagent), the tick note (update_plan_step) —
 *  so it becomes the body (real newlines, scroll-capped), and the
 *  remaining short fields compress to a `k=v` meta line. Only when no
 *  string field dominates (small flat args) does the whole pretty JSON
 *  show. */
function ToolCallArgs({ argsText }: { argsText: string }) {
  const body = (() => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(argsText);
    } catch {
      return null;
    }
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      return null;
    }
    const entries = Object.entries(parsed as Record<string, unknown>);
    let longest: [string, string] | null = null;
    for (const [key, value] of entries) {
      if (typeof value === "string" && (longest === null || value.length > longest[1].length)) {
        longest = [key, value];
      }
    }
    if (longest === null || longest[1].length < 80) return null;
    const restLine = entries
      .filter(([key]) => key !== longest![0])
      .map(([key, value]) => {
        const s = typeof value === "string" ? value : JSON.stringify(value) ?? "";
        return `${key}=${s.length > 60 ? `${s.slice(0, 57)}…` : s}`;
      })
      .join(" · ");
    return { restLine, text: longest[1] };
  })();
  if (body === null) {
    return (
      <Typography variant="body2" component="pre" sx={argsPreSx}>
        {argsText}
      </Typography>
    );
  }
  return (
    <>
      {body.restLine !== "" && (
        <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
          {body.restLine}
        </Typography>
      )}
      <Typography
        variant="body2"
        component="pre"
        sx={{ ...argsPreSx, maxHeight: 280, overflowY: "auto", mt: body.restLine ? 0.25 : 0 }}
      >
        {body.text}
      </Typography>
    </>
  );
}

/** One chat message of a request/response payload, laid out for
 *  reading instead of a raw JSON dump: a role line (plus the tool
 *  identity on tool results) over the content — string content renders
 *  as MARKDOWN (the shared MarkdownView at dense scale, matching how
 *  the Result view renders the model's documents); assistant tool_calls
 *  via :class:`ToolCallArgs` — the semantic argument field as the body.
 *  Anything that is not a role-shaped object falls back to pretty
 *  JSON. */
function ChatMessage({ message }: { message: unknown }) {
  if (message === null || typeof message !== "object") {
    return <ContextBlock label="message" payload={message} />;
  }
  const msg = message as Record<string, unknown>;
  const role = typeof msg.role === "string" ? msg.role : "message";
  const toolCallId =
    typeof msg.tool_call_id === "string" ? msg.tool_call_id : null;
  const contentIsText = typeof msg.content === "string";
  const content =
    typeof msg.content === "string"
      ? msg.content
      : msg.content === null || msg.content === undefined
        ? null
        : JSON.stringify(msg.content, null, 2);
  const toolCalls = Array.isArray(msg.tool_calls) ? msg.tool_calls : [];
  return (
    <Box sx={{ borderLeft: 2, borderColor: "divider", pl: 1.25, py: 0.25 }}>
      <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
        {role}
        {toolCallId ? ` · ${toolCallId}` : ""}
        {typeof msg.name === "string" ? ` · ${msg.name}` : ""}
      </Typography>
      {content !== null && content.trim() !== "" && (
        contentIsText ? (
          <MarkdownView source={content} dense />
        ) : (
          <Typography
            variant="body2"
            sx={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}
          >
            {content}
          </Typography>
        )
      )}
      {toolCalls.map((call, i) => {
        const fn = ((call as Record<string, unknown>)?.function ??
          {}) as Record<string, unknown>;
        const argsText = (() => {
          const raw = String(fn.arguments ?? "");
          try {
            return JSON.stringify(JSON.parse(raw), null, 2);
          } catch {
            return raw;
          }
        })();
        return (
          <Box key={i} sx={{ mt: 0.5 }}>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block" }}
            >
              tool call · {String(fn.name ?? "?")}
            </Typography>
            <ToolCallArgs argsText={argsText} />
          </Box>
        );
      })}
      {content === null && toolCalls.length === 0 && (
        <Typography
          variant="body2"
          component="pre"
          sx={{
            margin: 0,
            fontFamily: "monospace",
            fontSize: "0.75rem",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {JSON.stringify(msg, null, 2)}
        </Typography>
      )}
    </Box>
  );
}

/** One recorded request/response pair of an expanded task row: a meta
 *  line (id · time · model · duration · outcome · tokens) with a
 *  content/raw toggle, then the pair in the picked view — content
 *  (default) lays out the semantic data only (the final assistant
 *  message; the call's NEW request messages, chained rows storing the
 *  shared conversation once), raw shows the stored JSONB payloads as
 *  pretty JSON. */
function RequestRow({ req }: { req: AgentStateLlmRequest }) {
  const [view, setView] = useState<"content" | "raw">("content");
  // Token counts: the request-log columns first, the response payload's
  // usage block as the fallback for rows recorded before the columns.
  const usage = (req.response_payload as
    | { usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number } }
    | null)?.usage;
  const inTok = req.input_tokens ?? usage?.prompt_tokens ?? null;
  const outTok = req.output_tokens ?? usage?.completion_tokens ?? null;
  const totTok = req.total_tokens ?? usage?.total_tokens ?? null;
  const tokenSummary = [
    inTok !== null ? `${inTok} in` : null,
    outTok !== null ? `${outTok} out` : null,
    totTok !== null ? `${totTok} tok` : null,
  ]
    .filter((part) => part !== null)
    .join(" / ");
  const responseBody = req.response_payload as
    | { choices?: { message?: unknown }[] }
    | null;
  const responseMessage = responseBody?.choices?.[0]?.message ?? null;
  const payload = req.request_payload as Record<string, unknown> | null;
  const messages =
    payload !== null &&
    typeof payload === "object" &&
    Array.isArray(payload.messages)
      ? payload.messages
      : null;
  const incremental = payload?._chain_incremental === true;
  const shared =
    typeof payload?._chain_shared_messages === "number"
      ? payload._chain_shared_messages
      : 0;
  return (
    <Box sx={{ borderLeft: 2, borderColor: "divider", pl: 1.25, py: 0.25 }}>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          flexWrap: "wrap",
        }}
      >
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ minWidth: 0, flex: "1 1 auto" }}
        >
          #{req.request_id} · {req.created_at.slice(11, 19)} ·{" "}
          {req.model ?? "—"} ·{" "}
          {req.duration_ms !== null ? `${(req.duration_ms / 1000).toFixed(1)}s` : "—"} ·{" "}
          {req.is_failed
            ? `failed: ${req.failure_reason ?? "unknown"}`
            : `ok${req.status_code !== null ? ` (${req.status_code})` : ""}`}
          {tokenSummary !== "" ? ` · ${tokenSummary}` : ""}
        </Typography>
        <ToggleButtonGroup
          size="small"
          exclusive
          value={view}
          onChange={(_, v: "content" | "raw" | null) => {
            if (v !== null) setView(v);
          }}
        >
          <ToggleButton
            value="content"
            sx={{ px: 0.75, py: 0, fontSize: "0.65rem", textTransform: "none" }}
          >
            content
          </ToggleButton>
          <ToggleButton
            value="raw"
            sx={{ px: 0.75, py: 0, fontSize: "0.65rem", textTransform: "none" }}
          >
            raw
          </ToggleButton>
        </ToggleButtonGroup>
      </Box>
      {view === "raw" ? (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5, mt: 0.5 }}>
          <ContextBlock
            label="response — raw payload"
            payload={req.response_payload ?? req.failure_reason ?? "(no response body recorded)"}
            scroll
          />
          {req.request_payload !== null && req.request_payload !== undefined && (
            <ContextBlock
              label="request — raw payload"
              payload={req.request_payload}
              scroll
            />
          )}
        </Box>
      ) : (
        <>
          {responseMessage !== null && typeof responseMessage === "object" ? (
            <Box sx={{ mt: 0.5 }}>
              <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                response
              </Typography>
              <ChatMessage message={responseMessage} />
            </Box>
          ) : (
            <ContextBlock
              label="response"
              payload={req.response_payload ?? req.failure_reason ?? "(no response body recorded)"}
              scroll
            />
          )}
          {messages !== null && (
            <Box sx={{ mt: 0.5 }}>
              <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                request ·{" "}
                {incremental
                  ? `${shared} shared messages stored once · ${messages.length} new`
                  : `${messages.length} messages as sent`}
              </Typography>
              <Box
                sx={{
                  maxHeight: 280,
                  overflowY: "auto",
                  display: "flex",
                  flexDirection: "column",
                  gap: 0.5,
                  mt: 0.25,
                }}
              >
                {messages.map((m, i) => (
                  <ChatMessage key={i} message={m} />
                ))}
              </Box>
            </Box>
          )}
        </>
      )}
    </Box>
  );
}

/** The clicked task's recorded LLM requests/responses — HIDDEN by
 *  default; clicking the section loads them once (spinner on the toggle
 *  line, uncached: a live run records new ones) and renders them under
 *  the task's input/output contexts. A task's rows are its
 *  llm_context_id chain (a top-level step narrowed to its tick window,
 *  a subagent child its whole chain); tasks from runs that predate the
 *  chain binding have no linkage and say so. */
function TaskLlmRequests({
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
    ReturnType<typeof fetchStateLlmRequests>
  > | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!open || data !== null || error !== null) return;
    let live = true;
    fetchStateLlmRequests(threadId, stateId)
      .then((d) => {
        if (live) setData(d);
      })
      .catch((e: unknown) => {
        if (live) {
          setError(e instanceof Error ? e.message : String(e));
        }
      });
    return () => {
      live = false;
    };
  }, [open, data, error, threadId, stateId]);
  const loading = open && data === null && error === null;

  return (
    <Box sx={{ mt: 0.75 }}>
      <Chip
        size="small"
        variant="outlined"
        color={data !== null && data.requests.length > 0 ? "primary" : "default"}
        icon={
          loading ? (
            <CircularProgress size={12} />
          ) : open ? (
            <ExpandLess sx={{ fontSize: 16 }} />
          ) : (
            <ExpandMore sx={{ fontSize: 16 }} />
          )
        }
        label={
          data !== null
            ? `LLM requests — ${data.requests.length} recorded · ctx ${data.llm_context_id?.slice(0, 6) ?? "—"}`
            : "LLM requests"
        }
        onClick={() => setOpen((o) => !o)}
        sx={{
          height: 24,
          fontSize: "0.75rem",
          "& .MuiChip-icon": { fontSize: 16, ml: 0.5 },
        }}
      />
      <Collapse in={open} timeout="auto" unmountOnExit>
        {error !== null ? (
          <Typography variant="caption" color="error" sx={{ display: "block", pl: 0.5 }}>
            llm requests: {error}
          </Typography>
        ) : loading ? (
          <Typography variant="caption" color="text.disabled" sx={{ display: "block", pl: 0.5 }}>
            loading llm requests…
          </Typography>
        ) : data === null ? null : data.requests.length === 0 ? (
          <Typography variant="caption" color="text.disabled" sx={{ display: "block", pl: 0.5 }}>
            {hasContext
              ? "no llm requests recorded for this task"
              : "no llm-request linkage — this run predates the request-chain binding"}
          </Typography>
        ) : (
          <Box sx={{ display: "flex", flexDirection: "column", gap: 0.75, mt: 0.5 }}>
            {data.requests.map((req) => (
              <RequestRow key={req.request_id} req={req} />
            ))}
          </Box>
        )}
      </Collapse>
    </Box>
  );
}

export default function ThreadGraphView({
  threadId,
  expectLive = false,
  onSettled,
  refreshSignal = 0,
  onPlanTemplateSaved,
}: ThreadGraphViewProps) {
  const themeMode = useChartThemeMode();

  // ---- the polled thread graph + selections ----
  const { detail, loading, error, fetchedAt } = useThreadDetail(
    threadId,
    expectLive,
    refreshSignal,
  );
  // The optimistic planning-window canvas (buildPendingThreadGraphModel):
  // a generation in flight whose thread detail hasn't landed yet — the
  // daemon registers the thread only once POST /run reaches it, and the
  // planning rows persist a beat later — draws the running planning node
  // IMMEDIATELY (the Generate click's feedback), never an empty
  // placeholder. The real model replaces it the moment a fetch returns
  // the thread.
  const pendingModel = useMemo(
    () =>
      expectLive && detail === null
        ? buildPendingThreadGraphModel(threadId ?? "")
        : null,
    [expectLive, detail, threadId],
  );
  const model = useMemo(
    () => (detail !== null ? buildThreadGraphModel(detail) : pendingModel),
    [detail, pendingModel],
  );
  // The clicked graph circle (drives the beneath-the-graph items list).
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  // Step rows whose subagent sub-list is expanded (per state_id).
  const [expandedRows, setExpandedRows] = useState<Set<number>>(new Set());
  // Task rows whose input/output context detail is expanded (per
  // state_id — the row click, distinct from the chevron's subagents).
  const [ctxOpenRows, setCtxOpenRows] = useState<Set<number>>(new Set());
  // The header's thread-id copy button flips to a check for a beat.
  const [idCopied, setIdCopied] = useState(false);
  useEffect(() => {
    setSelectedNodeId(null);
    setExpandedRows(new Set());
    setCtxOpenRows(new Set());
  }, [threadId]);

  // THE SUPER THREAD ID (2026-11-06 three-chain refactor) — the run's
  // CHAIN ROOT (the plan graph's thread id = llm.deep_agent_states.
  // super_thread_id, shared by every thread of the run). It is THE run
  // identifier the header displays and the copy button copies; the read
  // resolves the whole chain from ANY member id, so when the view was
  // opened by a member's id the two differ and the member shows beside
  // the root as a small caption.
  const superThreadId =
    model?.superThreadId ?? detail?.super_thread_id ?? threadId;
  const viewingMemberId =
    threadId !== null && superThreadId !== null && threadId !== superThreadId;

  const copyThreadId = async () => {
    if (superThreadId === null) return;
    try {
      await navigator.clipboard.writeText(superThreadId);
      setIdCopied(true);
      setTimeout(() => setIdCopied(false), 2000);
    } catch {
      // clipboard unavailable (permissions) — nothing to do
    }
  };

  // The watched run just settled — the parent refreshes its history.
  const wasActiveRef = useRef(false);
  useEffect(() => {
    const active = model?.active ?? false;
    if (wasActiveRef.current && !active) onSettled?.();
    wasActiveRef.current = active;
  }, [model, onSettled]);

  const option = useMemo(
    () => (model !== null ? buildThreadGraphOption(model, themeMode, selectedNodeId) : null),
    [model, themeMode, selectedNodeId],
  );

  const items = useMemo(
    () => (model !== null && selectedNodeId !== null ? itemsUnderNodeId(model, selectedNodeId) : []),
    [model, selectedNodeId],
  );

  // The context view beneath the graph: the clicked circle, or — when
  // NOTHING is clicked — the SUPERVISOR by default (the main path's
  // checker: its lane state, its one LLM context and the kind
  // supervisor-review document are the run-level view). The canvas
  // highlight stays on actual clicks only; threads without a
  // supervisor (pre-feature / degraded execute-lane runs) keep the old
  // nothing-selected behavior.
  const effectiveNodeId =
    selectedNodeId ??
    (model?.nodeStates.has("supervisor") === true ? "supervisor" : null);

  // ---- the clicked node's own contexts (the runner's reference-by-id
  // design: the PLAN node's persisted payload is the plan JSON; every
  // runner-lane node's LLM context rides the registry as counts) ----
  const nodeLane = useMemo(
    () =>
      effectiveNodeId !== null
        ? detail?.node_states.find((n) => n.node === effectiveNodeId) ?? null
        : null,
    [detail, effectiveNodeId],
  );
  const nodeLlmContexts = useMemo(
    () =>
      effectiveNodeId !== null
        ? (detail?.llm_contexts ?? []).filter((c) => c.node === effectiveNodeId)
        : [],
    [detail, effectiveNodeId],
  );
  // Per-context LLM traffic (tokens + busy time) by context id — the
  // Contexts panel's consumption lines. A RUNNING node has no registry
  // item yet, but its chain is already recording in llm_requests.
  const usageByContext = useMemo(() => {
    const m = new Map<string, AgentLlmUsage>();
    for (const u of detail?.llm_usage ?? []) m.set(u.context_id, u);
    return m;
  }, [detail]);
  const nodeUsage =
    nodeLane?.llm_context_id != null
      ? usageByContext.get(nodeLane.llm_context_id) ?? null
      : null;
  const planContext = useMemo(
    () =>
      effectiveNodeId === "plan"
        ? (detail?.contexts ?? []).find((c) => c.kind === "plan") ?? null
        : null,
    [detail, effectiveNodeId],
  );
  // A graph's FINAL OUTPUT document — the RESULT graph's kind
  // final-result context (the run's answer document, the same payload
  // the Result view displays) on the chain's last output seat, or the
  // CUSTOM graph's kind graph-output handoff document on the middle
  // seat (what the result graph's reviews read as their input).
  const resultContext = useMemo(
    () =>
      isOutputNodeId(effectiveNodeId ?? "")
        ? (detail?.contexts ?? []).find(
            (c) =>
              c.kind ===
              (segmentIndexOfNodeId(effectiveNodeId ?? "") ===
                Math.max(0, (detail?.segments?.length ?? 1) - 1)
                ? "final-result"
                : "graph-output"),
          ) ?? null
        : null,
    [detail, effectiveNodeId],
  );
  // The supervisor node's MAIN-PATH REVIEW — the kind supervisor-review
  // context (the bounded cross-check document: per-step lines, the
  // issues, the overall verdict). What the default (nothing-selected)
  // view exists to show.
  const supervisorReview = useMemo(
    () =>
      effectiveNodeId === "supervisor"
        ? (detail?.contexts ?? []).find(
            (c) => c.kind === "supervisor-review",
          ) ?? null
        : null,
    [detail, effectiveNodeId],
  );
  // The clicked step's FORK RESULTS — the parent agent's reviewed
  // material: each child's subagent-result context (the answer the
  // parent's conversation gathered and synthesized into its own work;
  // for a DELEGATED step the fork WAS the engine and its answer became
  // the step's finding). Rendered in the parent's context section —
  // the merge-back edge's payload, so to speak.
  const forkReviews = useMemo(() => {
    if (effectiveNodeId === null || !effectiveNodeId.startsWith("step-")) {
      return [] as { stateId: number; title: string; status: string;
                     startedAt: string | null;
                     payload: unknown }[];
    }
    const stateId = Number(effectiveNodeId.slice(5));
    const children = model?.childrenByParent.get(stateId) ?? [];
    return children.map((child) => ({
      stateId: child.state_id,
      title: child.title,
      status: child.status,
      startedAt: child.started_at,
      payload:
        child.result_context_id !== null
          ? detail?.contexts.find(
              (c) => c.context_id === child.result_context_id,
            )?.payload ?? null
          : null,
    }));
  }, [effectiveNodeId, model, detail]);

  // The header's status line — the id itself renders in full beside it
  // (with its copy button), so only the state texts live here. The
  // pending-canvas windows (the run registering / its plan rows landing)
  // say so above the optimistic graph.
  const statusLine = useMemo(() => {
    if (threadId === null) {
      return expectLive
        ? "starting the run — the daemon registers the thread in moments…"
        : "no run picked";
    }
    if (detail === null && expectLive) {
      return "run registered — waiting for its plan to persist…";
    }
    return null;
  }, [threadId, detail, expectLive]);

  /** One row of the beneath-the-graph items list: status dot, the step's
   *  work tag + title, timing tail; clicking the row toggles its
   *  input/output context detail (the chevron expands its spawned-
   *  subagent rows, and only those — stopPropagation keeps the two
   *  toggles apart). */
  const renderItemRow = (stateId: number, depth: 0 | 1) => {
    const state =
      detail?.states.find((s) => s.state_id === stateId) ?? null;
    if (state === null) return null;
    const children = model?.childrenByParent.get(state.state_id) ?? [];
    const expanded = expandedRows.has(state.state_id);
    const ctxOpen = ctxOpenRows.has(state.state_id);
    const inputCtx =
      state.input_context_id !== null
        ? detail?.contexts.find((c) => c.context_id === state.input_context_id) ??
          null
        : null;
    const outputCtx =
      state.result_context_id !== null
        ? detail?.contexts.find((c) => c.context_id === state.result_context_id) ??
          null
        : null;
    return (
      <Box key={state.state_id}>
        <Box
          onClick={() =>
            setCtxOpenRows((prev) => {
              const next = new Set(prev);
              if (next.has(state.state_id)) next.delete(state.state_id);
              else next.add(state.state_id);
              return next;
            })
          }
          sx={{
            display: "flex",
            alignItems: "center",
            py: 0.25,
            pl: depth === 0 ? 1 : 5,
            pr: 1,
            borderRadius: 1,
            cursor: "pointer",
            "&:hover": { bgcolor: "action.hover" },
          }}
        >
          <Box sx={{ mr: 1, display: "flex" }}>
            <StatusChip status={state.status} compact />
          </Box>
          <Box sx={{ minWidth: 0, flexGrow: 1 }}>
            <Typography variant="body2" noWrap>
              {state.title}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {state.parent_state_id === null
                ? `${state.ordinal}. ${state.node}`
                : state.node}
              {state.started_at ? ` · ${state.started_at.slice(11, 19)}` : ""}
              {state.llm_context_id
                ? ` · ctx ${state.llm_context_id.slice(0, 6)}`
                : ""}
            </Typography>
          </Box>
          {children.length > 0 && (
            <IconButton
              size="small"
              onClick={(e) => {
                e.stopPropagation();
                setExpandedRows((prev) => {
                  const next = new Set(prev);
                  if (next.has(state.state_id)) next.delete(state.state_id);
                  else next.add(state.state_id);
                  return next;
                });
              }}
              aria-label="toggle subagents"
            >
              {expanded ? <ExpandLess fontSize="small" /> : <ExpandMore fontSize="small" />}
            </IconButton>
          )}
        </Box>
        {ctxOpen && (
          <Box sx={{ pl: depth === 0 ? 2 : 6, pr: 1, pb: 0.75 }}>
            {inputCtx === null && outputCtx === null ? (
              <Typography variant="caption" color="text.disabled">
                no persisted input/output context for this task
              </Typography>
            ) : (
              <Box sx={{ display: "flex", flexDirection: "column", gap: 0.75 }}>
                {inputCtx !== null && (
                  <ContextBlock label="Input" payload={inputCtx.payload} />
                )}
                {outputCtx !== null && (
                  <ContextBlock label="Output" payload={outputCtx.payload} />
                )}
              </Box>
            )}
            {threadId !== null && (
              <TaskLlmRequests
                threadId={threadId}
                stateId={state.state_id}
                hasContext={state.llm_context_id !== null}
              />
            )}
          </Box>
        )}
        {children.length > 0 && (
          <Collapse in={expanded} timeout="auto" unmountOnExit>
            {children.map((child) => renderItemRow(child.state_id, 1))}
          </Collapse>
        )}
      </Box>
    );
  };

  return (
    <Box>
      {/*
       * Merged-view header — the "LangGraph thread" label the result
       * panel's toggle selects, the status legend, and the live chip.
       * The chart renders bare (no card chrome): the result panel's
       * Paper is the outer card.
       */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1.5,
          flexWrap: "wrap",
          mb: 1,
        }}
      >
        <Typography variant="subtitle2">LangGraph thread</Typography>
        {(detail?.segments ?? []).length > 1 && (
          <Tooltip title="One run = THREE chained LangGraphs (plan → custom → result), one thread each, chained by super_thread_id — the canvas joins them with the dashed double-arrow handoff edges">
            <Chip
              size="small"
              variant="outlined"
              label={`chain · ${(detail?.segments ?? [])
                .map((s) => s.graph_type)
                .join(" → ")}`}
              sx={{ height: 22 }}
            />
          </Tooltip>
        )}
        {statusLine !== null && (
          <Typography variant="caption" color="text.secondary">
            {statusLine}
          </Typography>
        )}
        {superThreadId !== null && (
          <>
            <Tooltip title="The run's super_thread_id — the chain root (the plan graph's thread id) that every thread of the run (plan / custom / result) carries; GET /api/ai/threads/:id resolves the whole chain from it">
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: "flex", alignItems: "baseline", gap: 0.5 }}
              >
                <Box component="span" sx={{ fontFamily: "monospace" }}>
                  super
                </Box>
                <Box
                  component="span"
                  sx={{ fontFamily: "monospace", wordBreak: "break-all" }}
                >
                  {superThreadId}
                </Box>
              </Typography>
            </Tooltip>
            <Tooltip title={idCopied ? "Copied" : "Copy the super thread id"}>
              <IconButton
                size="small"
                aria-label="copy super thread id"
                onClick={copyThreadId}
                sx={{ p: 0.25 }}
              >
                {idCopied ? (
                  <Check sx={{ fontSize: 16 }} color="success" />
                ) : (
                  <ContentCopy sx={{ fontSize: 16 }} />
                )}
              </IconButton>
            </Tooltip>
          </>
        )}
        {viewingMemberId && (
          <Tooltip title="Opened by a chain member's thread id — the whole chain renders; the super id above is the run's root">
            <Typography variant="caption" color="text.secondary">
              member {threadId?.slice(0, 8)}…
            </Typography>
          </Tooltip>
        )}
        <Box
          sx={{
            display: "flex",
            gap: 1,
            alignItems: "center",
            ml: "auto",
            flexWrap: "wrap",
          }}
        >
          {(["completed", "in_progress", "failed", "pending", "skipped"] as const).map(
            (s) => (
              <Box key={s} sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                <StatusChip status={s} compact />
                <Typography variant="caption" color="text.secondary">
                  {s === "skipped" ? "not run" : s.replace("_", " ")}
                </Typography>
              </Box>
            ),
          )}
          {model?.active && (
            <Tooltip title={`Live — websocket updates + 3s poll fallback${fetchedAt ? ` · last ${fetchedAt.toLocaleTimeString()}` : ""}`}>
              <Chip
                size="small"
                color="primary"
                variant="outlined"
                icon={<Bolt />}
                label="live"
                sx={{ height: 22 }}
              />
            </Tooltip>
          )}
          <RunHeartbeat active={model?.active ?? false} />
          {/* The plan-template Save action: only a SETTLED run with a
              persisted plan can be templated (a live run's plan may still
              mutate; a plan-less degraded run has nothing to save). The
              save reads the CHAIN ROOT's plan context (the plan graph's
              thread — the super id), so a member-opened view saves from
              the root too. */}
          {superThreadId !== null &&
            model !== null &&
            !model.active &&
            (detail?.contexts ?? []).some((c) => c.kind === "plan") && (
              <SaveTemplateButton
                threadId={superThreadId}
                onSaved={onPlanTemplateSaved}
              />
            )}
        </Box>
      </Box>
      <BaseChart
        variant="bare"
        loading={loading && detail === null}
        error={error}
        option={option}
        emptyText={
          threadId === null
            ? expectLive
              ? "Starting the run — the planning node opens the moment the daemon registers it…"
              : "No run picked — click a Summary History dot"
            : expectLive && detail === null
              ? "Run registered — waiting for its plan to persist…"
              : "This run left no thread — stopped before its plan persisted"
        }
        height={model !== null && model.maxForkRows > 0
          ? 320 + Math.min(model.maxForkRows, 6) * 70
          : 320}
        onEvents={{
          click: (params: unknown) => {
            const p = params as { data?: { id?: string }; dataType?: string };
            const id = p.data?.id;
            if (p.dataType === "edge" || id === undefined) return;
            if (selectedNodeId === id) {
              setSelectedNodeId(null);
              setExpandedRows(new Set());
            } else {
              setSelectedNodeId(id);
              setExpandedRows(new Set());
            }
          },
        }}
        onCanvasClick={() => {
          setSelectedNodeId(null);
          setExpandedRows(new Set());
        }}
      />
      {/* ---- the context view beneath the graph: the clicked node's —
           or, by DEFAULT (nothing clicked), the supervisor's (the main
           path's checker: its runner-lane state, its ONE context and
           the kind supervisor-review document). ONE CONTEXT PER NODE
           (the runner's design — the node's whole execution, every LLM
           request in it, runs as one conversation referenced by id) ---- */}
      {effectiveNodeId !== null &&
        (nodeLane !== null ||
          planContext !== null ||
          resultContext !== null ||
          supervisorReview !== null) && (
          <Box sx={{ mt: 2 }}>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mb: 0.5 }}
            >
              {selectedNodeId === null
                ? `Contexts — ${effectiveNodeId} by default (no node clicked; click a circle for its own)`
                : "Contexts — one context per node, shared across its LLM requests"}
            </Typography>
            {nodeLane !== null && (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: "block" }}
              >
                {nodeLane.node} · {nodeLane.status} · model{" "}
                {nodeLane.model ?? "—"} · {nodeLane.n_llm_calls} llm calls ·{" "}
                {nodeLane.n_tool_calls} tool calls
                {nodeLane.n_states > 0 ? ` · ${nodeLane.n_states} tasks` : ""}
                {nodeLane.answer_chars > 0
                  ? ` · ${nodeLane.answer_chars} answer chars`
                  : ""}
                {nodeLane.elapsed_s !== null
                  ? ` · elapsed ${formatElapsedS(nodeLane.elapsed_s)}`
                  : ""}
                {usageTail(nodeUsage)}
                {nodeLane.error ? ` · ${nodeLane.error}` : ""}
              </Typography>
            )}
            {nodeLlmContexts.map((c) => (
              <Typography
                key={c.context_id}
                variant="caption"
                color="text.secondary"
                sx={{ display: "block" }}
              >
                ctx {c.context_id.slice(0, 6)} · {c.n_llm_calls} llm ·{" "}
                {c.n_tool_calls} tool requests under this one context
                {usageTail(usageByContext.get(c.context_id) ?? null)}
                {c.checkpoint_id
                  ? ` · ckpt ${c.checkpoint_id.slice(0, 8)}`
                  : ""}
              </Typography>
            ))}
            {planContext !== null && (
              <Box sx={{ mt: 1 }}>
                <ContextBlock label="plan" payload={planContext.payload} scroll />
              </Box>
            )}
            {resultContext !== null && (
              <Box sx={{ mt: 1 }}>
                <ContextBlock
                  label="final result — the Result view's document"
                  payload={resultContext.payload}
                  scroll
                />
              </Box>
            )}
            {effectiveNodeId === "supervisor" && (
              supervisorReview !== null ? (
                <Box sx={{ mt: 1 }}>
                  <ContextBlock
                    label="supervisor review — the watchdog's intervention on a root-path failure (error diagnosis · plan/topology verdict)"
                    payload={supervisorReview.payload}
                    scroll
                  />
                </Box>
              ) : (
                <Typography
                  variant="caption"
                  color="text.disabled"
                  sx={{ display: "block", mt: 1 }}
                >
                  {nodeLane?.status === "running"
                    ? "watching — intervenes only when a root-path node fails"
                    : "idle — no root-path failure (the watchdog made no intervention)"}
                </Typography>
              )
            )}
            {forkReviews.length > 0 && (
              <Box sx={{ mt: 1 }}>
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ display: "block", mb: 0.5 }}
                >
                  Fork results — merged back into this node ·{" "}
                  {nodeLane?.llm_context_id != null
                    ? "gathered & reviewed by this node's conversation (its synthesis rides this node's llm requests)"
                    : "delegation — the fork was this step's engine; its answer became the step's finding"}
                </Typography>
                {forkReviews.map((fork) => (
                  <Box key={fork.stateId} sx={{ mb: 0.75 }}>
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      sx={{ display: "block" }}
                    >
                      fork · {fork.status}
                      {fork.startedAt ? ` · ${fork.startedAt.slice(11, 19)}` : ""}{" "}
                      — {fork.title}
                    </Typography>
                    {fork.payload !== null && fork.payload !== undefined ? (
                      <ContextBlock label="fork answer" payload={fork.payload} scroll />
                    ) : (
                      <Typography variant="caption" color="text.disabled">
                        no persisted result context for this fork
                      </Typography>
                    )}
                  </Box>
                ))}
              </Box>
            )}
          </Box>
        )}
      {/* ---- the clicked node's tasks — the plan node's planning task
           (the running planning node every run begins with), the plan
           steps a step node's hop executed in plan order (not canvas
           nodes: the canvas is the graph's own topology only) ---- */}
      {selectedNodeId !== null && items.length > 0 && (
        <Box sx={{ mt: 2 }}>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ display: "block", mb: 0.5 }}
          >
            Tasks of {selectedNodeId} · walked inside its one context · click
            a row for its input/output
          </Typography>
          <Box sx={{ display: "flex", flexDirection: "column", gap: 0.25 }}>
            {items.map((s) => renderItemRow(s.state_id, 0))}
          </Box>
        </Box>
      )}
    </Box>
  );
}
