/** The beneath-graph task rows for a clicked node, and the content
 *  bounding box over a node list. */
import type { AgentThreadState } from "@/lib/api-client/aiThreads";
import type {
  ThreadGraphContentBounds,
  ThreadGraphModel,
  ThreadGraphNode,
} from "./types";
import { bareNodeId, isOutputNodeId } from "./nodeIds";

/** The tasks listed beneath the graph for a clicked node — the plan
 *  node's planning task, a step node's OWN row (its spawns expand
 *  beneath it), a spawn circle's own child row, legacy execute's whole
 *  task list; result and supervisor own no tasks (their views stay
 *  strictly their contexts — the supervisor's review document). */
export function itemsUnderNodeId(
  model: ThreadGraphModel,
  nodeId: string,
): AgentThreadState[] {
  const bare = bareNodeId(nodeId);
  if (bare === "plan") {
    return model.planTask !== null ? [model.planTask] : [];
  }
  if (nodeId === "supervisor" || isOutputNodeId(nodeId)) {
    // The output seats and the supervisor own no task rows — their
    // views stay strictly their contexts (the graph-output handoff
    // document / the final answer / the supervisor's review).
    return [];
  }
  if (nodeId.startsWith("spawn-")) {
    const id = Number(nodeId.slice(5));
    const state = model.statesById.get(id);
    return state === undefined ? [] : [state];
  }
  if (nodeId.startsWith("fork-")) {
    // An inherit-context child (child_kind 1) — its canvas node id
    // carries the fork- prefix; the same one-row read as a spawn's.
    const id = Number(nodeId.slice(5));
    const state = model.statesById.get(id);
    return state === undefined ? [] : [state];
  }
  if (nodeId.startsWith("old-")) {
    // The drift fork's ABANDONED pre-fork tail (child_kind 2) — the
    // demoted old chain; its row carries the drift note.
    const id = Number(nodeId.slice(4));
    const state = model.statesById.get(id);
    return state === undefined ? [] : [state];
  }
  if (model.mode === "legacy") {
    return bare === "execute" ? model.topLevelStates : [];
  }
  if (nodeId.startsWith("step-")) {
    const id = Number(nodeId.slice(5));
    const state = model.topLevelStates.find((s) => s.state_id === id);
    return state === undefined ? [] : [state];
  }
  return [];
}

/** The node list's position bounding box — computed once per build over
 *  the assembled canvas (chain shifts included). */
export function contentBoundsOf(nodes: ThreadGraphNode[]): ThreadGraphContentBounds {
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const n of nodes) {
    minX = Math.min(minX, n.x);
    maxX = Math.max(maxX, n.x);
    minY = Math.min(minY, n.y);
    maxY = Math.max(maxY, n.y);
  }
  return minX === Infinity
    ? { minX: 0, maxX: 0, minY: 0, maxY: 0 }
    : { minX, maxX, minY, maxY };
}
