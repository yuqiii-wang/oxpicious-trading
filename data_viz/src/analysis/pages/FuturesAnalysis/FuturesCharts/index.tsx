/**
 * FuturesCharts — the 3-plot futures analysis view.
 *
 * Layout (top → bottom):
 *   1. Futures price curves (identical to Data Viz) with gap_price_vs_underlying
 *      added to the tooltip for each contract.
 *   2. Correlation (corr_price_vs_underlying) — one line per contract
 *      (active + matured), y-axis fixed at -1 to 1. Built by
 *      buildCorrelationChartOption.
 *   3. Basis convergence (contrarian) — signed gap between futures and spot
 *      (bps) per contract with an emphasized zero line: as a contract ages
 *      toward expiry its deviation collapses into zero. Dots mark each
 *      matured contract's expiry gap (history mode) and each active
 *      contract's yesterday gap. Built by buildBasisConvergenceChartOption.
 *
 * All plots share:
 *   - A synced time slider (dataZoom) via shared zoomRange state.
 *   - The exact same per-contract color scheme (blue active / grey matured
 *     gradients) via computeFuturesContractStyles().
 *   - Cross-chart hover: hovering any plot shows the tooltip + axis
 *     pointer on all of them (see tooltipSync.ts for the manual
 *     showTip/hideTip mechanism — NOT echarts.connect).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Stack, Typography } from "@mui/material";
import type * as echarts from "echarts";
import type { EChartsOption } from "echarts";
import EChart from "@/components/EChart";
import type { FuturesCombinedResponse } from "@shared/types";
import {
  buildFuturesChartOption,
  buildBasisConvergenceChartOption,
  type FuturesChartExtra,
} from "@/dataviz/features/futures/chartOption";
import { useFuturesExt } from "./useFuturesExt";
import { useExpiryDots } from "./expiryDots";
import { syncTipTo } from "./tooltipSync";
import { buildCorrelationChartOption } from "./buildCorrOption";
import { ChartSection } from "./ChartSection";

interface FuturesChartsProps {
  product: string;
  combinedData: FuturesCombinedResponse;
  viewMode: "future" | "history";
}

export function FuturesCharts({ product, combinedData, viewMode }: FuturesChartsProps) {
  const { extData, loadingExt, errorExt, gapMap, corrMap } = useFuturesExt(product);

  // Chart instances for manual cross-chart tooltip sync
  const priceChartRef = useRef<echarts.ECharts | null>(null);
  const corrChartRef = useRef<echarts.ECharts | null>(null);
  const convChartRef = useRef<echarts.ECharts | null>(null);
  const syncingRef = useRef(false);

  // Shared zoom range (percentages 0-100) for both plots' dataZoom sliders
  const [zoomRange, setZoomRange] = useState<{ start: number; end: number } | null>(null);

  // Expiry dots (history mode, hover-triggered) — ref store + targeted push
  // to the price chart, kept out of React state to avoid tooltip flicker.
  const { expiryDotsRef, applyExpiryDots, computeExpiryDots } = useExpiryDots(
    product,
    combinedData,
    viewMode,
    priceChartRef,
  );

  // Default zoom: show last 120 days
  const defaultZoom = useMemo(() => {
    if (!combinedData) return { start: 0, end: 100 };
    const total = combinedData.dates.length;
    const start = Math.max(0, 100 - (120 / Math.max(total, 1)) * 100);
    return { start, end: 100 };
  }, [combinedData]);

  const currentZoom = zoomRange ?? defaultZoom;

  // Reset zoom when product changes
  useEffect(() => {
    setZoomRange(null);
  }, [product]);

  // Handle dataZoom events from either chart — syncs both plots
  const handleZoom = useCallback((params: unknown) => {
    if (!params || typeof params !== "object") return;
    const ev = params as { batch?: Array<{ start?: number; end?: number }>; start?: number; end?: number };
    const batchItem = ev.batch?.[0] ?? ev;
    const start = typeof batchItem.start === "number" ? batchItem.start : currentZoom.start;
    const end = typeof batchItem.end === "number" ? batchItem.end : currentZoom.end;
    if (Math.abs(start - currentZoom.start) < 0.01 && Math.abs(end - currentZoom.end) < 0.01) return;
    setZoomRange({ start, end });
  }, [currentZoom.start, currentZoom.end]);

  // ---- Cross-chart tooltip sync (manual showTip/hideTip) ------------------
  // Forward the hovered category index to the other charts as a pixel point
  // (see tooltipSync.ts for why echarts.connect is not used).
  const handlePriceTipSync = useCallback(
    (params: unknown) => {
      syncTipTo(corrChartRef.current, params, syncingRef);
      syncTipTo(convChartRef.current, params, syncingRef);
      // Compute expiry dots for history mode
      if (viewMode === "history") {
        const p = params as { dataIndex?: number; currTrigger?: string };
        if (p.dataIndex != null && Number.isFinite(p.dataIndex)) {
          applyExpiryDots(computeExpiryDots(p.dataIndex));
        } else if (p.currTrigger === "leave") {
          applyExpiryDots([]);
        }
      }
    },
    [viewMode, computeExpiryDots, applyExpiryDots],
  );
  const handleCorrTipSync = useCallback(
    (params: unknown) => {
      syncTipTo(priceChartRef.current, params, syncingRef);
      syncTipTo(convChartRef.current, params, syncingRef);
    },
    [],
  );
  const handleConvTipSync = useCallback(
    (params: unknown) => {
      syncTipTo(priceChartRef.current, params, syncingRef);
      syncTipTo(corrChartRef.current, params, syncingRef);
    },
    [],
  );

  // Leaving any chart hides the tooltip + axis pointer on ALL charts
  const hideAllTips = useCallback(() => {
    priceChartRef.current?.dispatchAction({ type: "hideTip" });
    corrChartRef.current?.dispatchAction({ type: "hideTip" });
    convChartRef.current?.dispatchAction({ type: "hideTip" });
    applyExpiryDots([]);
  }, [applyExpiryDots]);

  const priceEvents = useMemo(() => ({
    dataZoom: handleZoom,
    updateAxisPointer: handlePriceTipSync,
    globalout: hideAllTips,
  }), [handleZoom, handlePriceTipSync, hideAllTips]);

  const corrEvents = useMemo(() => ({
    dataZoom: handleZoom,
    updateAxisPointer: handleCorrTipSync,
    globalout: hideAllTips,
  }), [handleZoom, handleCorrTipSync, hideAllTips]);

  const convEvents = useMemo(() => ({
    dataZoom: handleZoom,
    updateAxisPointer: handleConvTipSync,
    globalout: hideAllTips,
  }), [handleZoom, handleConvTipSync, hideAllTips]);

  // First plot — reuse existing chartOption with gap extra + synced zoom
  const firstPlotOption = useMemo<EChartsOption | null>(() => {
    if (!combinedData) return null;
    const extra: FuturesChartExtra | undefined = gapMap
      ? {
          gapByCodeDate: gapMap,
          expiryDotsRef: viewMode === "history" ? expiryDotsRef : undefined,
        }
      : undefined;
    return buildFuturesChartOption(combinedData, viewMode, currentZoom, extra);
  }, [combinedData, gapMap, currentZoom, viewMode, expiryDotsRef]);

  // Second plot — correlation curves (active + matured contracts)
  const corrOption = useMemo<EChartsOption | null>(() => {
    if (!combinedData || !corrMap) return null;
    return buildCorrelationChartOption(combinedData, corrMap, viewMode, currentZoom);
  }, [combinedData, corrMap, currentZoom, viewMode]);

  // Third plot — basis convergence (contrarian): signed futures−spot gap
  // per contract in bps, decaying into the zero line toward expiry; with
  // expiry-gap dots (matured, history mode) and yesterday-gap dots (active).
  const convOption = useMemo<EChartsOption | null>(() => {
    if (!combinedData || !gapMap) return null;
    return buildBasisConvergenceChartOption(
      combinedData,
      gapMap,
      viewMode,
      currentZoom,
    );
  }, [combinedData, gapMap, currentZoom, viewMode]);

  const nActive = combinedData?.contracts.filter((c) => c.is_alive && c.is_continuous).length ?? 0;
  const nMatured = combinedData?.contracts.filter((c) => !c.is_alive).length ?? 0;

  return (
    <Stack spacing={2}>
      {/* First plot — futures price curves */}
      <ChartSection
        title={(
          <>
            Futures Price Curves — {nActive} active · {nMatured} matured
            {extData && (
              <Typography component="span" variant="body2" color="text.secondary" sx={{ ml: 1 }}>
                (tooltip shows gap_price_vs_underlying)
              </Typography>
            )}
          </>
        )}
      >
        {firstPlotOption && (
          <EChart
            option={firstPlotOption}
            height={460}
            minHeight={340}
            onReady={(inst) => { priceChartRef.current = inst; }}
            onEvents={priceEvents}
          />
        )}
      </ChartSection>

      {/* Second plot — correlation (active + matured, synced slider & hover) */}
      <ChartSection
        title={(
          <>
            Correlation (corr_price_vs_underlying, 20d rolling)
            {combinedData && (
              <Typography component="span" variant="body2" color="text.secondary" sx={{ ml: 1 }}>
                ({nActive} active · {nMatured} matured)
              </Typography>
            )}
          </>
        )}
        loading={loadingExt}
        error={errorExt}
        errorLabel="Failed to load correlation data"
      >
        {corrOption && (
          <EChart
            option={corrOption}
            height={340}
            minHeight={260}
            onReady={(inst) => { corrChartRef.current = inst; }}
            onEvents={corrEvents}
          />
        )}
      </ChartSection>

      {/* Third plot — basis convergence (contrarian): futures−spot gap in
          bps collapsing into the zero line toward expiry, with expiry-gap
          and yesterday-gap markers (synced slider & hover) */}
      <ChartSection
        title={(
          <>
            Basis Convergence (contrarian) — futures vs spot gap, bps
            {combinedData && (
              <Typography component="span" variant="body2" color="text.secondary" sx={{ ml: 1 }}>
                (zero = futures meets spot · dots mark expiry &amp; yesterday gaps)
              </Typography>
            )}
          </>
        )}
        loading={loadingExt}
        error={errorExt}
        errorLabel="Failed to load gap data"
      >
        {convOption && (
          <EChart
            option={convOption}
            height={340}
            minHeight={260}
            onReady={(inst) => { convChartRef.current = inst; }}
            onEvents={convEvents}
          />
        )}
      </ChartSection>
    </Stack>
  );
}
