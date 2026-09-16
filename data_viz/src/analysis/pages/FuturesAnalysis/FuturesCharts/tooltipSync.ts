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
 */
import type { MutableRefObject } from "react";
import type * as echarts from "echarts";

// Dispatch showTip on `to` at the pixel matching the hovered category
// index, or hideTip on leave/invalid index. `syncingRef` guards against
// re-entrant sync loops (showTip on `to` would otherwise re-trigger `to`'s
// own axis-pointer handlers).
export function syncTipTo(
  to: echarts.ECharts | null,
  params: unknown,
  syncingRef: MutableRefObject<boolean>,
): void {
  if (!to || syncingRef.current) return;
  const p = params as { currTrigger?: string; dataIndex?: number };
  if (p.currTrigger === "leave" || p.dataIndex == null || !Number.isFinite(p.dataIndex)) {
    to.dispatchAction({ type: "hideTip" });
    return;
  }
  const x = to.convertToPixel({ xAxisIndex: 0 }, p.dataIndex);
  if (!Number.isFinite(x)) return;
  syncingRef.current = true;
  try {
    to.dispatchAction({ type: "showTip", x, y: to.getHeight() / 2 });
  } finally {
    syncingRef.current = false;
  }
}
