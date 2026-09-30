/**
 * useVisibleRange — the onVisibleRangeChange reporter for OhlcChart.
 * Maps the dataZoom's live start/end % back onto the category-axis dates
 * and reports the visible range. Fired on ECharts 'datazoom' events AND
 * on every rows rebuild (the option re-anchor). The callback lives in a
 * ref so the chart.on binding (made once) always calls the freshest
 * closure without re-binding on parent re-renders.
 */
import { useCallback, useEffect, useRef } from "react";
import type { MutableRefObject } from "react";
import type { ECharts } from "echarts";

export type VisibleRange = { start: string; end: string } | null;

export function useVisibleRangeReporter(
  chartRef: MutableRefObject<ECharts | null>,
  chartDates: string[],
  onVisibleRangeChange?: (range: VisibleRange) => void,
): void {
  const visibleRangeCbRef = useRef(onVisibleRangeChange);
  useEffect(() => {
    visibleRangeCbRef.current = onVisibleRangeChange;
  }, [onVisibleRangeChange]);

  const emitVisibleRange = useCallback(() => {
    const cb = visibleRangeCbRef.current;
    if (!cb) return;
    const chart = chartRef.current;
    const n = chartDates.length;
    if (!chart || n === 0) {
      cb(null);
      return;
    }
    try {
      const dz = (
        chart.getOption() as
          | { dataZoom?: Array<{ start?: number; end?: number }> }
          | undefined
      )?.dataZoom?.[0];
      const s = typeof dz?.start === "number" ? dz.start : 0;
      const e = typeof dz?.end === "number" ? dz.end : 100;
      const startIdx = Math.max(0, Math.min(n - 1, Math.floor((s / 100) * (n - 1))));
      const endIdx = Math.max(0, Math.min(n - 1, Math.ceil((e / 100) * (n - 1))));
      cb({ start: chartDates[startIdx], end: chartDates[endIdx] });
    } catch {
      // Instance disposed mid-read (HMR / unmount) — skip this report.
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chartDates]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const handler = () => emitVisibleRange();
    chart.on("datazoom", handler);
    return () => {
      chart.off("datazoom", handler);
    };
    // chartRef is the caller's useRef — identity-stable, like a local ref.
  }, [emitVisibleRange, chartRef]);

  // Initial report + re-report after rebuilds (the option effect re-anchors
  // the window; 'datazoom' alone never fires for those).
  useEffect(() => {
    emitVisibleRange();
  }, [emitVisibleRange]);
}
