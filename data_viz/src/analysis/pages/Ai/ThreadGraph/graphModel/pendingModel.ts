/** The optimistic planning-window canvas — the ghost chain a run in
 *  flight draws before its thread detail exists. */
import type {
  AgentLlmContext,
  AgentNodeState,
  AgentThreadState,
} from "@/lib/api-client/aiThreads";
import type { ThreadGraphModel, ThreadGraphNode } from "./types";
import { GAP_COL, X_CHAIN_GAP, halfFootprint } from "./footprints";
import { contentBoundsOf } from "./items";

/** The OPTIMISTIC planning-window canvas — what a generation in flight
 *  draws before its thread detail exists (the daemon registers the
 *  thread only once POST /run lands, and the planning rows persist a
 *  beat later): the run's THREE-GRAPH CHAIN as ghosts — plan
 *  (in_progress, "planning…") → handoff → custom (pending, the steps
 *  run here once the plan lands) → handoff → result (pending, the
 *  review wave + the final answer) → END — so the Generate click shows
 *  the chain shape immediately and the canvas never flashes when the
 *  real detail replaces this model. Positions use the same
 *  footprint-measured lane advances as the real build. */
export function buildPendingThreadGraphModel(
  threadId: string,
): ThreadGraphModel {
  // start → plan → output, then the two handoff gaps and END — every
  // advance from the footprint registry, exactly like buildSegmentModel.
  const startEdge = halfFootprint("endpoint");
  const planX = startEdge + GAP_COL + halfFootprint("runner");
  const outputX = planX + halfFootprint("runner") + GAP_COL
    + halfFootprint("output");
  const endX = outputX + 2 * X_CHAIN_GAP + halfFootprint("output")
    + GAP_COL + halfFootprint("endpoint");
  return {
    threadId,
    nodes: [
      { id: "start", kind: "endpoint", label: "START", status: "neutral",
        x: 0, y: 0 },
      { id: "plan", kind: "runner", label: "plan",
        sublabel: "planning…", status: "in_progress", x: planX, y: 0 },
      { id: "output", kind: "output", label: "output",
        sublabel: "the plan", status: "in_progress", x: outputX, y: 0 },
      { id: "s1:output", kind: "output", label: "output",
        sublabel: "findings synthesis", status: "pending",
        x: outputX + X_CHAIN_GAP, y: 0 },
      { id: "s2:output", kind: "output", label: "output",
        sublabel: "the run's final answer", status: "pending",
        x: outputX + X_CHAIN_GAP * 2, y: 0 },
      { id: "end", kind: "endpoint", label: "END", status: "neutral",
        x: endX, y: 0 },
    ],
    edges: [
      { source: "start", target: "plan" },
      { source: "plan", target: "output" },
      { source: "output", target: "s1:output", kind: "handoff" },
      { source: "s1:output", target: "s2:output", kind: "handoff" },
      { source: "s2:output", target: "end" },
    ],
    active: true,
    superThreadId: threadId,
    childrenByParent: new Map<number, AgentThreadState[]>(),
    childCountByNodeId: new Map<string, number>(),
    statesById: new Map<number, AgentThreadState>(),
    maxSpawnRows: 0,
    contentBounds: contentBoundsOf([
      { x: 0, y: 0 }, { x: planX, y: 0 }, { x: outputX, y: 0 },
      { x: outputX + X_CHAIN_GAP, y: 0 },
      { x: outputX + X_CHAIN_GAP * 2, y: 0 }, { x: endX, y: 0 },
    ] as ThreadGraphNode[]),
    topLevelStates: [],
    planTask: null,
    nodeStates: new Map<string, AgentNodeState>(),
    nodeStatesByStateId: new Map<number, AgentNodeState>(),
    contextsById: new Map<string, AgentLlmContext>(),
    mode: "steps",
    segments: [],
  };
}
