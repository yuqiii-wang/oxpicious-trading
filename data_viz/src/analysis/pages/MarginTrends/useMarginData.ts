/**
 * useMarginData — custom hook for Margin Trends data fetching and state.
 *
 * Manages:
 *   - Series data (1st plot)
 *   - Correlation data (2nd plot)
 *   - Trend episodes (shade overlay on 1st plot)
 *   - Selected security codes (drives both plots)
 *   - Single-item mode reset logic
 */
import { useEffect, useRef, useState } from "react";
import {
  fetchMarginIndustrySeries,
  fetchMarginIndustryCorrelation,
  fetchMarginTrends,
} from "@/lib/api-client";
import type {
  MarginIndustrySeriesResponse,
  MarginIndustryCorrelationResponse,
  MarginTrendsShadeResponse,
} from "@shared/types";
import type { MarginAttribution, MarginSeries, CorrWindow } from "./constants";

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
  corrData: MarginIndustryCorrelationResponse | null;
  trendsData: MarginTrendsShadeResponse | null;
  loadingSeries: boolean;
  loadingCorr: boolean;
  errorSeries: string | null;
  errorCorr: string | null;
  series: MarginSeries;
  setSeries: (s: MarginSeries) => void;
  corrWindow: CorrWindow;
  setCorrWindow: (w: CorrWindow) => void;
  selectedCodes: string[];
  setSelectedCodes: (codes: string[]) => void;
  isSingleItemMode: boolean;
}

export function useMarginData(
  industryId: string | null,
  attribution: MarginAttribution,
  selectedItemCode: string | null | undefined,
): UseMarginDataReturn {
  const isSingleItemMode = !!selectedItemCode;

  const [seriesData, setSeriesData] = useState<MarginIndustrySeriesResponse | null>(null);
  const [corrData, setCorrData] = useState<MarginIndustryCorrelationResponse | null>(null);
  const [trendsData, setTrendsData] = useState<MarginTrendsShadeResponse | null>(null);
  const [loadingSeries, setLoadingSeries] = useState(false);
  const [loadingCorr, setLoadingCorr] = useState(false);
  const [errorSeries, setErrorSeries] = useState<string | null>(null);
  const [errorCorr, setErrorCorr] = useState<string | null>(null);

  const [series, setSeries] = useState<MarginSeries>("balance");
  const [corrWindow, setCorrWindow] = useState<CorrWindow>(60);
  const [selectedCodes, setSelectedCodes] = useState<string[]>([]);

  // ---- Load series data (1st plot) when industry / attribution changes ----
  useEffect(() => {
    if (!industryId) {
      setSeriesData(null);
      setSelectedCodes([]);
      return;
    }
    let cancelled = false;
    setLoadingSeries(true);
    setErrorSeries(null);
    setCorrData(null);
    fetchMarginIndustrySeries(industryId, attribution)
      .then((resp) => {
        if (cancelled) return;
        setSeriesData(resp);
        setLoadingSeries(false);
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
        setSelectedCodes([]);
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [industryId, attribution]);

  // ---- Load trend episodes (1st plot shade overlay) ----
  useEffect(() => {
    if (!industryId) {
      setTrendsData(null);
      return;
    }
    let cancelled = false;
    fetchMarginTrends(industryId, attribution)
      .then((resp) => { if (!cancelled) setTrendsData(resp); })
      .catch(() => { if (!cancelled) setTrendsData(null); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [industryId, attribution]);

  // ---- Reset selectedCodes when exiting single-item mode ----
  const prevSingleItemRef = useRef(false);
  useEffect(() => {
    const wasSingle = prevSingleItemRef.current;
    prevSingleItemRef.current = isSingleItemMode;
    if (wasSingle && !isSingleItemMode && seriesData) {
      setSelectedCodes(pickTopCodes(seriesData.rows, seriesData.securities, series, 2));
    }
  }, [isSingleItemMode, seriesData, series]);

  // ---- Load correlation data (2nd plot) ----
  useEffect(() => {
    if (isSingleItemMode) {
      setCorrData(null);
      setLoadingCorr(false);
      return;
    }
    if (!industryId || selectedCodes.length < 2) {
      setCorrData(null);
      return;
    }
    let cancelled = false;
    setLoadingCorr(true);
    setErrorCorr(null);
    fetchMarginIndustryCorrelation(industryId, attribution, selectedCodes, series, corrWindow)
      .then((resp) => {
        if (cancelled) return;
        setCorrData(resp);
        setLoadingCorr(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setErrorCorr(e.message);
        setLoadingCorr(false);
      });
    return () => { cancelled = true; };
  }, [industryId, attribution, selectedCodes, series, corrWindow, isSingleItemMode]);

  return {
    seriesData,
    corrData,
    trendsData,
    loadingSeries,
    loadingCorr,
    errorSeries,
    errorCorr,
    series,
    setSeries,
    corrWindow,
    setCorrWindow,
    selectedCodes,
    setSelectedCodes,
    isSingleItemMode,
  };
}
