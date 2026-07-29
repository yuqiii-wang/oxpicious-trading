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
}

export default function EChart({ option, height = 320, minHeight = 200, group, onReady, onEvents }: EChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const themeMode = useStore((s) => s.themeMode);

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

  // Update option when it changes
  useEffect(() => {
    if (!chartRef.current) return;
    chartRef.current.setOption(option, { notMerge: false, lazyUpdate: true });
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
