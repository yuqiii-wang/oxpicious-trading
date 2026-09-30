/**
 * useSecBaselineBundle — fetch ONE security's baseline bundle (IndexBundle |
 * EtfBundle | StockBundle) through the SHARED /api/sec-baseline endpoint —
 * one URL per (sec_type, code) for the shared baseline-plot component
 * (IndexPanel / EtfMarginPanel / StockPanel).
 *
 * Extracted 2026-09-29 from the verbatim-duplicated effect in PePanel +
 * DividendsPanel; consolidated again onto the shared API path (the former
 * version branched over the three page-scoped combined browse endpoints —
 * shared component ⇒ shared data path).
 */
import { useEffect, useState } from "react";
import { fetchSecBaseline } from "@/lib/api-client";
import type {
  IndexBundle,
  EtfBundle,
  StockBundle,
  PeSecType,
} from "@shared/types";

export type SecBaselineBundle = IndexBundle | EtfBundle | StockBundle;

export interface SecBaselineBundleState {
  bundle: SecBaselineBundle | null;
  loading: boolean;
  error: string | null;
}

export function useSecBaselineBundle(
  code: string,
  secType: PeSecType,
): SecBaselineBundleState {
  const [bundle, setBundle] = useState<SecBaselineBundle | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fetch the security baseline bundle on mount and whenever code/sec_type
  // changes.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setBundle(null);
    fetchSecBaseline(secType, code)
      .then((r) => {
        if (cancelled) return;
        setBundle(r.bundle);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [code, secType]);

  return { bundle, loading, error };
}
