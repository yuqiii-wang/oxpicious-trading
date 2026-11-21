/**
 * useForecastRunPoll — owns the Run Forecast process state for the
 * Forecast page (the single-tag sibling of the Singleton page's
 * useProcessStatusPoll).
 *
 * The run POST resolves at the SUBMIT (the async-submit contract), so
 * this hook is the single source of truth for "is the forecast
 * pipeline running": it polls the enriched process-status endpoint for
 * the forecast tag and reports the active→inactive exit edge to the
 * page (the reload / failure-tail trigger). Cadence mirrors the
 * singleton hook: 60s idle, 5s while the process runs.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchStrategyProcessStatus,
  type StrategyProcessStatus,
} from "@/lib/api-client";

const STATUS_POLL_IDLE_MS = 60_000;
const STATUS_POLL_ACTIVE_MS = 5_000;

interface Options {
  /** The forecast process-id-tag ("" disables the poll). */
  tag: string;
  /** The tag's exit edge: the process's exit_code (null when
   *  unknowable — an adopted run or an evicted tag). */
  onSettled: (exitCode: number | null) => void;
}

export interface ForecastRunPoll {
  /** True while the forecast pipeline holds its tag (the Run button's
   *  spinner + the in-progress alert). */
  active: boolean;
  /** True while a status fetch is in flight. */
  refreshing: boolean;
  /** Poll once right away (manual refresh / just-submitted). */
  refresh: () => void;
}

export function useForecastRunPoll({ tag, onSettled }: Options): ForecastRunPoll {
  const [active, setActive] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshSeq, setRefreshSeq] = useState(0);

  const wasActiveRef = useRef(false);
  const onSettledRef = useRef(onSettled);
  useEffect(() => { onSettledRef.current = onSettled; }, [onSettled]);

  // New tag identity → drop the previous latch + active flag.
  useEffect(() => {
    wasActiveRef.current = false;
    setActive(false);
  }, [tag]);

  const poll = useCallback(async () => {
    if (!tag) return;
    setRefreshing(true);
    try {
      const status = await fetchStrategyProcessStatus([tag]);
      const entry: StrategyProcessStatus | undefined = status[tag];
      const now = entry?.running === true;
      setActive(now);
      // The exit edge fires only when a poll previously SAW the tag
      // active (unknown tags answer running:false — a fresh mount never
      // fires a stale settle).
      if (now) {
        wasActiveRef.current = true;
      } else if (wasActiveRef.current) {
        wasActiveRef.current = false;
        onSettledRef.current(entry?.exit_code ?? null);
      }
    } catch {
      /* status poll is best-effort */
    } finally {
      setRefreshing(false);
    }
  }, [tag]);

  useEffect(() => {
    void poll();
  }, [poll, refreshSeq]);

  useEffect(() => {
    if (!tag) return;
    const timer = setInterval(poll, active ? STATUS_POLL_ACTIVE_MS : STATUS_POLL_IDLE_MS);
    return () => clearInterval(timer);
  }, [poll, active, tag]);

  const refresh = useCallback(() => setRefreshSeq((s) => s + 1), []);

  return { active, refreshing, refresh };
}
