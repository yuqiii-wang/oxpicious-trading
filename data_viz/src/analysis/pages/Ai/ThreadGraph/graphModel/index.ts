/** Barrel of the graphModel split — the public surface is identical to
 *  the former single graphModel.ts (importers keep using `./graphModel`).
 *  The architecture contract doc lives on ./types; the segment builder
 *  (buildSegmentModel) and its metrics helpers stay internal modules. */
export type {
  ThreadGraphContentBounds,
  ThreadGraphEdge,
  ThreadGraphModel,
  ThreadGraphNode,
  ThreadGraphNodeKind,
  ThreadNodeStatus,
  ThreadSegmentView,
} from "./types";
export { bareNodeId, isOutputNodeId, segmentIndexOfNodeId } from "./nodeIds";
export {
  SPAWN_TO_SPAWN_OFFSET,
  GAP_COL,
  GAP_SPAWN,
  KIND_LABEL_PX,
  KIND_SYMBOL_SIZE,
  X_CHAIN_GAP,
  Y_STEP,
  spawnColumnOffset,
  halfFootprint,
} from "./footprints";
export { contentBoundsOf, itemsUnderNodeId } from "./items";
export { buildPendingThreadGraphModel } from "./pendingModel";
export { buildThreadGraphModel } from "./buildThreadGraphModel";
export {
  FIT_FLOOR,
  MAX_VIEW_HEIGHT,
  MIN_VIEWPORT_HEIGHT,
  VIEW_RECT_RATIO,
  estimateFitScale,
  graphViewHeight,
} from "./viewBudget";
