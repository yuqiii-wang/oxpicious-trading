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
 *     every 60s (AI_EVENTS_FALLBACK_POLL_MS — the event channel that
 *     used to push these instantly is intentionally disabled,
 *     2026-11-11) while any state is pending/in_progress and settles when
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
 *     token consumption in k (total, in · out · cached, from the
 *     context's llm_requests chain rollup — cached = the re-sent
 *     history overlap chained by prev_request_id); the node's own
 *     llm-context registry
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
 *     default behind an outlined "Agent steps" chip that loads them
 *     on first click (spinner while in flight; GET
 *     /threads/:id/states/:stateId/agent-steps — inference steps (the
 *     llm.llm_requests rows) + spawn steps (one per spawned sub node)
 *     rows attributed by chain: the task's llm_context_id, a top-level
 *     step narrowed to its start/finish tick window; a subagent child
 *     owns its whole chain). A SPAWN step row is itself CLICKABLE TO
 *     EXPAND — its "sub node #N agent steps" affordance lazily loads
 *     the child node's own agent steps (the same endpoint keyed on
 *     child_state_id) and renders them nested inline, spawn rows
 *     clickable the same way down the tree. Each request row defaults to the CONTENT
 *     view — the semantic data only (the final assistant message, the
 *     call's NEW request messages; chained requests store the shared
 *     conversation once) — with a content/raw toggle; raw shows the
 *     stored JSONB payloads as pretty JSON. Tasks without linkage say
 *     so. No side panel: the payloads render inline under the row.
 *
 * onSettled fires when the watched run transitions live → settled — the
 * parent refreshes its run history so the strip's dot counts jump.
 *
 * The extracted pieces live beside this file: ./format (formatters),
 * ./payloadViews (ContextBlock/ChatMessage/ToolCallArgs), ./stepRows
 * (the four step-type rows + the shared AgentStepList body),
 * ./TaskAgentSteps (the lazy accordion),
 * ./GraphBoundaryHandle (the drag-resize grip) and ./ContextsPanel
 * (the beneath-graph contexts view).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Collapse,
  IconButton,
  Tooltip,
  Typography,
} from "@mui/material";
import { Bolt, Check, ContentCopy, ExpandLess, ExpandMore, Refresh, Stop } from "@mui/icons-material";
import {
  BaseChart,
  useChartThemeMode,
} from "@/shared/charts/base-chart";
import {
  bareNodeId,
  buildPendingThreadGraphModel,
  buildThreadGraphModel,
  estimateFitScale,
  graphViewHeight,
  isOutputNodeId,
  itemsUnderNodeId,
  segmentIndexOfNodeId,
  FIT_FLOOR,
} from "./graphModel";
import { buildThreadGraphOption, defaultGraphView, ZOOM_MAX } from "./threadGraphOption";
import type { ThreadGraphModel } from "./graphModel";
import type { AgentLlmUsage } from "@/lib/api-client/aiThreads";
import StatusChip from "./StatusChip";
import RunHeartbeat from "./RunHeartbeat";

// THE AUTO-EXPAND budget (2026-11-14): a never-touched thread's spawn
// subtrees expand by default while the run has at most this many
// rendered child rows (kind-2 stubs excluded) — the forked/spawned
// nodes show without hunting for the ▸N cue; bigger runs keep the
// all-folded canvas so the graph fits the window (the fold's reason).
const AUTO_EXPAND_MAX_CHILDREN = 8;
// The shared empty registry (AUTO mode's folded face for big runs).
const NO_EXPANDED: ReadonlySet<string> = new Set<string>();
import {
  loadExpandedNodes,
  saveExpandedNodes,
} from "./expandedNodesStore";
import SaveTemplateButton from "./SaveTemplateButton";
import { useThreadDetail } from "./useThreadDetail";
import { formatElapsedS, usageTail } from "./format";
import { ContextBlock } from "./payloadViews";
import { TaskAgentSteps } from "./TaskAgentSteps";
import GraphBoundaryHandle from "./GraphBoundaryHandle";
import ContextsPanel from "./ContextsPanel";
import type { SpawnReview } from "./ContextsPanel";

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
   *  of waiting out the poll. 2026-11-11 the channel is intentionally
   *  disabled (AI_EVENTS_CHANNEL_DISABLED), so no bump arrives and the
   *  60s poll is the only refresh path. */
  refreshSignal?: number;
  /** Fires when the run's plan was saved as a template (the Save plan
   *  button) — the parent refreshes its template picker. */
  onPlanTemplateSaved?: () => void;
  /** Stops the live run (POST /api/ai/summary/stop keyed on the super
   *  thread id) — the header renders its Stop button ONLY when this
   *  handler is present AND the canvas shows a live run (any node
   *  running/pending, the same flag the live chip reads). The view owns
   *  the button's busy state + the post-stop refresh pulses; the parent
   *  owns the API call and its error/notice surface (never rethrows). */
  onStop?: (superThreadId: string) => Promise<void>;
  /** The parent is still resolving WHICH run to show (the Summary
   *  History's dates / picked-day rows fetch in flight) — while nothing
   *  is picked yet, the placeholder spins instead of telling the user
   *  to click a dot they already clicked. A genuine empty (nothing
   *  loading, no run picked) keeps the instructive text. */
  resolvingPick?: boolean;
}

export default function ThreadGraphView({
  threadId,
  expectLive = false,
  onSettled,
  refreshSignal = 0,
  onPlanTemplateSaved,
  onStop,
  resolvingPick = false,
}: ThreadGraphViewProps) {
  const themeMode = useChartThemeMode();

  // ---- the polled thread graph + selections ----
  const { detail, loading, error, fetchedAt, refresh, refreshing } =
    useThreadDetail(threadId, expectLive, refreshSignal);
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
  // THE FOLD REGISTRY — the canvas node ids ("step-<id>" / "spawn-<id>")
  // whose spawn spurs render. 2026-11-14 the DEFAULT-EXPAND default: a
  // thread the user never touched (no persisted entry) AUTO-EXPANDS its
  // small spawn subtrees (AUTO_EXPAND_MAX_CHILDREN child rows) so the
  // forked/spawned nodes SHOW — the old all-folded default hid every
  // spawned node behind a faint ▸N cue and read as "no forked/spawned
  // nodes" (the user's 2026-11-14 report). Big subtrees stay folded (the
  // layout-fit reason the fold exists). The FIRST parent click flips the
  // thread to its EXPLICIT registry (`expandedNodes`, persisted verbatim
  // — an empty set = the user's all-folded), which the reload restores.
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(new Set());
  const [autoExpand, setAutoExpand] = useState(false);
  // The AUTO mode's synchronous mirror — the persist effect reads the
  // ref (the state closure lags one commit; that lag wrote an empty
  // registry over the armed auto default under StrictMode's
  // double-invoke).
  const autoExpandRef = useRef(false);
  const { effectiveExpanded, model } = useMemo(() => {
    if (detail === null) {
      return { effectiveExpanded: expandedNodes, model: pendingModel };
    }
    if (!autoExpand) {
      return {
        effectiveExpanded: expandedNodes,
        model: buildThreadGraphModel(detail, expandedNodes),
      };
    }
    // AUTO mode: the expandable parents are exactly the model's
    // childCount keys with a positive count; small subtrees (by the
    // rendered child-row total, kind-2 stubs excluded) expand all.
    const base = buildThreadGraphModel(detail, NO_EXPANDED);
    const kids = detail.states.filter(
      (s) => s.parent_state_id !== null && s.child_kind !== 2,
    );
    const expanded =
      kids.length === 0 || kids.length > AUTO_EXPAND_MAX_CHILDREN
        ? NO_EXPANDED
        : new Set(
            [...base.childCountByNodeId.entries()]
              .filter(([, n]) => n > 0)
              .map(([id]) => id),
          );
    return {
      effectiveExpanded: expanded,
      model: buildThreadGraphModel(detail, expanded),
    };
  }, [detail, pendingModel, autoExpand, expandedNodes]);
  // The clicked graph circle (drives the beneath-the-graph items list).
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  // Step rows whose subagent sub-list is expanded (per state_id).
  const [expandedRows, setExpandedRows] = useState<Set<number>>(new Set());
  // Task rows whose input/output context detail is expanded (per
  // state_id — the row click, distinct from the chevron's subagents).
  const [ctxOpenRows, setCtxOpenRows] = useState<Set<number>>(new Set());
  // The header's thread-id copy button flips to a check for a beat.
  const [idCopied, setIdCopied] = useState(false);
  // The user's roamed graph zoom/center (content coords, read off the
  // live option after every graphRoam event) — folded back into each
  // rebuilt option so the 60s live poll, node clicks and theme toggles
  // never yank the pan/zoom back to the fit view. Reset on thread
  // change (the view of a different run starts at its own fit).
  const roamRef = useRef<{ zoom: number; center: [number, number] } | null>(
    null,
  );
  // The chart instance (structural — only getOption is read, inside the
  // graphRoam listener, to lift the roamed zoom/center the action
  // handler just wrote into the series model).
  const chartRef = useRef<{ getOption: () => unknown } | null>(null);
  useEffect(() => {
    setSelectedNodeId(null);
    setExpandedRows(new Set());
    setCtxOpenRows(new Set());
    // The fold registry RESTORES (not resets — see expandedNodesStore):
    // a non-empty stored entry restores verbatim; null (never touched
    // — or an empty entry, which reads the same since 2026-11-15)
    // flips the AUTO default on — small spawn subtrees expand, and the
    // graph reflows to exactly the shape the user left it in once they
    // click. The mode lands in the REF synchronously (the persist
    // effect below reads the ref, never the stale state closure — the
    // closure pass under StrictMode's double-invoke wrote an empty
    // registry over the armed auto default, blocking it forever).
    const stored = loadExpandedNodes(threadId);
    if (stored === null) {
      autoExpandRef.current = true;
      setAutoExpand(true);
      setExpandedNodes(new Set());
    } else {
      autoExpandRef.current = false;
      setAutoExpand(false);
      setExpandedNodes(stored);
    }
    roamRef.current = null;
  }, [threadId]);
  // Persist every expansion/fold click under the thread id. Declared
  // AFTER the restore effect so a thread switch writes back the freshly
  // restored set (never the previous thread's registry), and the page
  // refresh finds the canvas as it was left. The FIRST run per threadId
  // is SKIPPED: at that commit `expandedNodes` is still the stale
  // all-folded default (the restore's setState has not landed yet), and
  // writing it would clobber the stored entry before the restore reads
  // it — under StrictMode's double effect pass the second restore would
  // then read the clobbered store and the fold state would never
  // survive (observed live 2026-11-11: the seeded entry wiped to {}).
  // AUTO mode never persists: the thread stays never-touched until the
  // user's first click flips it to the explicit registry (the store's
  // null entry is what re-arms the auto default on the next visit).
  const persistedThreadRef = useRef<string | null>(null);
  useEffect(() => {
    if (persistedThreadRef.current !== threadId) {
      persistedThreadRef.current = threadId;
      return;
    }
    // The REF, not the state closure: the restore's setState lands a
    // commit later, and the stale-closure pass is what wrote the
    // blocking empty entries (see the store's header).
    if (autoExpandRef.current) return;
    saveExpandedNodes(threadId, expandedNodes);
  }, [threadId, expandedNodes, autoExpand]);
  // The chart box — the anchor the canvas width measurement reads (the
  // model is viewport-agnostic on purpose).
  const chartBoxRef = useRef<HTMLDivElement | null>(null);
  // The canvas' measured width — the fit-scale estimate the view's
  // zoom floor needs.
  const [chartWidth, setChartWidth] = useState(0);
  useEffect(() => {
    const el = chartBoxRef.current;
    if (el === null) return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? 0;
      setChartWidth((prev) => (Math.abs(prev - w) > 0.5 ? w : prev));
    });
    ro.observe(el);
    setChartWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);
  // The user-dragged viewport height override (px) — set by dragging
  // the graph's bottom boundary, cleared by double-clicking it. Unlike
  // the roam view (reset per thread) it persists across thread changes:
  // a viewport preference, not a view of one run's geometry.
  const [heightOverride, setHeightOverride] = useState<number | null>(null);

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

  // The header Stop button (the onStop prop's affordance): cancel the
  // live chain keyed on the super thread id. THE CONFIRMED STOP — the
  // daemon's /stop resolves only AFTER the cancelled run finished
  // unwinding and settling the whole chain (server-side
  // STOP_SETTLE_WAIT_SEC bound), so the single refresh below draws the
  // settled rows straight away: the running node keeps its live
  // appearance while the await is in flight (the button reads
  // "Stopping…") and turns settled grey exactly when the server
  // confirms — never off the bare stop request, and no speculative
  // re-pulses (the pre-confirmation refresh race they papered over is
  // gone).
  const [stopping, setStopping] = useState(false);
  const handleStop = async () => {
    if (onStop === undefined || superThreadId === null || stopping) return;
    setStopping(true);
    try {
      await onStop(superThreadId);
      refresh();
    } finally {
      setStopping(false);
    }
  };

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

  // The canvas height — TRACKS the content: the model's natural height
  // (graphViewHeight = content height × FIT_FLOOR), so the vertical fit
  // ratio stays at the floor as subtrees expand and collapse and the
  // rows keep their spacing. A boundary drag overrides it; double-click
  // resets to the natural height.
  const graphHeight = useMemo(
    () =>
      heightOverride ??
      (model !== null ? graphViewHeight(model) : 360),
    [model, heightOverride],
  );
  // The refs the view lock reads — the lock memo deliberately does NOT
  // depend on the model (an expansion reflow must never re-fit the
  // view); it reads the CURRENT model/canvas height through them.
  const modelRef = useRef<ThreadGraphModel | null>(null);
  modelRef.current = model;
  const graphHeightRef = useRef(360);
  graphHeightRef.current = graphHeight;
  // THE VIEW LOCK — the frozen center (and the zoom floor it locked
  // at), recomputed ONLY on viewport changes: thread switch, canvas
  // width (panel/window resize), boundary drag — plus the moment the
  // model first becomes measurable (chartWidth is often already
  // measured when the detail lands; without the ready flag the lock
  // would stay null forever and the view would fall back to the
  // auto-fit). Expansion toggles, poll ticks and node selections keep
  // the lock — the graph re-renders IN PLACE, the anchor point never
  // moves.
  const modelReady = model !== null && chartWidth > 0;
  const viewportKey = `${threadId ?? ""}|${chartWidth}|${heightOverride ?? "x"}`;
  const viewLock = useMemo(
    () => {
      const m = modelRef.current;
      const h = graphHeightRef.current;
      if (m === null || chartWidth <= 0 || h <= 0) return null;
      return defaultGraphView(
        m,
        estimateFitScale(m, chartWidth, h),
        chartWidth,
      );
    },
    // viewportKey + modelReady encode every legitimate re-fit trigger;
    // reading the model/canvas height through refs IS the freeze.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [viewportKey, modelReady],
  );
  const option = useMemo(() => {
    if (model === null) return null;
    // THE SPACING FLOOR RIDES ON THE FROZEN VIEW — the center stays
    // locked (or roamed), and the zoom is the frozen zoom RAISED (never
    // lowered) to the floor of the CURRENT fit: an expansion that would
    // otherwise compress the smallest nodes re-scales around the
    // anchored point instead of squeezing, and the extra content pans.
    const roamed = roamRef.current;
    const fs = estimateFitScale(model, chartWidth, graphHeight);
    const floorZoom = fs < FIT_FLOOR
      ? Math.min(FIT_FLOOR / Math.max(fs, 1e-6), ZOOM_MAX)
      : 1;
    const zoom = roamed?.zoom ?? Math.max(viewLock?.zoom ?? 1, floorZoom);
    const center = roamed?.center ?? viewLock?.center;
    return buildThreadGraphOption(
      model,
      themeMode,
      selectedNodeId,
      center !== undefined ? { roam: { zoom, center } } : { roam: null },
      effectiveExpanded,
    );
  }, [model, themeMode, selectedNodeId, chartWidth, graphHeight, viewLock, effectiveExpanded]);

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
      bareNodeId(effectiveNodeId ?? "") === "plan"
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
  // The clicked step's SPAWN RESULTS — the parent agent's reviewed
  // material: each child's subagent-result context (the answer the
  // parent's conversation gathered and synthesized into its own work;
  // for a DELEGATED step the spawn WAS the engine and its answer became
  // the step's finding). A clicked SPAWN node with its own sub-spawns
  // renders the same section for its children. Rendered in the parent's
  // context section — the merge-back edge's payload, so to speak.
  const spawnReviews = useMemo(() => {
    if (
      effectiveNodeId === null ||
      (!effectiveNodeId.startsWith("step-") &&
        !effectiveNodeId.startsWith("spawn-") &&
        !effectiveNodeId.startsWith("fork-") &&
        !effectiveNodeId.startsWith("old-"))
    ) {
      return [] as SpawnReview[];
    }
    const stateId = Number(effectiveNodeId.replace(
      /^(step|spawn|fork|old|resume)-/,
      "",
    ));
    const children = model?.childrenByParent.get(stateId) ?? [];
    return children.map((child) => ({
      stateId: child.state_id,
      title: child.title,
      status: child.status,
      startedAt: child.started_at,
      childKind: child.child_kind,
      payload:
        child.result_context_id !== null
          ? detail?.contexts.find(
              (c) => c.context_id === child.result_context_id,
            )?.payload ?? null
          : null,
    }));
  }, [effectiveNodeId, model, detail]);

  // A clicked SPAWN node's own faces — spawn children are not
  // runner-lane nodes (no node_states store item; their node value is
  // the goal slug, never the canvas id), so the panel joins through the
  // child's STATE row: the context registry's state_id key for its one
  // llm context, and the state's input/result context ids for its
  // prompt + answer payloads.
  const spawnState = useMemo(() => {
    if (
      effectiveNodeId === null ||
      (!effectiveNodeId.startsWith("spawn-") &&
        !effectiveNodeId.startsWith("fork-") &&
        !effectiveNodeId.startsWith("old-") &&
        !effectiveNodeId.startsWith("resume-"))
    ) {
      return null;
    }
    const sliceLen = effectiveNodeId.startsWith("old-") ? 4
      : effectiveNodeId.startsWith("fork-") ? 5
      : effectiveNodeId.startsWith("resume-") ? 7 : 6;
    return model?.statesById.get(Number(effectiveNodeId.slice(sliceLen)))
      ?? null;
  }, [effectiveNodeId, model]);
  const spawnCtx = useMemo(
    () =>
      spawnState !== null
        ? detail?.llm_contexts.find(
            (c) => c.state_id === spawnState.state_id,
          ) ?? null
        : null,
    [spawnState, detail],
  );

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
              {state.started_at !== null && state.finished_at !== null
                ? ` · ${formatElapsedS(
                    (new Date(state.finished_at).getTime()
                      - new Date(state.started_at).getTime()) / 1000,
                  )}`
                : state.started_at !== null
                  ? " · running"
                  : ""}
              {usageTail(usageByContext.get(state.llm_context_id ?? "") ?? null)}
              {state.llm_context_id !== null &&
                !usageByContext.has(state.llm_context_id)
                ? " · — tok"
                : ""}
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
              <TaskAgentSteps
                threadId={threadId}
                stateId={state.state_id}
                hasContext={state.llm_context_id !== null}
                onActionDone={refresh}
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
          <Tooltip title="One run = THREE chained LangGraphs (plan → custom → result), one thread each, chained by super_thread_id — the canvas joins them with the dashed handoff edges, chain-styled across the inter-graph gap">
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
          {/* The proactive pull: one fetch NOW from the backend — the
              60s live poll is automatic (the websocket push is
              intentionally disabled, 2026-11-11), this is the user's
              manual belt (and the only refresh path on a settled run,
              whose poll loop has stopped). */}
          <Tooltip title="Refresh now — pull the run's latest state from the backend">
            <span>
              <IconButton
                size="small"
                aria-label="refresh run state"
                onClick={refresh}
                disabled={threadId === null}
                sx={{ p: 0.5 }}
              >
                {refreshing ? (
                  <CircularProgress size={16} />
                ) : (
                  <Refresh sx={{ fontSize: 18 }} />
                )}
              </IconButton>
            </span>
          </Tooltip>
          {model?.active && (
            <Tooltip title={`Live — 60s poll refresh (websocket disabled)${fetchedAt ? ` · last ${fetchedAt.toLocaleTimeString()}` : ""}`}>
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
          {/* The live run's Stop: the canvas' own affordance (the page
              toolbar's Stop is gated on the daemon's active-run
              registry, not on what this view shows). Renders beside the
              live chip — the pair reads "this run is live, stop it" —
              and only when the parent wired onStop. The optimistic
              planning canvas (active: true, superThreadId = threadId)
              gets it too, so a generation in flight is stoppable before
              its first detail fetch lands. */}
          {onStop !== undefined &&
            superThreadId !== null &&
            model?.active && (
              <Tooltip title="Stop the run — cancels the chain and settles every remaining node">
                <span>
                  <Button
                    variant="outlined"
                    color="error"
                    size="small"
                    startIcon={
                      stopping ? <CircularProgress size={14} /> : <Stop />
                    }
                    onClick={handleStop}
                    disabled={stopping}
                  >
                    {stopping ? "Stopping…" : "Stop"}
                  </Button>
                </span>
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
      <Box ref={chartBoxRef}>
        <BaseChart
          variant="bare"
          loading={
            (loading && detail === null) ||
            // The pick-resolve windows (page mount's dates fetch, a dot
            // click's day-rows fetch): nothing is picked yet, so spin in
            // place of the "No run picked" caption. Scoped to the
            // no-run placeholder — a mounted graph's background
            // history refresh must not flash the spinner; the live
            // window keeps its own "Starting the run…" text (no fetch
            // is in flight there — the daemon just hasn't registered).
            (threadId === null && !expectLive && resolvingPick)
          }
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
          height={graphHeight}
          onReady={(chart) => {
            chartRef.current = chart;
          }}
          onEvents={{
            // The roamed zoom/center land in the series model via the
            // graphRoam action; lift them here so the next rebuild (60s
            // live poll, node click, theme toggle) re-applies them.
            graphRoam: () => {
              const chart = chartRef.current;
              if (chart === null) return;
              const opt = chart.getOption() as
                | { series?: { zoom?: number; center?: number[] }[] }
                | undefined;
              const s = opt?.series?.[0];
              if (
                typeof s?.zoom === "number" &&
                Array.isArray(s.center) &&
                s.center.length === 2
              ) {
                roamRef.current = {
                  zoom: s.zoom,
                  center: [s.center[0], s.center[1]],
                };
              }
            },
            click: (params: unknown) => {
              const p = params as { data?: { id?: string }; dataType?: string };
              const id = p.data?.id;
              if (p.dataType === "edge" || id === undefined) return;
              // A PARENT node toggles its spawn subtree (the ▸N/▾N cue's
              // contract — the ONLY expand/fold control) while the
              // click also selects the node as before; childless nodes
              // just select. In AUTO mode the first toggle seeds the
              // EXPLICIT registry from the auto-expanded set (the
              // thread stops being never-touched), so the user's fold
              // survives the reload (the store's contract).
              if (model?.childCountByNodeId.has(id) === true) {
                setAutoExpand(false);
                setExpandedNodes(() => {
                  const next = new Set(effectiveExpanded);
                  if (next.has(id)) next.delete(id);
                  else next.add(id);
                  return next;
                });
              }
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
      </Box>
      {model !== null && (
        <GraphBoundaryHandle height={graphHeight} onResize={setHeightOverride} />
      )}
      {/* ---- the context view beneath the graph: the clicked node's —
           or, by DEFAULT (nothing clicked), the supervisor's (the main
           path's checker: its runner-lane state, its ONE context and
           the kind supervisor-review document) ---- */}
      <ContextsPanel
        threadId={threadId}
        effectiveNodeId={effectiveNodeId}
        defaultSelection={selectedNodeId === null}
        nodeLane={nodeLane}
        nodeLlmContexts={nodeLlmContexts}
        nodeUsage={nodeUsage}
        usageByContext={usageByContext}
        planContext={planContext}
        resultContext={resultContext}
        supervisorReview={supervisorReview}
        spawnReviews={spawnReviews}
        spawnState={spawnState}
        spawnCtx={spawnCtx}
        onRefresh={refresh}
      />
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
