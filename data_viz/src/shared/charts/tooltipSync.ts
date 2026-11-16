/**
 * Cross-chart tooltip sync (manual showTip/hideTip).
 *
 * Forward the hovered category index to another chart as a pixel point, so
 * its axis tooltip + pointer render at the same date regardless of
 * per-series nulls. echarts.connect is NOT used for this: connect
 * propagates the source chart's seriesIndex+dataIndex, and the receiving
 * chart resolves the point from its own series at that index; when that
 * series has a null value at the hovered date the resolved point is NaN
 * and the tooltip silently goes empty/stale. Manual dispatch with a pixel
 * computed from the shared category index is deterministic.
 *
 * Lives in shared/charts since MaSpread (main plot ↔ MA-Chain Alignment)
 * uses the same mechanism alongside an echarts.connect group: connect
 * keeps the shared dataZoom window, this helper keeps the hover crosshair.
 */
import type { MutableRefObject } from "react";
import type * as echarts from "echarts";

// Dispatch showTip on `to` at the pixel matching the hovered category
// index, or hideTip on leave/invalid index. `syncingRef` guards against
// re-entrant sync loops (showTip on `to` would otherwise re-trigger `to`'s
// own axis-pointer handlers). The dispatched showTip carries
// `escapeConnect: true` — if `to` sits in an echarts.connect group the
// broadcast would otherwise echo this pixel-coords tip (valid only in
// `to`'s own space) back to the source chart; escapeConnect marks the
// action as local-only.
//
// ECharts emits updateAxisPointer in TWO payload shapes: with
// `dataIndex` (axis snapped onto a series point) and with only `x`/`y`
// pixels (between snap points). Pass the SOURCE chart as `from` and the
// pixel form is resolved through its own category axis (containPixel
// guard — a point off the source grid means a real leave, hideTip).
export function syncTipTo(
  to: echarts.ECharts | null,
  params: unknown,
  syncingRef: MutableRefObject<boolean>,
  from?: echarts.ECharts | null,
): void {
  if (!to || syncingRef.current) return;
  const p = params as { currTrigger?: string; dataIndex?: number; x?: number; y?: number };
  if (p.currTrigger === "leave") {
    to.dispatchAction({ type: "hideTip", escapeConnect: true });
    return;
  }
  let dataIndex = p.dataIndex;
  if ((dataIndex == null || !Number.isFinite(dataIndex)) && from != null && p.x != null && p.y != null) {
    if (!from.containPixel("grid", [p.x, p.y])) {
      to.dispatchAction({ type: "hideTip", escapeConnect: true });
      return;
    }
    const idx = from.convertFromPixel({ xAxisIndex: 0 }, p.x);
    dataIndex = Number.isFinite(idx) ? Math.round(idx) : NaN;
  }
  if (dataIndex == null || !Number.isFinite(dataIndex) || dataIndex < 0) {
    to.dispatchAction({ type: "hideTip", escapeConnect: true });
    return;
  }
  const x = to.convertToPixel({ xAxisIndex: 0 }, dataIndex);
  if (!Number.isFinite(x)) return;
  syncingRef.current = true;
  try {
    to.dispatchAction({
      type: "showTip",
      escapeConnect: true,
      x,
      y: to.getHeight() / 2,
    });
  } finally {
    syncingRef.current = false;
  }
}
