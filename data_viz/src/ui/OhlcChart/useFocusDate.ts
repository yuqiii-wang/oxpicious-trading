/**
 * useFocusDate — the focusDateRequest handler for OhlcChart. Centers
 * the CURRENT dataZoom window span on the requested date (non-trading
 * dates snap forward to the next trading day) and dispatches the
 * dataZoom action — which moves BOTH dataZoom components (inside +
 * slider) and re-fires 'datazoom', so onVisibleRangeChange reporters
 * stay in sync. Guarded by the request's seq so a click is applied
 * exactly once.
 */
import { useEffect, useRef } from "react";
import type { MutableRefObject } from "react";
import type { ECharts } from "echarts";

export function useFocusDateJump(
  chartRef: MutableRefObject<ECharts | null>,
  chartDates: string[],
  focusDateRequest?: { date: string; seq: number } | null,
): void {
  const lastFocusSeqRef = useRef(-1);
  useEffect(() => {
    const req = focusDateRequest;
    if (!req || req.seq === lastFocusSeqRef.current) return;
    const chart = chartRef.current;
    const n = chartDates.length;
    if (!chart || n === 0) return;
    let idx = chartDates.indexOf(req.date);
    if (idx < 0) {
      idx = chartDates.findIndex((d) => d >= req.date);
      if (idx < 0) idx = n - 1;
    }
    try {
      const dz = (
        chart.getOption() as
          | { dataZoom?: Array<{ start?: number; end?: number }> }
          | undefined
      )?.dataZoom?.[0];
      const span =
        typeof dz?.start === "number" && typeof dz?.end === "number"
          ? Math.max(dz.end - dz.start, 1)
          : 100;
      const pos = (idx / Math.max(n - 1, 1)) * 100;
      let start = pos - span / 2;
      let end = pos + span / 2;
      if (start < 0) {
        start = 0;
        end = span;
      }
      if (end > 100) {
        end = 100;
        start = 100 - span;
      }
      chart.dispatchAction({ type: "dataZoom", start, end });
      lastFocusSeqRef.current = req.seq;
    } catch {
      // Instance disposed mid-dispatch (HMR / unmount) — skip this request.
    }
    // chartRef is the caller's useRef — identity-stable, like a local ref.
  }, [focusDateRequest, chartDates, chartRef]);
}
