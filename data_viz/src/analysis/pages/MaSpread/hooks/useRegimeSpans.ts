/**
 * useRegimeSpans — the Market Regimes row's span source
 * (stats.market_regimes): the code's contiguous same-regime spans, fetched
 * ONCE on the first regime pick (imperative ensureSpans — an effect that
 * both sets and depends on its own loading flag would kill its in-flight
 * fetch on the very re-render it triggers, leaving the row stuck disabled
 * with the shades never drawn). A slow response for a previous code never
 * lands after a switch (guard ref); a failure yields {}.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchMarketRegimeSpans } from "@/lib/api-client";
import type { MaSpreadSecType, MarketRegimeSpans } from "@shared/types";

export function useRegimeSpans(code: string, secType: MaSpreadSecType) {
  const [regimeSpans, setRegimeSpans] = useState<Partial<MarketRegimeSpans> | null>(null);
  const [regimesLoading, setRegimesLoading] = useState(false);
  // Tracks the panel's current (secType, code) so a slow regime-span
  // response for a previous code never lands after a switch.
  const regimeCodeKeyRef = useRef(`${secType}:${code}`);
  useEffect(() => {
    regimeCodeKeyRef.current = `${secType}:${code}`;
  }, [code, secType]);
  // New code → the cached spans (and the in-flight flag) reset with the
  // panel's other per-code state.
  useEffect(() => {
    setRegimeSpans(null);
    setRegimesLoading(false);
  }, [code, secType]);

  // Fetch the spans ONCE per code — no-op while loaded or in flight.
  const ensureSpans = useCallback(() => {
    if (regimeSpans != null || regimesLoading) return;
    setRegimesLoading(true);
    const reqKey = `${secType}:${code}`;
    fetchMarketRegimeSpans(code, secType)
      .then((d) => {
        if (regimeCodeKeyRef.current === reqKey) setRegimeSpans(d.spans ?? {});
      })
      .catch(() => {
        if (regimeCodeKeyRef.current === reqKey) setRegimeSpans({});
      })
      .finally(() => {
        if (regimeCodeKeyRef.current === reqKey) setRegimesLoading(false);
      });
  }, [code, secType, regimeSpans, regimesLoading]);

  return { regimeSpans, regimesLoading, ensureSpans };
}
