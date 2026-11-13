/** The chain builder — splits a merged thread detail per segment,
 *  builds each segment's lane, and lays the segments LEFT TO RIGHT
 *  with the handoff edges between them. */
import type {
  AgentLlmContext,
  AgentNodeState,
  AgentThreadDetail,
  AgentThreadState,
} from "@/lib/api-client/aiThreads";
import type {
  ThreadGraphEdge,
  ThreadGraphModel,
  ThreadGraphNode,
  ThreadSegmentView,
} from "./types";
import { X_CHAIN_GAP, Y_STEP } from "./footprints";
import { contentBoundsOf } from "./items";
import { buildSegmentModel } from "./buildSegmentModel";

/** The chain-level flags shared by every segment build. */
function chainFlags(detail: AgentThreadDetail): {
  active: boolean;
  hasFinalResult: boolean;
  hasGraphOutput: boolean;
} {
  return {
    active:
      detail.states.some(
        (s) => s.status === "pending" || s.status === "in_progress",
      ) || detail.node_states.some((s) => s.status === "running"),
    hasFinalResult: detail.contexts.some((c) => c.kind === "final-result"),
    hasGraphOutput: detail.contexts.some((c) => c.kind === "graph-output"),
  };
}

/** Builds the canvas model. `expanded` carries the EXPANDED canvas
 *  node ids — parents whose spawn spurs render. The default (empty set)
 *  folds every spawn subtree: the smallest level hides and the layout
 *  reserves no room for it, so the main path stays properly spaced and
 *  the graph fits the window; a parent click expands one level. */
export function buildThreadGraphModel(
  detail: AgentThreadDetail,
  expanded: ReadonlySet<string> = new Set<string>(),
): ThreadGraphModel {
  const segMetas =
    detail.segments !== undefined && detail.segments.length > 0
      ? detail.segments
      : [
          {
            thread_id: detail.super_thread_id ?? detail.thread_id,
            graph_type: "single",
          } as const,
        ];
  const flags = chainFlags(detail);
  const root = detail.super_thread_id ?? detail.thread_id;

  if (segMetas.length === 1) {
    // Legacy / degraded one-thread runs (and any single-segment chain —
    // a run stopped inside its plan phase): the exact pre-chain canvas.
    const m = buildSegmentModel({
      index: 0,
      last: true,
      graphType: segMetas[0].graph_type,
      states: detail.states,
      nodeStatesRows: detail.node_states,
      contexts: detail.contexts,
      llmContexts: detail.llm_contexts,
      llmUsage: detail.llm_usage,
      expanded,
      ...flags,
    });
    return {
      ...m,
      threadId: detail.thread_id,
      superThreadId: root,
      contentBounds: contentBoundsOf(m.nodes),
      segments: [
        {
          threadId: segMetas[0].thread_id,
          graphType: segMetas[0].graph_type,
          x0: m.minX,
          x1: m.maxX,
        },
      ],
    };
  }

  // ---- THE CHAIN: split the merged detail per segment (states by
  // their thread_id; the store's node items by their s<k>: namespace,
  // falling back to the owning state's segment), build each segment's
  // lane locally, then lay them LEFT TO RIGHT with the HANDOFF edge
  // between consecutive terminals and entries.
  const segStates = segMetas.map((meta) =>
    detail.states.filter((s) => s.thread_id === meta.thread_id),
  );
  const stateOwner = new Map<number, number>();
  segStates.forEach((rows, k) =>
    rows.forEach((s) => stateOwner.set(s.state_id, k)),
  );
  const segNodeRows: AgentNodeState[][] = segMetas.map(() => []);
  for (const ns of detail.node_states) {
    const m = ns.node.match(/^s(\d+):/);
    const k =
      m !== null
        ? Number(m[1])
        : ns.state_id !== null
          ? stateOwner.get(ns.state_id) ?? 0
          : 0;
    (segNodeRows[k] ?? segNodeRows[0]).push(ns);
  }
  const models = segMetas.map((meta, k) =>
    buildSegmentModel({
      index: k,
      last: k === segMetas.length - 1,
      graphType: meta.graph_type,
      states: segStates[k],
      nodeStatesRows: segNodeRows[k],
      contexts: detail.contexts,
      llmContexts: detail.llm_contexts,
      llmUsage: detail.llm_usage,
      expanded,
      ...flags,
    }),
  );

  const nodes: ThreadGraphNode[] = [];
  const edges: ThreadGraphEdge[] = [];
  const views: ThreadSegmentView[] = [];
  const childrenByParent = new Map<number, AgentThreadState[]>();
  const childCountByNodeId = new Map<string, number>();
  const statesById = new Map<number, AgentThreadState>();
  const nodeStates = new Map<string, AgentNodeState>();
  const nodeStatesByStateId = new Map<number, AgentNodeState>();
  const contextsById = new Map<string, AgentLlmContext>();
  let maxSpawnRows = 0;
  let anySteps = false;
  const topLevelStates: AgentThreadState[] = [];
  let shift = 0;
  models.forEach((m, k) => {
    for (const n of m.nodes) {
      n.x += shift;
      nodes.push(n);
    }
    edges.push(...m.edges);
    m.childrenByParent.forEach((v, key) => childrenByParent.set(key, v));
    m.childCountByNodeId.forEach((v, key) => childCountByNodeId.set(key, v));
    m.statesById.forEach((v, key) => statesById.set(key, v));
    m.nodeStates.forEach((v, key) => nodeStates.set(key, v));
    m.nodeStatesByStateId.forEach((v, key) => nodeStatesByStateId.set(key, v));
    m.contextsById.forEach((v, key) => contextsById.set(key, v));
    maxSpawnRows = Math.max(maxSpawnRows, m.maxSpawnRows);
    anySteps = anySteps || m.mode === "steps";
    topLevelStates.push(...m.topLevelStates);
    views.push({
      threadId: segMetas[k].thread_id,
      graphType: segMetas[k].graph_type,
      x0: shift,
      x1: shift + m.maxX,
    });
    // The segment's floating CAPTION above its lane — the graph's type
    // (plan / custom / result), the chain's reading aid. It hangs 1.4
    // rows above the segment's own TOP node (the supervisor's circle
    // when present, else the lane) instead of a fixed band — a folded
    // (shallow) segment carries no dead air above it, so the content
    // bbox hugs the drawn graph.
    const segTop = m.nodes.reduce((top, n) => Math.min(top, n.y), 0);
    nodes.push({
      id: `caption-${k}`, kind: "caption",
      label: segMetas[k].graph_type, status: "neutral",
      x: shift + m.maxX / 2, y: segTop - 1.4 * Y_STEP,
    });
    if (k > 0) {
      // THE INTER-GRAPH EDGE — one graph's FINAL OUTPUT to the next
      // graph's entry: chain-styled but dashed, spanning the wider
      // X_CHAIN_GAP, so the two graphs read as two graphs joined by
      // the handoff.
      edges.push({
        source: models[k - 1].terminalId,
        target: m.firstNodeId,
        kind: "handoff",
      });
    }
    shift += m.maxX + X_CHAIN_GAP;
  });

  return {
    threadId: detail.thread_id,
    superThreadId: root,
    nodes,
    edges,
    active: flags.active,
    childrenByParent,
    childCountByNodeId,
    statesById,
    maxSpawnRows,
    contentBounds: contentBoundsOf(nodes),
    topLevelStates,
    planTask: models[0].planTask,
    nodeStates,
    nodeStatesByStateId,
    contextsById,
    mode: anySteps ? "steps" : "legacy",
    segments: views,
  };
}
