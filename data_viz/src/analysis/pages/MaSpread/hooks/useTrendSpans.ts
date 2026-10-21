/**
 * useTrendSpans — the signals tier's trend-phase spans
 * (analysis.trend_days via the trend-spans API — the RSI re-entry
 * classification shared with the rule cells). Fetched eagerly on mount /
 * code switch (the tooltip readout wants them on any hover) — one small
 * endpoint, windowed to the API's 10y max (the panel charts full
 * history). A slow response for a previous code never lands after a
 * switch (guard ref); a failure yields {} (the section just stays off).
 */
import { useEffect, useRef, useState } from "react";
import { fetchTrendSpans } from "@/lib/api-client";
import type { MaSpreadSecType } from "@shared/types";
import type { TrendSpansRecord } from "../chartOption";

export function useTrendSpans(code: string, secType: MaSpreadSecType) {
  const [trendSpans, setTrendSpans] = useState<TrendSpansRecord | null>(null);
  // Tracks the panel's current (secType, code) so a slow trend-span
  // response for a previous code never lands after a switch (the
  // regime-span precedent).
  const trendCodeKeyRef = useRef(`${secType}:${code}`);
  useEffect(() => {
    trendCodeKeyRef.current = `${secType}:${code}`;
    setTrendSpans(null);
    const reqKey = `${secType}:${code}`;
    fetchTrendSpans(secType, code, 3650)
      .then((d) => {
        if (trendCodeKeyRef.current === reqKey) setTrendSpans(d.spans ?? {});
      })
      .catch(() => {
        if (trendCodeKeyRef.current === reqKey) setTrendSpans({});
      });
  }, [code, secType]);
  return trendSpans;
}
