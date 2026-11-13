/** The view budget — how the canvas sizes itself so the main path
 *  renders properly spaced. */
import type { ThreadGraphModel } from "./types";

/** ---- THE VIEW BUDGET — how the canvas sizes itself so the main path
 *  renders properly spaced. ECharts fits the positions bbox into the
 *  canvas uniformly (scale = min of the two ratios) at series zoom 1;
 *  with nodeScaleRatio 0 the circles stay fixed-px at any zoom. The
 *  canvas height TRACKS the content (graphViewHeight — contentHeight ×
 *  FIT_FLOOR), so the vertical fit ratio stays at the floor as subtrees
 *  expand and collapse, and the spawn subtrees fold by default to keep
 *  the folded graph shallow; whatever still lands below FIT_FLOOR
 *  (width-bound chains, a dragged-shorter viewport) the view's zoom
 *  raises — never sinks — around the frozen center. Roam pans the
 *  remainder; the draggable boundary trades room back. */

/** The vertical fit floor — the minimum EFFECTIVE scale the view
 *  guarantees (fit × zoom). At 0.55 the measured abstract spacings
 *  clear every fixed-px footprint with air: row pitch 93px screen vs a
 *  49px spawn half-block, column pitch ~90px vs 84px step name blocks.
 *  A chain wider than the canvas at this scale pans horizontally. */
export const FIT_FLOOR = 0.55;

const MIN_VIEW_HEIGHT = 380;
/** The drag ceiling of the graph viewport — the draggable boundary
 *  caps here. */
export const MAX_VIEW_HEIGHT = 2000;

/** The drag floor of the graph viewport — the boundary never shrinks
 *  the canvas below this (roam zoom reads what the strip hides). */
export const MIN_VIEWPORT_HEIGHT = 280;

/** The canvas height for a built model: content height × FIT_FLOOR,
 *  clamped. TRACKS the content — expanding a subtree grows the canvas
 *  with it, so the vertical fit ratio stays at the floor and the rows
 *  keep their spacing (the window clamp of earlier iterations is gone:
 *  a squeezed canvas was what crowded the expanded spurs; the folded
 *  default stays shallow enough to fit the window anyway). */
export function graphViewHeight(model: ThreadGraphModel): number {
  const h = model.contentBounds.maxY - model.contentBounds.minY;
  return Math.round(
    Math.min(Math.max(h * FIT_FLOOR, MIN_VIEW_HEIGHT), MAX_VIEW_HEIGHT),
  );
}

/** The ECharts auto-fit scale estimate for a canvas of width×height px:
 *  the graph view's layout rect defaults to a CENTERED 80% rect
 *  (GraphSeries defaultOption + getLayoutRect's both-width-and-height-
 *  unspecified branch, aspect-matched to the content bbox), so the
 *  native fit — the scale the drawn positions actually get at zoom 1 —
 *  is 0.8 × min(cw/W, ch/H). The estimate IS that native scale (the
 *  view raises the zoom so the effective scale never lands below
 *  FIT_FLOOR — no compression of the main path; the cropped remainder
 *  pans). Call with a not-yet-measured width as Infinity → zoom stays
 *  1. */
export const VIEW_RECT_RATIO = 0.8;

export function estimateFitScale(
  model: ThreadGraphModel,
  widthPx: number,
  heightPx: number,
): number {
  const w = Math.max(model.contentBounds.maxX - model.contentBounds.minX, 1);
  const h = Math.max(model.contentBounds.maxY - model.contentBounds.minY, 1);
  return VIEW_RECT_RATIO * Math.min(widthPx / w, heightPx / h);
}
