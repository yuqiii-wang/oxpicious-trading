/**
 * useMarginData — custom hook for Margin Trends data fetching and state.
 *
 * Manages:
 *   - Series data (main plot) + the raw response (analytics source)
 *   - Selected security codes (highlighting in the plot)
 *   - Single-item mode reset logic
 */
import { useEffect, useRef, useState } from "react";
import { fetchMarginIndustrySeries } from "@/lib/api-client";
import type { MarginIndustrySeriesResponse } from "@shared/types";
import type { MarginAttribution, MarginSeries } from "../constants";

/** Compute top-N codes by latest non-null value from series rows. */
function pickTopCodes(
  rows: Array<{ code: string; balance: number | null; buy: number | null }>,
  securities: Array<{ code: string }>,
  series: MarginSeries,
  n: number,
): string[] {
  const codeLatest = new Map<string, number>();
  const seen = new Set<string>();
  for (let i = rows.length - 1; i >= 0; i--) {
    const r = rows[i];
    if (seen.has(r.code)) continue;
    const v = series === "balance" ? r.balance : r.buy;
    if (v != null && Number.isFinite(v)) {
      codeLatest.set(r.code, v);
      seen.add(r.code);
    }
  }
  return securities
    .map((s) => ({ code: s.code, v: codeLatest.get(s.code) ?? -Infinity }))
    .sort((a, b) => b.v - a.v)
    .slice(0, n)
    .map((x) => x.code);
}

export interface UseMarginDataReturn {
  seriesData: MarginIndustrySeriesResponse | null;
  /** The RAW curve response — the hyped-vs-drained analytics (returns /
   *  vol / buy-share terciles) always derive from it, so the companion
   *  chart and the intensity shading stay IDENTICAL across the Raw |
   *  Normalized toggle. Same object as seriesData in raw mode. */
  rawSeriesData: MarginIndustrySeriesResponse | null;
  loadingSeries: boolean;
  errorSeries: string | null;
  series: MarginSeries;
  setSeries: (s: MarginSeries) => void;
  selectedCodes: string[];
  setSelectedCodes: (codes: string[]) => void;
  isSingleItemMode: boolean;
}

export function useMarginData(
  industryId: string | null,
  attribution: MarginAttribution,
  selectedItemCode: string | null | undefined,
  underlyingCode?: string | null,
  normalize?: boolean,
): UseMarginDataReturn {
  const isSingleItemMode = !!selectedItemCode;

  const [seriesData, setSeriesData] = useState<MarginIndustrySeriesResponse | null>(null);
  const [rawSeriesData, setRawSeriesData] = useState<MarginIndustrySeriesResponse | null>(null);
  const [loadingSeries, setLoadingSeries] = useState(false);
  const [errorSeries, setErrorSeries] = useState<string | null>(null);

  const [series, setSeries] = useState<MarginSeries>("balance");
  const [selectedCodes, setSelectedCodes] = useState<string[]>([]);

  // ---- Load series data when industry / attribution / value mode changes.
  //      The RAW response is always fetched — it is the analytics source
  //      so the hyped-vs-drained view does not move with the value toggle.
  //      With the toggle on, the normalized response is fetched
  //      additionally and becomes the displayed series (margin lines +
  //      member-change dots). ---------------------------------------------
  useEffect(() => {
    if (!industryId) {
      setSeriesData(null);
      setRawSeriesData(null);
      setSelectedCodes([]);
      return;
    }
    const isNormalized = !!normalize && attribution === "industry";
    let cancelled = false;
    setLoadingSeries(true);
    setErrorSeries(null);
    const rawFetch = fetchMarginIndustrySeries(industryId, attribution, underlyingCode);
    const normFetch = isNormalized
      ? fetchMarginIndustrySeries(industryId, attribution, underlyingCode, true)
      : null;
    Promise.all([rawFetch, normFetch])
      .then(([raw, norm]) => {
        if (cancelled) return;
        setRawSeriesData(raw);
        setSeriesData(norm ?? raw);
        setLoadingSeries(false);
        const resp = norm ?? raw;
        if (isSingleItemMode && selectedItemCode) {
          setSelectedCodes([selectedItemCode]);
        } else {
          setSelectedCodes(pickTopCodes(resp.rows, resp.securities, series, 2));
        }
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setErrorSeries(e.message);
        setLoadingSeries(false);
        setSeriesData(null);
        setRawSeriesData(null);
        setSelectedCodes([]);
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [industryId, attribution, underlyingCode, normalize]);

  // ---- Reset selectedCodes when exiting single-item mode ----
  const prevSingleItemRef = useRef(false);
  useEffect(() => {
    const wasSingle = prevSingleItemRef.current;
    prevSingleItemRef.current = isSingleItemMode;
    if (wasSingle && !isSingleItemMode && seriesData) {
      setSelectedCodes(pickTopCodes(seriesData.rows, seriesData.securities, series, 2));
    }
  }, [isSingleItemMode, seriesData, series]);

  return {
    seriesData,
    rawSeriesData,
    loadingSeries,
    errorSeries,
    series,
    setSeries,
    selectedCodes,
    setSelectedCodes,
    isSingleItemMode,
  };
}
