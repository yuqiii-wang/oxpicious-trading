/**
 * Expiry-marker computation for the futures analysis charts.
 *
 * Semantics: hovering a date D answers "which contracts are LIVE on D, and
 * where do they expire?" — one dot per contract trading on D, ANCHORED AT
 * that contract's expiry date (D + the row's days_to_expiry, i.e. the
 * expiry as known on D), on the curve's endpoint there: the final
 * settlement price on the price plot, the final gap (the convergence
 * endpoint) on the basis plot. Sweeping the timeline walks the live set
 * through time — a contract's expiry dot appears while it is live and
 * disappears once the cursor passes its last trading day. Alive contracts
 * near the present whose expiry lies beyond the chart's data have no
 * on-chart anchor (the tooltip's exp column carries their forward expiry).
 *
 * The expiry label comes from the row's days_to_expiry on the hovered
 * date. Meta last_date serves as fallback for matured contracts only —
 * for alive contracts it is merely the data end, not an expiry.
 *
 * Dots are deliberately kept OUT of React state: a state update on every
 * mouse move would rebuild the chart option (setOption notMerge) and reset
 * the tooltip → flickering. Instead we mutate refs and push a targeted
 * per-series setOption (merge by id) to each chart.
 */
import { useCallback, useEffect, useMemo, useRef } from "react";
import type { RefObject } from "react";
import type * as echarts from "echarts";
import {
  buildExpiryDotsSeriesData,
  expiryDateFromDte,
  EXPIRY_DOTS_SERIES_ID,
  type ExpiryDot,
  type ViewMode,
} from "@/dataviz/features/futures/chartOption";
import type {
  FuturesCombinedResponse,
  FuturesContractMeta,
  FuturesRow,
} from "@shared/types";

export interface LiveExpiryDots {
  priceDots: ExpiryDot[];
  convDots: ExpiryDot[];
}

// Index of the first axis trading date on/after `targetDate`, or null when
// the target lies beyond the axis end (no on-chart anchor).
function nextDateIndex(
  dates: string[],
  dateIndexMap: Map<string, number>,
  targetDate: string,
): number | null {
  const exact = dateIndexMap.get(targetDate);
  if (exact != null) return exact;
  const target = new Date(targetDate + "T00:00:00");
  for (let i = 0; i < dates.length; i++) {
    if (new Date(dates[i] + "T00:00:00") >= target) return i;
  }
  return null;
}

// The contract's last row on/before `uptoDate` (rows sorted by date).
function lastRowAtOrBefore(rows: FuturesRow[], uptoDate: string): FuturesRow | null {
  for (let i = rows.length - 1; i >= 0; i--) {
    if (rows[i].date <= uptoDate) return rows[i];
  }
  return null;
}

// Last non-null gap at/before `fromDate`, walking back at most
// `maxLookback` calendar dates (the final session may be missing from
// futures_ext).
function lastGapAtOrBefore(
  dm: Map<string, number | null> | undefined,
  fromDate: string,
  maxLookback = 7,
): number | null {
  if (!dm) return null;
  let d = fromDate;
  for (let i = 0; i < maxLookback; i++) {
    const g = dm.get(d);
    if (g != null && Number.isFinite(g)) return g;
    const prev = new Date(d + "T00:00:00");
    prev.setDate(prev.getDate() - 1);
    d = `${prev.getFullYear()}-${String(prev.getMonth() + 1).padStart(2, "0")}-${String(prev.getDate()).padStart(2, "0")}`;
  }
  return null;
}

// One dot per contract LIVE on `hoveredDate`, anchored at its expiry date
// (hoveredDate + the row's days_to_expiry as known on that date) on the
// curve's endpoint there. In future mode only alive contracts qualify
// (matured lines are ghosted and tooltip-hidden there); history mode shows
// every contract live on D.
export function computeLiveExpiryDots(
  contracts: FuturesContractMeta[],
  dates: string[],
  dateIndexMap: Map<string, number>,
  sortedRowsByCode: Map<string, FuturesRow[]>,
  hoveredDate: string,
  gapByCodeDate: Map<string, Map<string, number | null>> | undefined,
  viewMode: ViewMode,
): LiveExpiryDots {
  const priceDots: ExpiryDot[] = [];
  const convDots: ExpiryDot[] = [];
  for (const c of contracts) {
    if (viewMode !== "history" && !c.is_alive) continue;
    const rows = sortedRowsByCode.get(c.code);
    if (!rows || !rows.length) continue;
    const rowOnDate = rows.find((r) => r.date === hoveredDate);
    if (!rowOnDate) continue; // contract not trading on the hovered date

    const dte = rowOnDate.days_to_expiry;
    if (dte == null || !Number.isFinite(dte) || dte < 0) continue;
    const expiryDate = expiryDateFromDte(hoveredDate, dte);

    // Anchor on the axis: the expiry date itself, or the first trading
    // date after it. Null = expiry beyond the chart's data (alive near
    // the present) — no on-chart anchor, the tooltip covers it.
    const anchorIdx = nextDateIndex(dates, dateIndexMap, expiryDate);
    if (anchorIdx == null) continue;
    const anchorDate = dates[anchorIdx];

    // The curve's endpoint at the anchor: the contract's last row on/before
    // the anchor date (normally the expiry session itself).
    const lastRow = lastRowAtOrBefore(rows, anchorDate);
    if (!lastRow) continue;

    const settle = lastRow.settlement_price;
    if (settle != null && Number.isFinite(settle)) {
      priceDots.push({
        dateIndex: anchorIdx,
        value: settle,
        code: c.code,
        expiryDate,
      });
    }
    const gap = lastGapAtOrBefore(gapByCodeDate?.get(c.code), lastRow.date);
    if (gap != null) {
      convDots.push({
        dateIndex: anchorIdx,
        value: gap * 1e4,
        code: c.code,
        expiryDate,
      });
    }
  }
  return { priceDots, convDots };
}

/**
 * Owns the expiry-dot ref stores + targeted chart pushes for BOTH plots.
 * `applyHover(dataIndex)` re-computes the live-contract expiry dots at the
 * hovered date; null clears them (mouse leave).
 */
export function useExpiryDots(
  product: string,
  combinedData: FuturesCombinedResponse | null,
  viewMode: ViewMode,
  gapByCodeDate: Map<string, Map<string, number | null>> | undefined,
  priceChartRef: RefObject<echarts.ECharts | null>,
  convChartRef: RefObject<echarts.ECharts | null>,
) {
  const priceDotsRef = useRef<ExpiryDot[]>([]);
  const convDotsRef = useRef<ExpiryDot[]>([]);
  const lastSigRef = useRef<string>("");

  // Per-contract rows sorted by date + the date→index map, rebuilt per
  // product payload; the per-hover scan below is O(contracts × rows).
  const sortedRowsByCode = useMemo(() => {
    const m = new Map<string, FuturesRow[]>();
    if (combinedData) {
      for (const r of combinedData.rows) {
        let arr = m.get(r.code);
        if (!arr) {
          arr = [];
          m.set(r.code, arr);
        }
        arr.push(r);
      }
      for (const arr of m.values()) arr.sort((a, b) => a.date.localeCompare(b.date));
    }
    return m;
  }, [combinedData]);

  const dateIndexMap = useMemo(() => {
    const m = new Map<string, number>();
    if (combinedData) combinedData.dates.forEach((d, i) => m.set(d, i));
    return m;
  }, [combinedData]);

  // Push new expiry dots to the charts WITHOUT rebuilding the whole option.
  const push = useCallback(
    (price: ExpiryDot[], conv: ExpiryDot[]) => {
      const sig = (ds: ExpiryDot[]) =>
        ds.map((d) => `${d.code}:${d.dateIndex}:${d.value ?? ""}`).join("|");
      const nextSig = `${sig(price)}#${sig(conv)}`;
      if (nextSig === lastSigRef.current) return; // unchanged — no-op
      lastSigRef.current = nextSig;
      priceDotsRef.current = price;
      convDotsRef.current = conv;
      const totalDates = combinedData?.dates.length;
      priceChartRef.current?.setOption({
        series: [
          { id: EXPIRY_DOTS_SERIES_ID, data: buildExpiryDotsSeriesData(price, totalDates) },
        ],
      });
      convChartRef.current?.setOption({
        series: [
          { id: EXPIRY_DOTS_SERIES_ID, data: buildExpiryDotsSeriesData(conv, totalDates) },
        ],
      });
    },
    [priceChartRef, convChartRef, combinedData],
  );

  // dataIndex = hovered category index; null clears (mouse leave).
  const applyHover = useCallback(
    (dataIndex: number | null) => {
      if (dataIndex == null || !Number.isFinite(dataIndex) || !combinedData) {
        push([], []);
        return;
      }
      const hoveredDate = combinedData.dates[dataIndex];
      if (!hoveredDate) {
        push([], []);
        return;
      }
      const { priceDots, convDots } = computeLiveExpiryDots(
        combinedData.contracts,
        combinedData.dates,
        dateIndexMap,
        sortedRowsByCode,
        hoveredDate,
        gapByCodeDate,
        viewMode,
      );
      push(priceDots, convDots);
    },
    [combinedData, dateIndexMap, sortedRowsByCode, gapByCodeDate, viewMode, push],
  );

  // Clear refs when leaving history mode or changing product. The next
  // full option build (notMerge) recreates the dots series with empty data.
  useEffect(() => {
    priceDotsRef.current = [];
    convDotsRef.current = [];
    lastSigRef.current = "";
    // Drop any on-chart dots left over from the previous mode/product —
    // child EChart option effects have already run by the time this fires.
    priceChartRef.current?.setOption({
      series: [{ id: EXPIRY_DOTS_SERIES_ID, data: [] }],
    });
    convChartRef.current?.setOption({
      series: [{ id: EXPIRY_DOTS_SERIES_ID, data: [] }],
    });
  }, [product, viewMode, priceChartRef, convChartRef]);

  return { priceDotsRef, convDotsRef, applyHover };
}
