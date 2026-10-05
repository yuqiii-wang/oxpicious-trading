/**
 * Per-plot data hooks for the options dashboards — each plot loads its own
 SQL aggregate (one plot, one query; see api/services/options.service.ts
 * "Per-plot queries") instead of consuming the full-history /combined blob.
 *
 * The hooks are cancellation-safe (cancelled flags on unmount/re-run) and
 * ride the shared LRU-cached fetchJson layer, so repeated snapshots for the
 * same date dedupe client-side. `reloadKey` (the Refresh button) re-runs
 * the fetch with the server-side aggregate cache bypassed.
 */
import { useEffect, useState } from "react";
import {
  fetchOptionsDates,
  fetchOptionsSnapshotRows,
  fetchOptionsSnapshotAxes,
  fetchOptionsSkewDaily,
} from "@/lib/api-client/options";
import type { OptionsTargetType, OptionsVenue } from "@/lib/api-client/options";
import type {
  OptionsRow,
  SkewDailyRow,
  OptionsSnapshotRowsResponse,
  OptionsSnapshotAxesResponse,
} from "@shared/types";

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
  reloadKey = 0,
): OptionsDatesState {
  const [state, setState] = useState<OptionsDatesState>(EMPTY_DATES);
  useEffect(() => {
    if (!underlying) {
      setState(EMPTY_DATES);
      return;
    }
    let cancelled = false;
    setState({ ...EMPTY_DATES, loading: true });
    fetchOptionsDates(underlying, targetType, exchange, { fresh: reloadKey > 0 })
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
  }, [underlying, targetType, exchange, reloadKey]);
  return state;
}

/** Selected date(s)' quote rows — one small index-narrow query per batch
 *  (wall panel, smile panel, greeks panel). Cached per date set by the
 *  shared LRU layer; historical batches freeze via the end_date param. */
export function useOptionsSnapshotRows(
  underlying: string,
  dates: string[],
  targetType?: OptionsTargetType,
  exchange?: OptionsVenue,
  reloadKey = 0,
): { rows: OptionsRow[]; loading: boolean; error: string | null } {
  const [resp, setResp] = useState<OptionsSnapshotRowsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const datesKey = [...dates].sort().join(",");
  useEffect(() => {
    if (!underlying || dates.length === 0) {
      setResp(null);
      setLoading(false);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchOptionsSnapshotRows(underlying, dates, targetType, exchange, {
      fresh: reloadKey > 0,
    })
      .then((r) => {
        if (cancelled) return;
        setResp(r);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setResp(null);
        setError(e.message);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [underlying, datesKey, targetType, exchange, reloadKey]);
  return { rows: resp?.rows ?? [], loading, error };
}

/** History-wide wall-panel axes (unified strikes + expiry months) — fetched
 *  once per underlying/venue (never varies with the selected date). */
export function useOptionsSnapshotAxes(
  underlying: string,
  targetType?: OptionsTargetType,
  exchange?: OptionsVenue,
  reloadKey = 0,
): { axes: OptionsSnapshotAxesResponse | null; loading: boolean; error: string | null } {
  const [axes, setAxes] = useState<OptionsSnapshotAxesResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!underlying) {
      setAxes(null);
      setLoading(false);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchOptionsSnapshotAxes(underlying, targetType, exchange, {
      fresh: reloadKey > 0,
    })
      .then((r) => {
        if (cancelled) return;
        setAxes(r);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setAxes(null);
        setError(e.message);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [underlying, targetType, exchange, reloadKey]);
  return { axes, loading, error };
}

/** Full-history per (date × real expiry) smile anchors — the Smile
 *  Chronology panel's own aggregate (both Today and History views), so the
 *  panel never needs the raw /combined blob regardless of venue (CFFEX
 *  underlyings ship ~200MB of JSON through it). */
export function useOptionsSkewDaily(
  underlying: string,
  targetType?: OptionsTargetType,
  exchange?: OptionsVenue,
  reloadKey = 0,
): { rows: SkewDailyRow[]; loading: boolean; error: string | null } {
  const [rows, setRows] = useState<SkewDailyRow[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!underlying) {
      setRows(null);
      setLoading(false);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchOptionsSkewDaily(underlying, targetType, exchange, {
      fresh: reloadKey > 0,
    })
      .then((r) => {
        if (cancelled) return;
        setRows(r.rows);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (!cancelled) {
          setRows(null);
          setError(e.message);
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [underlying, targetType, exchange, reloadKey]);
  return { rows: rows ?? [], loading, error };
}
