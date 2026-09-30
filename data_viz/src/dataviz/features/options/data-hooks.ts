/**
 * Per-plot data hooks for the options dashboards — each plot loads its own
 SQL aggregate (one plot, one query; see api/services/options.service.ts
 * "Per-plot queries") instead of consuming the full-history /combined blob.
 *
 * The hooks are cancellation-safe (cancelled flags on unmount/re-run) and
 * ride the shared LRU-cached fetchJson layer, so repeated snapshots for the
 * same date dedupe client-side.
 */
import { useEffect, useState } from "react";
import {
  fetchOptionsDates,
  fetchOptionsSnapshot,
} from "@/lib/api-client/options";
import type { OptionsTargetType, OptionsVenue } from "@/lib/api-client/options";
import type { OptionsSnapshotResponse } from "@shared/types";

export interface OptionsDatesState {
  dates: string[];
  rowCount: number;
  contractCount: number;
  loading: boolean;
  error: string | null;
}

const EMPTY_DATES: OptionsDatesState = {
  dates: [],
  rowCount: 0,
  contractCount: 0,
  loading: false,
  error: null,
};

/** Distinct trading dates + counters for one underlying/venue (date picker,
 *  page caption, snapshot-date derivation). */
export function useOptionsDates(
  underlying: string,
  targetType?: OptionsTargetType,
  exchange?: OptionsVenue,
): OptionsDatesState {
  const [state, setState] = useState<OptionsDatesState>(EMPTY_DATES);
  useEffect(() => {
    if (!underlying) {
      setState(EMPTY_DATES);
      return;
    }
    let cancelled = false;
    setState({ ...EMPTY_DATES, loading: true });
    fetchOptionsDates(underlying, targetType, exchange)
      .then((resp) => {
        if (cancelled) return;
        setState({
          dates: resp.dates,
          rowCount: resp.row_count,
          contractCount: resp.contract_count,
          loading: false,
          error: null,
        });
      })
      .catch((e: Error) => {
        if (!cancelled) {
          setState({ ...EMPTY_DATES, loading: false, error: e.message });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [underlying, targetType, exchange]);
  return state;
}

/** One date's quote rows + unified strike/expiry-month axes (wall panel,
 *  smile panel, greeks panel). Cached per date by the shared LRU layer. */
export function useOptionsSnapshot(
  underlying: string,
  date: string,
  targetType?: OptionsTargetType,
  exchange?: OptionsVenue,
): { snapshot: OptionsSnapshotResponse | null; loading: boolean; error: string | null } {
  const [snapshot, setSnapshot] = useState<OptionsSnapshotResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!underlying || !date) {
      setSnapshot(null);
      setLoading(false);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchOptionsSnapshot(underlying, date, targetType, exchange)
      .then((resp) => {
        if (cancelled) return;
        setSnapshot(resp);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setSnapshot(null);
        setError(e.message);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [underlying, date, targetType, exchange]);
  return { snapshot, loading, error };
}
