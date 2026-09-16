/**
 * useFuturesExt — fetches per-contract ext series (gap + correlation vs
 * underlying) for a product and exposes them as code→date→value maps ready
 * to be consumed by the chart option builders.
 */
import { useEffect, useMemo, useState } from "react";
import { fetchFuturesExt } from "@/lib/api-client";
import type { FuturesExtResponse } from "@/lib/api-client/analysis-futures";

function toNestedMap(
  record: Record<string, Record<string, number | null>>,
): Map<string, Map<string, number | null>> {
  const map = new Map<string, Map<string, number | null>>();
  for (const [code, byDate] of Object.entries(record)) {
    map.set(code, new Map(Object.entries(byDate)));
  }
  return map;
}

export function useFuturesExt(product: string) {
  const [extData, setExtData] = useState<FuturesExtResponse | null>(null);
  const [loadingExt, setLoadingExt] = useState(false);
  const [errorExt, setErrorExt] = useState<string | null>(null);

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

  // Gap + correlation maps for chartOption (code -> date -> value)
  const gapMap = useMemo(
    () => (extData ? toNestedMap(extData.gapByCodeDate) : undefined),
    [extData],
  );

  const corrMap = useMemo(
    () => (extData ? toNestedMap(extData.corrByCodeDate) : undefined),
    [extData],
  );

  return { extData, loadingExt, errorExt, gapMap, corrMap };
}
