/**
 * Custom hook for managing chart interactions (click, hover, ready).
 */
import { useCallback, useRef } from "react";
import type { StrategyBacktestResponse } from "@shared/types";

export interface ChartInteractionState {
  chartInstanceRef: React.MutableRefObject<import("echarts").ECharts | null>;
  handleChartClick: (params: unknown) => void;
  handleCanvasClick: (dataIdx: number, pixel?: [number, number]) => void;
  handleChartReady: (instance: import("echarts").ECharts) => void;
}

export function useChartInteractions(
  displayBacktest: StrategyBacktestResponse | null,
  backtestRef: React.MutableRefObject<StrategyBacktestResponse | null>,
): ChartInteractionState {
  const chartInstanceRef = useRef<import("echarts").ECharts | null>(null);

  const handleChartClick = useCallback((_params: unknown) => {
    // Chart click handling placeholder
  }, []);

  const handleCanvasClick = useCallback((_dataIdx: number, _pixel?: [number, number]) => {
    // Canvas click handling placeholder
  }, []);

  const handleChartReady = useCallback((instance: import("echarts").ECharts) => {
    chartInstanceRef.current = instance;
  }, []);

  return {
    chartInstanceRef,
    handleChartClick,
    handleCanvasClick,
    handleChartReady,
  };
}
