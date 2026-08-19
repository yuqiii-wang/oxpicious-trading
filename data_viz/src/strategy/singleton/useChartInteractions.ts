/**
 * Custom hook for managing chart interactions (click, hover, ready).
 *
 * Handles:
 *  - Clicking forecast curves to select a scenario
 *  - Canvas clicks to find closest forecast curve
 *  - Mousemove tracking for hovered scenario highlighting
 */
import { useCallback, useEffect, useRef } from "react";
import type { StrategyBacktestResponse, StrategyForecast1mResponse } from "@shared/types";

const FC_ORDER = [
  "mir_255d_std_scale", "flip_255d_std_scale",
  "mir_255d_std_half_scale", "flip_255d_std_half_scale",
  "mir_20d_std_scale", "flip_20d_std_scale",
  "mir_255d_max_std_scale", "flip_255d_max_std_scale",
  "rand", "rand_opp",
] as const;

export interface ChartInteractionState {
  chartInstanceRef: React.MutableRefObject<import("echarts").ECharts | null>;
  handleChartClick: (params: unknown) => void;
  handleCanvasClick: (dataIdx: number, pixel?: [number, number]) => void;
  handleChartReady: (instance: import("echarts").ECharts) => void;
}

export function useChartInteractions(
  displayBacktest: StrategyBacktestResponse | null,
  forecast: StrategyForecast1mResponse | null,
  backtestRef: React.MutableRefObject<StrategyBacktestResponse | null>,
  forecastRef: React.MutableRefObject<StrategyForecast1mResponse | null>,
  hoveredScenarioRef: React.MutableRefObject<string | null>,
  onScenarioChange: (scenario: string) => void,
): ChartInteractionState {
  const chartInstanceRef = useRef<import("echarts").ECharts | null>(null);
  const eventCleanupRef = useRef<(() => void) | null>(null);

  const handleChartClick = useCallback((params: unknown) => {
    const p = params as { seriesName?: string };
    const sn = p?.seriesName;
    if (!sn || !sn.startsWith("FC ")) return;
    const sc = sn.slice(3);
    if (sc === "mean") return;
    onScenarioChange(sc);
  }, [onScenarioChange]);

  const handleCanvasClick = useCallback((dataIdx: number, pixel?: [number, number]) => {
    if (!displayBacktest || !forecast || forecast.rows.length === 0) return;
    if (!pixel) return;
    const chart = chartInstanceRef.current;
    if (!chart) return;

    const ohlcLen = displayBacktest.ohlc.length;
    if (dataIdx < ohlcLen) return;

    const fcDay = dataIdx - ohlcLen;
    if (fcDay < 0 || fcDay >= 20) return;

    const clickedValue = chart.convertFromPixel({ yAxisIndex: 0 }, pixel[1]);
    if (!Number.isFinite(clickedValue)) return;

    const fcStats = forecast.stats;
    const fcConv = fcStats?.first_buy_fill_price && fcStats.first_buy_fill_price > 0
      ? fcStats.anchor_close / fcStats.first_buy_fill_price : null;
    if (fcConv == null) return;

    const fcByScenario = new Map<string, typeof forecast.rows>();
    for (const r of forecast.rows) {
      const arr = fcByScenario.get(r.scenario) ?? [];
      arr.push(r);
      fcByScenario.set(r.scenario, arr);
    }

    let bestScenario: string | null = null;
    let bestDist = Infinity;
    for (const sc of FC_ORDER) {
      const arr = fcByScenario.get(sc);
      if (!arr || arr.length === 0) continue;
      const row = arr.find((r) => r.forecast_day === fcDay + 1);
      if (!row) continue;
      const plotVal = row.close_price * fcConv;
      const dist = Math.abs(plotVal - clickedValue);
      if (dist < bestDist) {
        bestDist = dist;
        bestScenario = sc;
      }
    }

    if (bestScenario) {
      onScenarioChange(bestScenario);
    }
  }, [displayBacktest, forecast, onScenarioChange]);

  const handleChartReady = useCallback((instance: import("echarts").ECharts) => {
    eventCleanupRef.current?.();
    eventCleanupRef.current = null;

    chartInstanceRef.current = instance;
    const dom = instance.getDom();
    if (!dom) return;

    const mouseMoveHandler = (e: MouseEvent) => {
      const rect = dom.getBoundingClientRect();
      const px = e.clientX - rect.left;
      const py = e.clientY - rect.top;

      const bt = backtestRef.current;
      const fc = forecastRef.current;
      if (!bt || !fc || fc.rows.length === 0) {
        hoveredScenarioRef.current = null;
        return;
      }
      if (!instance.containPixel("grid", [px, py])) {
        hoveredScenarioRef.current = null;
        return;
      }
      const idx = instance.convertFromPixel({ xAxisIndex: 0 }, px);
      const dataIdx = Math.round(idx);
      const ohlcLen = bt.ohlc.length;
      if (dataIdx < ohlcLen || dataIdx >= ohlcLen + 20) {
        hoveredScenarioRef.current = null;
        return;
      }
      const fcDay = dataIdx - ohlcLen;
      const mouseValue = instance.convertFromPixel({ yAxisIndex: 0 }, py);
      if (!Number.isFinite(mouseValue)) {
        hoveredScenarioRef.current = null;
        return;
      }
      const fcStats = fc.stats;
      const fcConv = fcStats?.first_buy_fill_price && fcStats.first_buy_fill_price > 0
        ? fcStats.anchor_close / fcStats.first_buy_fill_price : null;
      if (fcConv == null) {
        hoveredScenarioRef.current = null;
        return;
      }
      const fcByScenario = new Map<string, typeof fc.rows>();
      for (const r of fc.rows) {
        const arr = fcByScenario.get(r.scenario) ?? [];
        arr.push(r);
        fcByScenario.set(r.scenario, arr);
      }
      let bestScenario: string | null = null;
      let bestDist = Infinity;
      for (const sc of FC_ORDER) {
        const arr = fcByScenario.get(sc);
        if (!arr || arr.length === 0) continue;
        const row = arr.find((r) => r.forecast_day === fcDay + 1);
        if (!row) continue;
        const plotVal = row.close_price * fcConv;
        const dist = Math.abs(plotVal - mouseValue);
        if (dist < bestDist) {
          bestDist = dist;
          bestScenario = sc;
        }
      }
      hoveredScenarioRef.current = bestScenario;
    };

    const mouseLeaveHandler = () => {
      hoveredScenarioRef.current = null;
    };

    dom.addEventListener("mousemove", mouseMoveHandler, { capture: true });
    dom.addEventListener("mouseleave", mouseLeaveHandler, { capture: true });
    eventCleanupRef.current = () => {
      dom.removeEventListener("mousemove", mouseMoveHandler, { capture: true });
      dom.removeEventListener("mouseleave", mouseLeaveHandler, { capture: true });
    };
  }, [backtestRef, forecastRef, hoveredScenarioRef]);

  useEffect(() => {
    return () => { eventCleanupRef.current?.(); };
  }, []);

  return {
    chartInstanceRef,
    handleChartClick,
    handleCanvasClick,
    handleChartReady,
  };
}
