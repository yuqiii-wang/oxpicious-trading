/**
 * useDataZoomSync — synchronize dataZoom state across multiple ECharts
 * instances so that dragging the slider on one chart updates all others.
 *
 * ECharts' built-in `connect(group)` syncs axisPointer/tooltip but NOT the
 * dataZoom slider. This hook lifts `{start, end}` into React state and
 * wires up the `dataZoom` event on each chart to keep them in lockstep.
 */
import { useCallback, useRef, useState } from "react";
import { commonDataZoom } from "@/theme/chart-palette";
import type { EChartsOption } from "echarts";

export interface DataZoomState {
  start: number;
  end: number;
}

export function useDataZoomSync(initial: DataZoomState = { start: 0, end: 100 }) {
  const [zoom, setZoom] = useState<DataZoomState>(initial);
  // Ref to the latest zoom value so event handlers always read fresh state
  // without re-binding.
  const zoomRef = useRef(zoom);
  zoomRef.current = zoom;

  // Track whether a zoom update originated from our own handler (to avoid
  // re-triggering the same chart's listeners in a loop).
  const updatingRef = useRef(false);

  /** Build a dataZoom option block that reflects current shared state. */
  const buildDataZoom = useCallback(
    (overrides: Partial<import("echarts").DataZoomComponentOption> = {}): EChartsOption["dataZoom"] => {
      return commonDataZoom(overrides, zoom.start, zoom.end);
    },
    [zoom],
  );

  /** Build a dataZoom option with only the "inside" zoom (no visible slider bar). */
  const buildInsideDataZoom = useCallback(
    (overrides: Partial<import("echarts").DataZoomComponentOption> = {}): EChartsOption["dataZoom"] => {
      const full = commonDataZoom(overrides, zoom.start, zoom.end);
      return [full[0]];
    },
    [zoom],
  );

  /** Event handler for the `dataZoom` event fired by any connected chart. */
  const handleDataZoom = useCallback(
    (params: unknown) => {
      if (updatingRef.current) return;
      const p = params as { start?: number; end?: number; batch?: Array<{ start?: number; end?: number }> };
      // dataZoom event: params.batch[0].start / .end OR direct .start / .end
      const start = p.batch?.[0]?.start ?? p.start;
      const end = p.batch?.[0]?.end ?? p.end;
      if (start == null || end == null) return;
      if (start === zoomRef.current.start && end === zoomRef.current.end) return;
      updatingRef.current = true;
      setZoom({ start, end });
      // Allow React to commit the state before clearing the guard
      setTimeout(() => {
        updatingRef.current = false;
      }, 0);
    },
    [],
  );

  /** Programmatic setter (e.g. for resetting the zoom). */
  const setDataZoom = useCallback((next: DataZoomState) => {
    updatingRef.current = true;
    setZoom(next);
    setTimeout(() => {
      updatingRef.current = false;
    }, 0);
  }, []);

  return { zoom, buildDataZoom, buildInsideDataZoom, handleDataZoom, setDataZoom };
}
