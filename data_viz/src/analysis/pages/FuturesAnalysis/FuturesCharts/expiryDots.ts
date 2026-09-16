/**
 * Expiry-dot computation for the futures analysis charts (history mode):
 * for each contract active on the hovered date, mark its expiry on the
 * shared time axis at the spot price of the nearest trading day.
 *
 * Dots are deliberately kept OUT of React state: a state update on every
 * mouse move would rebuild the chart option (setOption notMerge) and reset
 * the tooltip → flickering. Instead we mutate a ref and push a targeted
 * per-series setOption (merge by id).
 */
import { useCallback, useEffect, useMemo, useRef } from "react";
import type { RefObject } from "react";
import type * as echarts from "echarts";
import {
  buildExpiryDotsSeriesData,
  EXPIRY_DOTS_SERIES_ID,
  type ExpiryDot,
} from "@/dataviz/features/futures/chartOption";
import type { FuturesCombinedResponse } from "@shared/types";

// Map a date string to its index in `dates`; falls back to the nearest
// trading date on/after the target, then to the last one before it.
export function findNearestDateIndex(
  dates: string[],
  dateIndexMap: Map<string, number>,
  targetDate: string,
): number | null {
  // Exact match first
  const exact = dateIndexMap.get(targetDate);
  if (exact != null) return exact;
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
}

// For each contract active on dates[hoveredIdx], locate its expiry date on
// the shared time axis and read the spot price there (the dot's y-value).
export function computeExpiryDots(
  combinedData: FuturesCombinedResponse,
  dateIndexMap: Map<string, number>,
  hoveredIdx: number,
): ExpiryDot[] {
  const { dates, rows, spot_price } = combinedData;
  const hoveredDate = dates[hoveredIdx];
  if (!hoveredDate) return [];

  // Build row lookup: code -> date -> FuturesRow
  const rowByCodeDate = new Map<string, Map<string, (typeof rows)[number]>>();
  for (const r of rows) {
    if (!rowByCodeDate.has(r.code)) rowByCodeDate.set(r.code, new Map());
    rowByCodeDate.get(r.code)!.set(r.date, r);
  }

  // Contracts active on the hovered date
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
    const mappedIdx = findNearestDateIndex(dates, dateIndexMap, expiryDateStr);
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
}

/**
 * Owns the expiry-dot ref store + targeted chart push. `priceChartRef` is
 * the chart the dots are rendered on (the price plot).
 */
export function useExpiryDots(
  product: string,
  combinedData: FuturesCombinedResponse | null,
  viewMode: "future" | "history",
  priceChartRef: RefObject<echarts.ECharts | null>,
) {
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
  }, [priceChartRef]);

  // Clear expiry dots when leaving history mode or changing product. The next
  // full option build (notMerge) recreates the dots series with empty data.
  useEffect(() => {
    expiryDotsRef.current = [];
    lastDotsSigRef.current = "";
  }, [viewMode, product]);

  const dateIndexMap = useMemo(() => {
    if (!combinedData) return null;
    const m = new Map<string, number>();
    combinedData.dates.forEach((d, i) => m.set(d, i));
    return m;
  }, [combinedData]);

  const computeExpiryDotsCb = useCallback(
    (hoveredIdx: number): ExpiryDot[] => {
      if (!combinedData || viewMode !== "history" || !dateIndexMap) return [];
      return computeExpiryDots(combinedData, dateIndexMap, hoveredIdx);
    },
    [combinedData, viewMode, dateIndexMap],
  );

  return { expiryDotsRef, applyExpiryDots, computeExpiryDots: computeExpiryDotsCb };
}
