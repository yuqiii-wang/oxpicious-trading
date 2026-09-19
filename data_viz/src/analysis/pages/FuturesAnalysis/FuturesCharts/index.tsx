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
import { useChartThemeMode } from "@/shared/charts/base-chart";
import { useAiAskAddon } from "@/shared/ai-ask";
import type { AiAskSpec } from "@/shared/ai-ask";
import type { FuturesCombinedResponse } from "@shared/types";
import {
  buildFuturesChartOption,
  buildBasisConvergenceChartOption,
  EXPIRY_DOTS_SERIES_NAME,
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
  const themeMode = useChartThemeMode();
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
    return buildFuturesChartOption(themeMode, combinedData, viewMode, currentZoom, extra);
  }, [themeMode, combinedData, gapMap, currentZoom, viewMode, expiryDotsRef]);

  // Second plot — correlation curves (active + matured contracts)
  const corrOption = useMemo<EChartsOption | null>(() => {
    if (!combinedData || !corrMap) return null;
    return buildCorrelationChartOption(themeMode, combinedData, corrMap, viewMode, currentZoom);
  }, [themeMode, combinedData, corrMap, currentZoom, viewMode]);

  // Third plot — basis convergence (contrarian): signed futures−spot gap
  // per contract in bps, decaying into the zero line toward expiry; with
  // expiry-gap dots (matured, history mode) and yesterday-gap dots (active).
  const convOption = useMemo<EChartsOption | null>(() => {
    if (!combinedData || !gapMap) return null;
    return buildBasisConvergenceChartOption(
      themeMode,
      combinedData,
      gapMap,
      viewMode,
      currentZoom,
    );
  }, [themeMode, combinedData, gapMap, currentZoom, viewMode]);

  const nActive = combinedData?.contracts.filter((c) => c.is_alive && c.is_continuous).length ?? 0;
  const nMatured = combinedData?.contracts.filter((c) => !c.is_alive).length ?? 0;

  // AI Ask — one spec per plot; state carries the CURRENT view mode
  // (future/history) so the modal / LLM always describe the view on screen.
  const aiInstruments = useMemo<NonNullable<AiAskSpec["instruments"]>>(
    () => [
      { code: product, name: combinedData.product_name, assetClass: "futures" },
      ...(combinedData.underlying_code
        ? [{ code: combinedData.underlying_code, name: combinedData.underlying_name, assetClass: "index" as const }]
        : []),
    ],
    [product, combinedData],
  );
  const spotName = combinedData.underlying_name || combinedData.product_name;
  const priceSpec = useMemo<AiAskSpec>(
    () => ({
      intro:
        `Futures price curves for product ${product} (${combinedData.product_name}): one ` +
        "settlement-price line per contract — active contracts in a blue gradient (farther " +
        "maturity = lighter), matured contracts in grey — plus the underlying spot line for " +
        "index futures. The tooltip adds each contract's gap vs the underlying; in history mode " +
        "hovering shows expiry-gap dots. All plots on this page share one synced time slider " +
        "and crosshair.",
      instruments: aiInstruments,
      series: [
        { name: spotName, unit: "index pts", description: "underlying (spot) daily close" },
        { name: EXPIRY_DOTS_SERIES_NAME, description: "expiry-gap dots (history mode, hover-triggered)" },
      ],
      state: { view_mode: viewMode },
      notes: [
        "Blue gradient = active contracts (farther maturity lighter); grey = matured.",
        "Time slider + hover crosshair are synchronized across the price / correlation / basis plots.",
      ],
    }),
    [product, combinedData.product_name, aiInstruments, spotName, viewMode],
  );
  const corrSpec = useMemo<AiAskSpec>(
    () => ({
      intro:
        "Per-contract 20-day rolling correlation vs the underlying (corr_price_vs_underlying): " +
        `one line per contract — ${nActive} active · ${nMatured} matured — using the same ` +
        "per-contract colors as the price curves above, y-axis fixed at -1 to 1. Follows the " +
        "shared time slider and crosshair of the futures analysis page.",
      instruments: aiInstruments,
      state: { view_mode: viewMode },
      notes: ["+1 = the contract moves in lockstep with the underlying; 0 = no linear relation."],
    }),
    [aiInstruments, nActive, nMatured, viewMode],
  );
  const convSpec = useMemo<AiAskSpec>(
    () => ({
      intro:
        "Basis convergence (contrarian): the signed futures − spot gap per contract in basis " +
        "points, collapsing into the zero line as each contract ages toward expiry (futures " +
        "meets spot). Dots mark each matured contract's expiry gap (history mode) and each " +
        "active contract's yesterday gap. Follows the shared time slider and crosshair of the " +
        "futures analysis page.",
      instruments: aiInstruments,
      series: [
        { name: "__zero_line__", unit: "bps", description: "the convergence target — futures meets spot (gap = 0)" },
      ],
      state: { view_mode: viewMode },
      notes: ["Contract line colors match the price curves above (blue active / grey matured)."],
    }),
    [aiInstruments, viewMode],
  );
  const priceAddon = useAiAskAddon({
    title: "Futures Price Curves",
    subtitle: `${nActive} active · ${nMatured} matured contracts`,
    option: firstPlotOption,
    spec: priceSpec,
    getInstance: () => priceChartRef.current,
  });
  const corrAddon = useAiAskAddon({
    title: "Correlation (corr_price_vs_underlying, 20d rolling)",
    subtitle: `${nActive} active · ${nMatured} matured contracts`,
    option: corrOption,
    spec: corrSpec,
    getInstance: () => corrChartRef.current,
  });
  const convAddon = useAiAskAddon({
    title: "Basis Convergence (contrarian)",
    subtitle: "futures − spot gap, bps · zero = futures meets spot",
    option: convOption,
    spec: convSpec,
    getInstance: () => convChartRef.current,
  });

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
        titleAddon={priceAddon}
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
        titleAddon={corrAddon}
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
        titleAddon={convAddon}
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
