/**
 * ECharts React wrapper with theme-aware styling.
 *
 * - Accepts an ECharts `option` prop and re-renders when it changes.
 * - Auto-resizes the chart on container resize.
 * - Exposes the chart instance via ref for cross-chart sync (used by the
 *   debt-baseline 4-panel view to share x-axis crosshair).
 * - Supports an `onEvents` prop for attaching interaction handlers (click,
 *   hover, etc.) that stay in sync with the latest option data.
 */
import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import type { EChartsOption } from "echarts";
import { useStore } from "@/store/filters";

interface EChartProps {
  option: EChartsOption;
  height?: number | string;
  /** Minimum container height (default 200). Set to a smaller value for
   *  narrow strip charts (e.g. timeline markers). */
  minHeight?: number;
  /** Optional group name for cross-chart tooltip sync. */
  group?: string;
  onReady?: (instance: echarts.ECharts) => void;
  /** Event handlers keyed by ECharts event name (e.g. { click: fn }). Handlers
   *  are re-bound when this object identity changes, so pass a stable
   *  reference (e.g. via useCallback) to avoid unnecessary re-binds. */
  onEvents?: Record<string, (params: unknown) => void>;
  /** Optional canvas-level click handler. Fires for ANY click inside the
   *  chart's plot grid (not just on data points / line segments). The
   *  callback receives the x-axis category index of the clicked position.
   *  Useful for "click any date to select it" interactions on line/area
   *  charts where showSymbol is false. */
  onCanvasClick?: (dataIndex: number) => void;
}

export default function EChart({ option, height = 320, minHeight = 200, group, onReady, onEvents, onCanvasClick }: EChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const themeMode = useStore((s) => s.themeMode);

  // Store the latest onCanvasClick in a ref so the zr handler (bound once on
  // mount) always calls the freshest closure without needing to re-bind.
  const onCanvasClickRef = useRef(onCanvasClick);
  useEffect(() => {
    onCanvasClickRef.current = onCanvasClick;
  }, [onCanvasClick]);

  // Init chart on mount
  useEffect(() => {
    if (!containerRef.current) return;
    const chart = echarts.init(containerRef.current, undefined, {
      renderer: "canvas",
    });
    chartRef.current = chart;
    if (group) chart.group = group;
    onReady?.(chart);
    const resizeObserver = new ResizeObserver(() => {
      chart.resize();
    });
    resizeObserver.observe(containerRef.current);
    return () => {
      resizeObserver.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Update option when it changes.
  // notMerge: true — REPLACE the entire option instead of merging. This is
  // critical for toggles that ADD/REMOVE series (e.g. benchmark / mean-only
  // on IndustrySentiments): with notMerge:false, ECharts keeps stale series
  // data from the previous option, so removed lines never disappear.
  useEffect(() => {
    if (!chartRef.current) return;
    chartRef.current.setOption(option, { notMerge: true, lazyUpdate: true });
  }, [option]);

  // Bind / re-bind event handlers when onEvents identity changes
  useEffect(() => {
    if (!chartRef.current || !onEvents) return;
    const chart = chartRef.current;
    for (const [eventName, handler] of Object.entries(onEvents)) {
      chart.on(eventName, handler);
    }
    return () => {
      for (const eventName of Object.keys(onEvents)) {
        chart.off(eventName);
      }
    };
  }, [onEvents]);

  // Canvas-level click handler — fires for clicks anywhere inside the plot
  // grid area. Converts the click's pixel x-coordinate to the nearest x-axis
  // category index and forwards it to onCanvasClick. Bound once on mount;
  // the latest callback is read from a ref so identity changes don't
  // require re-binding (and don't create a dispose/re-init cycle).
  useEffect(() => {
    if (!chartRef.current) return;
    const chart = chartRef.current;
    const zr = chart.getZr();
    const handler = (params: { offsetX?: number; offsetY?: number }) => {
      // Only fire when a callback is registered.
      const cb = onCanvasClickRef.current;
      if (!cb) return;
      const x = params.offsetX;
      const y = params.offsetY;
      if (x == null || y == null) return;
      // Ignore clicks outside the plot grid (axis labels, legend, margins).
      if (!chart.containPixel("grid", [x, y])) return;
      // convertFromPixel on a category axis returns a float index.
      const idx = chart.convertFromPixel({ xAxisIndex: 0 }, x);
      const dataIdx = Math.round(idx);
      if (dataIdx < 0) return;
      cb(dataIdx);
    };
    zr.on("click", handler);
    return () => {
      zr.off("click", handler);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Re-render on theme change (text/grid colors depend on mode)
  useEffect(() => {
    if (!chartRef.current) return;
    chartRef.current.resize();
  }, [themeMode]);

  return (
    <div
      ref={containerRef}
      style={{
        width: "100%",
        height: typeof height === "number" ? `${height}px` : height,
        minHeight,
      }}
    />
  );
}

/**
 * Wire up ECharts' built-in cross-chart tooltip sync across all charts in
 * the same group. Call this once at app startup.
 */
export function connectChartsByGroup(group: string) {
  echarts.connect(group);
}
