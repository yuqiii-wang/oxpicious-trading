/**
 * FuturesCharts — the 2-plot futures analysis view.
 *
 * Layout (top → bottom):
 *   1. Futures price curves (identical to Data Viz) with gap_price_vs_underlying
 *      added to the tooltip for each contract.
 *   2. Correlation (corr_price_vs_underlying) — one line per contract
 *      (active + matured), y-axis fixed at -1 to 1.
 *
 * Both plots share:
 *   - A synced time slider (dataZoom) via shared zoomRange state.
 *   - The exact same per-contract color scheme (blue active / grey matured
 *     gradients) via computeFuturesContractStyles().
 *   - Cross-chart hover: hovering either plot shows the tooltip + axis
 *     pointer on both. This is done via manual showTip/hideTip dispatch
 *     (NOT echarts.connect) — connect propagates the source chart's
 *     seriesIndex+dataIndex, and the receiving chart resolves the point
 *     from its own series at that index; when that series has a null
 *     correlation at the hovered date the resolved point is NaN and the
 *     tooltip silently goes empty/stale. Manual dispatch with a pixel
 *     computed from the shared category index is deterministic.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Box, CircularProgress, Stack, Typography } from "@mui/material";
import type * as echarts from "echarts";
import EChart from "@/components/EChart";
import {
  fetchFuturesExt,
} from "@/lib/api-client";
import type {
  FuturesCombinedResponse,
} from "@shared/types";
import type { FuturesExtResponse } from "@/lib/api-client/analysis-futures";
import {
  buildFuturesChartOption,
  buildExpiryDotsSeriesData,
  computeFuturesContractStyles,
  EXPIRY_DOTS_SERIES_ID,
  type FuturesChartExtra,
  type ExpiryDot,
} from "../../../dataviz/features/futures/chartOption";
import type { EChartsOption } from "echarts";
import {
  AXIS_POINTER_LINE,
  TOOLTIP_CARD_BG,
  TOOLTIP_CARD_BORDER,
  TOOLTIP_CARD_TEXT,
} from "@/theme/chart-palette";

interface FuturesChartsProps {
  product: string;
  combinedData: FuturesCombinedResponse;
  viewMode: "future" | "history";
}

export function FuturesCharts({ product, combinedData, viewMode }: FuturesChartsProps) {
  const [extData, setExtData] = useState<FuturesExtResponse | null>(null);
  const [loadingExt, setLoadingExt] = useState(false);
  const [errorExt, setErrorExt] = useState<string | null>(null);

  // Chart instances for manual cross-chart tooltip sync
  const priceChartRef = useRef<echarts.ECharts | null>(null);
  const corrChartRef = useRef<echarts.ECharts | null>(null);
  const syncingRef = useRef(false);

  // Shared zoom range (percentages 0-100) for both plots' dataZoom sliders
  const [zoomRange, setZoomRange] = useState<{ start: number; end: number } | null>(null);

  // Expiry dots — computed on hover for history mode. Deliberately kept OUT of
  // React state: a state update on every mouse move would rebuild the chart
  // option (setOption notMerge) and reset the tooltip → flickering. Instead we
  // mutate the ref and push a targeted per-series setOption (merge by id).
  const expiryDotsRef = useRef<ExpiryDot[]>([]);
  const lastDotsSigRef = useRef<string>("");

  // Push new expiry dots to the chart WITHOUT rebuilding the whole option.
  const applyExpiryDots = useCallback((dots: ExpiryDot[]) => {
    const sig = dots.map((d) => `${d.code}:${d.dateIndex}:${d.value ?? ""}`).join("|");
    if (sig === lastDotsSigRef.current) return; // unchanged — no-op
    lastDotsSigRef.current = sig;
    expiryDotsRef.current = dots;
    priceChartRef.current?.setOption({
      series: [{ id: EXPIRY_DOTS_SERIES_ID, data: buildExpiryDotsSeriesData(dots) }],
    });
  }, []);

  // Default zoom: show last 120 days
  const defaultZoom = useMemo(() => {
    if (!combinedData) return { start: 0, end: 100 };
    const total = combinedData.dates.length;
    const start = Math.max(0, 100 - (120 / Math.max(total, 1)) * 100);
    return { start, end: 100 };
  }, [combinedData]);

  const currentZoom = zoomRange ?? defaultZoom;

  // Load ext data (gap + correlation)
  useEffect(() => {
    if (!product) return;
    let cancelled = false;
    setLoadingExt(true);
    setErrorExt(null);
    fetchFuturesExt(product)
      .then((resp) => {
        if (cancelled) return;
        setExtData(resp);
        setLoadingExt(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setErrorExt(e.message);
        setLoadingExt(false);
      });
    return () => { cancelled = true; };
  }, [product]);

  // Reset zoom when product changes
  useEffect(() => {
    setZoomRange(null);
  }, [product]);

  // Clear expiry dots when leaving history mode or changing product. The next
  // full option build (notMerge) recreates the dots series with empty data.
  useEffect(() => {
    expiryDotsRef.current = [];
    lastDotsSigRef.current = "";
  }, [viewMode, product]);

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

  // ---- Expiry dots computation (history mode, hover-triggered) -----------
  // Map a date string to its index in the combinedData.dates array.
  const dateIndexMap = useMemo(() => {
    if (!combinedData) return null;
    const m = new Map<string, number>();
    combinedData.dates.forEach((d, i) => m.set(d, i));
    return m;
  }, [combinedData]);

  // Build gapByCodeDate Map for chartOption (needed before computeExpiryDots)
  const gapMap = useMemo(() => {
    if (!extData) return undefined;
    const map = new Map<string, Map<string, number | null>>();
    for (const [code, dateGap] of Object.entries(extData.gapByCodeDate)) {
      const inner = new Map<string, number | null>();
      for (const [date, val] of Object.entries(dateGap)) {
        inner.set(date, val);
      }
      map.set(code, inner);
    }
    return map;
  }, [extData]);

  const corrMap = useMemo(() => {
    if (!extData) return undefined;
    const map = new Map<string, Map<string, number | null>>();
    for (const [code, dateCorr] of Object.entries(extData.corrByCodeDate)) {
      const inner = new Map<string, number | null>();
      for (const [date, val] of Object.entries(dateCorr)) {
        inner.set(date, val);
      }
      map.set(code, inner);
    }
    return map;
  }, [extData]);

  // Find the nearest trading date index on or after the given date
  const findNearestDateIndex = useCallback(
    (targetDate: string): number | null => {
      if (!combinedData || !dateIndexMap) return null;
      const { dates } = combinedData;
      // Exact match first
      if (dateIndexMap.has(targetDate)) return dateIndexMap.get(targetDate)!;
      // Find next trading date >= target (using local time for comparison)
      const target = new Date(targetDate + "T00:00:00");
      for (let i = 0; i < dates.length; i++) {
        const d = new Date(dates[i] + "T00:00:00");
        if (d >= target) return i;
      }
      // Find previous trading date < target
      for (let i = dates.length - 1; i >= 0; i--) {
        const d = new Date(dates[i] + "T00:00:00");
        if (d < target) return i;
      }
      return null;
    },
    [combinedData, dateIndexMap],
  );

  // Compute expiry dots from the hovered date index
  const computeExpiryDots = useCallback(
    (hoveredIdx: number): ExpiryDot[] => {
      if (!combinedData || viewMode !== "history" || !dateIndexMap) return [];
      const { dates, rows, spot_price } = combinedData;
      const hoveredDate = dates[hoveredIdx];
      if (!hoveredDate) return [];

      // Build row lookup: code -> date -> FuturesRow
      const rowByCodeDate = new Map<string, Map<string, (typeof rows)[number]>>();
      for (const r of rows) {
        if (!rowByCodeDate.has(r.code)) rowByCodeDate.set(r.code, new Map());
        rowByCodeDate.get(r.code)!.set(r.date, r);
      }

      // For each contract active on this date, compute its expiry
      const contractsOnDate = new Set<string>();
      for (const r of rows) {
        if (r.date === hoveredDate) contractsOnDate.add(r.code);
      }

      const dots: ExpiryDot[] = [];
      const processedCodes = new Set<string>();

      for (const code of contractsOnDate) {
        if (processedCodes.has(code)) continue;
        processedCodes.add(code);

        const row = rowByCodeDate.get(code)?.get(hoveredDate);
        if (!row) continue;
        const dte = row.days_to_expiry;
        if (dte == null || !Number.isFinite(dte) || dte < 0) continue;

        // Compute expiry date: hovered_date + days_to_expiry
        const hoveredDateObj = new Date(hoveredDate + "T00:00:00");
        const expiryDateObj = new Date(hoveredDateObj);
        expiryDateObj.setDate(expiryDateObj.getDate() + Math.round(dte));
        const y = expiryDateObj.getFullYear();
        const m = String(expiryDateObj.getMonth() + 1).padStart(2, "0");
        const d = String(expiryDateObj.getDate()).padStart(2, "0");
        const expiryDateStr = `${y}-${m}-${d}`;

        // Map expiry date to nearest trading date
        const mappedIdx = findNearestDateIndex(expiryDateStr);
        if (mappedIdx == null) continue;

        const mappedDateStr = dates[mappedIdx] ?? expiryDateStr;

        // Get spot price at mapped date (the dot's y-value)
        const spotVal = spot_price?.[mappedIdx] ?? null;

        dots.push({
          dateIndex: mappedIdx,
          value: spotVal != null && Number.isFinite(spotVal) ? spotVal : null,
          code,
          expiryDate: expiryDateStr,
          mappedDate: mappedDateStr,
          dte: Math.round(dte),
        });
      }

      return dots;
    },
    [combinedData, viewMode, dateIndexMap, findNearestDateIndex],
  );

  // ---- Cross-chart tooltip sync (manual showTip/hideTip) ------------------
  // Forward the hovered category index to the other chart as a pixel point,
  // so its axis tooltip + pointer render at the same date regardless of
  // per-series nulls (see file header for why echarts.connect is not used).
  const syncTipTo = useCallback((to: echarts.ECharts | null, params: unknown) => {
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
  }, []);

  const handlePriceTipSync = useCallback(
    (params: unknown) => {
      syncTipTo(corrChartRef.current, params);
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
    [syncTipTo, viewMode, computeExpiryDots, applyExpiryDots],
  );
  const handleCorrTipSync = useCallback(
    (params: unknown) => syncTipTo(priceChartRef.current, params),
    [syncTipTo],
  );

  const hideCorrTip = useCallback(() => {
    corrChartRef.current?.dispatchAction({ type: "hideTip" });
    applyExpiryDots([]);
  }, [applyExpiryDots]);
  const hidePriceTip = useCallback(() => {
    priceChartRef.current?.dispatchAction({ type: "hideTip" });
    applyExpiryDots([]);
  }, [applyExpiryDots]);

  const priceEvents = useMemo(() => ({
    dataZoom: handleZoom,
    updateAxisPointer: handlePriceTipSync,
    globalout: hideCorrTip,
  }), [handleZoom, handlePriceTipSync, hideCorrTip]);

  const corrEvents = useMemo(() => ({
    dataZoom: handleZoom,
    updateAxisPointer: handleCorrTipSync,
    globalout: hidePriceTip,
  }), [handleZoom, handleCorrTipSync, hidePriceTip]);

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

  // Second plot — correlation curves (active + matured contracts), using the
  // exact same per-contract colors/opacities as the main price plot
  const corrOption = useMemo<EChartsOption | null>(() => {
    if (!combinedData || !corrMap) return null;

    const { dates } = combinedData;
    const { styleByCode, qualifying, matured, maturedCodeSet } =
      computeFuturesContractStyles(combinedData, viewMode, currentZoom);

    const mkSeries = (code: string) => {
      const st = styleByCode.get(code)!;
      const dm = corrMap.get(code);
      const data = dates.map((d) => {
        const v = dm?.get(d);
        return v != null && Number.isFinite(v) ? v : null;
      });
      return {
        name: code,
        type: "line" as const,
        showSymbol: false,
        connectNulls: true,
        sampling: "lttb" as const,
        data,
        itemStyle: { color: st.color },
        lineStyle: {
          width: st.lineWidth,
          color: st.color,
          opacity: st.opacity,
        },
        z: st.isActive ? 10 : 5,
      };
    };

    // Series order MUST match the price plot ([...qualifying, ...matured]):
    // echarts.connect cross-chart tooltip sync passes (seriesIndex, dataIndex)
    // of the source chart's first involved series, and the receiving chart
    // resolves the point from its own series at the same index — so a
    // different order would sample the wrong contract (often with null corr
    // → stale tooltip). z-levels still put matured behind the active blues.
    const series = [
      ...qualifying.map((c) => mkSeries(c.code)),
      ...matured.map((c) => mkSeries(c.code)),
    ];

    return {
      backgroundColor: "transparent",
      animation: false,
      grid: { left: 60, right: 24, top: 24, bottom: 60 },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "line", lineStyle: { color: AXIS_POINTER_LINE, type: "dashed" } },
        confine: true,
        backgroundColor: TOOLTIP_CARD_BG,
        borderColor: TOOLTIP_CARD_BORDER,
        borderWidth: 1,
        textStyle: { color: TOOLTIP_CARD_TEXT, fontSize: 11 },
        formatter: (params: unknown) => {
          const arr = (Array.isArray(params) ? params : [params]) as Array<{
            dataIndex?: number;
            axisValue?: string;
            seriesName?: string;
            value?: number | null;
            color?: string;
          }>;
          if (arr.length === 0) return "";
          const dateStr = arr[0].axisValue ?? dates[arr[0].dataIndex ?? 0] ?? "";
          const rowsHtml = arr
            .filter((p) => {
              const v = Array.isArray(p.value) ? p.value[1] : p.value;
              if (v == null || !Number.isFinite(v as number)) return false;
              // Matured (history) curves only appear in history mode —
              // matching the main price plot's tooltip behavior.
              if (viewMode !== "history" && maturedCodeSet.has(p.seriesName ?? "")) return false;
              return true;
            })
            .map((p) => {
              const v = (Array.isArray(p.value) ? p.value[1] : p.value) as number;
              const valStr = (v >= 0 ? "+" : "") + v.toFixed(4);
              return `<div><span style="color:${p.color ?? ""}">●</span> ${p.seriesName ?? ""}: <b>${valStr}</b></div>`;
            })
            .join("");
          return `<div style="font-weight:600">${dateStr}</div>${rowsHtml}`;
        },
      },
      xAxis: {
        type: "category",
        data: dates,
        axisLine: { lineStyle: { color: "#ddd" } },
        axisLabel: { fontSize: 11, color: "#888" },
        splitLine: { show: false },
        boundaryGap: false,
      },
      yAxis: {
        type: "value",
        min: -1,
        max: 1,
        name: "Correlation",
        nameTextStyle: { fontSize: 10, color: "#888" },
        axisLine: { lineStyle: { color: "#ddd" } },
        axisLabel: { fontSize: 11, color: "#888" },
        splitLine: { lineStyle: { color: "#eee", type: "dashed", opacity: 0.4 } },
      },
      dataZoom: [
        {
          type: "inside",
          start: currentZoom.start,
          end: currentZoom.end,
          zoomLock: false,
        },
        {
          type: "slider",
          height: 18,
          bottom: 10,
          start: currentZoom.start,
          end: currentZoom.end,
        },
      ],
      series,
    };
  }, [combinedData, corrMap, currentZoom, viewMode]);

  const nActive = combinedData?.contracts.filter((c) => c.is_alive && c.is_continuous).length ?? 0;
  const nMatured = combinedData?.contracts.filter((c) => !c.is_alive).length ?? 0;

  return (
    <Stack spacing={2}>
      {/* First plot — futures price curves */}
      <Box>
        <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
          Futures Price Curves — {nActive} active · {nMatured} matured
          {extData && (
            <Typography component="span" variant="body2" color="text.secondary" sx={{ ml: 1 }}>
              (tooltip shows gap_price_vs_underlying)
            </Typography>
          )}
        </Typography>
        {firstPlotOption && (
          <EChart
            option={firstPlotOption}
            height={460}
            minHeight={340}
            onReady={(inst) => { priceChartRef.current = inst; }}
            onEvents={priceEvents}
          />
        )}
      </Box>

      {/* Second plot — correlation (active + matured, synced slider & hover) */}
      <Box>
        <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
          Correlation (corr_price_vs_underlying, 20d rolling)
          {combinedData && (
            <Typography component="span" variant="body2" color="text.secondary" sx={{ ml: 1 }}>
              ({nActive} active · {nMatured} matured)
            </Typography>
          )}
        </Typography>
        {loadingExt && (
          <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
            <CircularProgress size={28} />
          </Box>
        )}
        {errorExt && (
          <Typography variant="body2" color="error">
            Failed to load correlation data: {errorExt}
          </Typography>
        )}
        {!loadingExt && !errorExt && corrOption && (
          <EChart
            option={corrOption}
            height={340}
            minHeight={260}
            onReady={(inst) => { corrChartRef.current = inst; }}
            onEvents={corrEvents}
          />
        )}
      </Box>
    </Stack>
  );
}
