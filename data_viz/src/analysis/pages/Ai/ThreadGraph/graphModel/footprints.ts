/** The layout's measured constants — the footprint registry plus the
 *  inter-node gaps. Private to the former single graphModel.ts; shared
 *  across the builder files (pending model, segment builder, chain
 *  builder) now that they are separate modules. */
import type { ThreadGraphNodeKind } from "./types";

/** ---- THE FOOTPRINT REGISTRY — the layout is measured, not stepped.
 *  ECharts scales node POSITIONS to fit the canvas but renders symbols
 *  and labels at fixed px (and with nodeScaleRatio 0 the circles stay
 *  fixed-px under roam zoom too), so screen spacing = abstract spacing
 *  × fit scale while every circle and label block keeps its px size.
 *  The layout therefore measures the SMALLEST nodes' footprints first
 *  (bottom-up: leaf spawns → subtrees → wave members → lane parents)
 *  and derives every gap from these tables — no flat step constants.
 *  The option builder renders from the same tables, so the reserved
 *  room can never drift from the drawn sizes. */
export const KIND_SYMBOL_SIZE: Record<ThreadGraphNodeKind, number> = {
  endpoint: 14,
  runner: 46,
  output: 40,
  step: 44,
  spawn: 30,
  fork: 30,
  abandoned: 26,
  resume: 26,
  supervisor: 44,
  caption: 0,
  bend: 0,
};

/** The fixed-px label block each kind hangs below (or, when labelTop,
 *  above) its circle — width × total height at the option builder's
 *  label sizing (the builder's label `width`s import from here so the
 *  block can't drift). */
export const KIND_LABEL_PX: Record<
  ThreadGraphNodeKind,
  { w: number; h: number }
> = {
  endpoint: { w: 0, h: 0 },
  runner: { w: 64, h: 34 },
  output: { w: 64, h: 34 },
  step: { w: 84, h: 15 },
  spawn: { w: 120, h: 13 },
  fork: { w: 120, h: 13 },
  abandoned: { w: 120, h: 13 },
  resume: { w: 120, h: 13 },
  supervisor: { w: 64, h: 34 },
  caption: { w: 64, h: 17 },
  bend: { w: 0, h: 0 },
};

/** A node's screen half-extent — the larger of its circle radius and
 *  its label block's half (labels hang centered on the circle's x).
 *  The unit every horizontal gap is measured in. */
export const halfFootprint = (kind: ThreadGraphNodeKind): number =>
  Math.max(KIND_SYMBOL_SIZE[kind] / 2, KIND_LABEL_PX[kind].w / 2);

/** Base gap between two adjacent columns' footprints — sized so that
 *  at the fit floor (FIT_FLOOR) two same-row label blocks (84px step
 *  names are the widest) keep visible air: 164 abstract × 0.55 ≈ 90px
 *  screen between centers vs 84px block widths. */
export const GAP_COL = 80;

/** Gap between a parent's own footprint edge and its spawn column
 *  center (the parent and its spurs are different rows, so this only
 *  keeps the circles and the spawn diagonal airy). */
export const GAP_SPAWN = 30;

/** The spawn column's center x measured from its parent node: the
 *  parent's half-footprint, the spawn gap, then the spawn's own
 *  half-footprint — the 120px goal label dominates the column's width,
 *  so a wider spawn label pushes the column (and the next lane column
 *  with it) out by itself. */
export const spawnColumnOffset = (parentKind: ThreadGraphNodeKind): number =>
  halfFootprint(parentKind) + GAP_SPAWN + halfFootprint("spawn");

/** A NESTED spawn column's center x measured from its parent SPAWN — the
 *  SLIGHT right-indent one depth level adds (the spawn label's half
 *  plus the spawn gap): multi-level spurs stair-step right of their
 *  parents instead of stacking in one shared column, so a grandchild
 *  reads as its own branch beside — not under — its parent's circle.
 *  The child's goal label starts past the parent's spawn trunk (the
 *  label half clears it by GAP_SPAWN) while the circles — half the
 *  label's width — keep two circle radii of air. */
export const SPAWN_TO_SPAWN_OFFSET = halfFootprint("spawn") + GAP_SPAWN;

/** One vertical row's spacing — wave members stack in their shared
 *  column, spawn spurs hang in their spawn column; the spawn-row
 *  arithmetic reads a parent's row back as y / Y_STEP, so both layouts
 *  must share the unit. At the fit floor a row pitches 170 × 0.55 ≈
 *  93px on screen — a 45px step half-block and a 49px spawn half-block
 *  both clear the next row's circle with room to spare. */
export const Y_STEP = 170;

/** The inter-graph gap of the chain layout — one handoff edge's length
 *  plus breathing room between the previous segment's terminal and the
 *  next segment's entry (a step wider than the lane itself, so the two
 *  graphs read as two graphs joined by the handoff). */
export const X_CHAIN_GAP = 210;
