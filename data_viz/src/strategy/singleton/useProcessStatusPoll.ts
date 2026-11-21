/**
 * useProcessStatusPoll — owns the run/train process state for the
 * Singleton Strategy page.
 *
 * The run/train POSTs resolve at the SUBMIT (2026-11-18 async submit —
 * they no longer block until the Python process exits), so this hook is
 * the single source of truth for "is a backend process running for the
 * current identity": it polls the enriched process-status endpoint
 * (running / exit_code per process-id-tag) and reports the active→
 * inactive exit edges to the page.
 *
 * Cadence (user-directed 2026-11-18): 60s while idle — the default
 * auto-check — dropping to 5s while either tag is active so the exit
 * edge (and the data reload / failure-tail fetch it triggers) lands
 * promptly. `refresh()` polls once right away (the status RefreshButton
 * and the just-submitted handlers use it).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchStrategyProcessStatus,
  type StrategyProcessStatus,
} from "@/lib/api-client";

/** Idle auto-check interval (user-directed default, 2026-11-18). */
const STATUS_POLL_IDLE_MS = 60_000;
/** Active interval — a run/train process is in flight; poll fast so the
 *  completion edge is caught (and the data reloaded) without a long lag. */
const STATUS_POLL_ACTIVE_MS = 5_000;

interface Options {
  /** The run/train process-id-tags for the CURRENT identity ("" when no
   *  code is selected — the hook then idles without polling). */
  runTag: string;
  trainTag: string;
  /** The run tag's exit edge: the process's exit_code (null when
   *  unknowable — an adopted run or an evicted tag). */
  onRunSettled: (exitCode: number | null) => void;
  /** Same for the train tag (covers train + its spawned final run —
   *  one job). */
  onTrainSettled: (exitCode: number | null) => void;
}

export interface ProcessStatusPoll {
  /** True while a run/train process holds its tag (drives the button
   *  spinners and the in-progress alerts). */
  runActive: boolean;
  trainActive: boolean;
  /** True while a status fetch is in flight (the RefreshButton's
   *  spinner — also blips on each auto-check). */
  refreshing: boolean;
  /** Poll once right away (manual refresh / just-submitted). */
  refresh: () => void;
}

export function useProcessStatusPoll({
  runTag,
  trainTag,
  onRunSettled,
  onTrainSettled,
}: Options): ProcessStatusPoll {
  const [runActive, setRunActive] = useState(false);
  const [trainActive, setTrainActive] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshSeq, setRefreshSeq] = useState(0);

  // Edge latches + settled callbacks live in refs: the latches reset on
  // a tag-identity change (the effect below), and the callbacks stay
  // current without rescheduling the poll loop.
  const wasRunRef = useRef(false);
  const wasTrainRef = useRef(false);
  const onRunSettledRef = useRef(onRunSettled);
  const onTrainSettledRef = useRef(onTrainSettled);
  useEffect(() => { onRunSettledRef.current = onRunSettled; }, [onRunSettled]);
  useEffect(() => { onTrainSettledRef.current = onTrainSettled; }, [onTrainSettled]);

  // New identity → drop the previous identity's latches and active
  // flags (declared before the polling effects so it runs first).
  useEffect(() => {
    wasRunRef.current = false;
    wasTrainRef.current = false;
    setRunActive(false);
    setTrainActive(false);
  }, [runTag, trainTag]);

  const poll = useCallback(async () => {
    if (!runTag || !trainTag) return;
    setRefreshing(true);
    try {
      const status = await fetchStrategyProcessStatus([runTag, trainTag]);
      const run: StrategyProcessStatus | undefined = status[runTag];
      const train: StrategyProcessStatus | undefined = status[trainTag];
      const runNow = run?.running === true;
      const trainNow = train?.running === true;
      setRunActive(runNow);
      setTrainActive(trainNow);
      // Exit edges — fire only when a poll previously SAW the tag
      // active (unknown tags answer running:false, same as the old
      // boolean endpoint, so a fresh mount never fires a stale settle).
      if (runNow) {
        wasRunRef.current = true;
      } else if (wasRunRef.current) {
        wasRunRef.current = false;
        onRunSettledRef.current(run?.exit_code ?? null);
      }
      if (trainNow) {
        wasTrainRef.current = true;
      } else if (wasTrainRef.current) {
        wasTrainRef.current = false;
        onTrainSettledRef.current(train?.exit_code ?? null);
      }
    } catch {
      /* status poll is best-effort */
    } finally {
      setRefreshing(false);
    }
  }, [runTag, trainTag]);

  // Immediate poll on mount / identity change / manual refresh.
  useEffect(() => {
    void poll();
  }, [poll, refreshSeq]);

  // Adaptive cadence: the interval is armed by the ACTIVE state (the
  // useOffsetCorrRefresh pattern) — 60s idle, 5s while a process runs.
  const anyActive = runActive || trainActive;
  useEffect(() => {
    if (!runTag || !trainTag) return;
    const timer = setInterval(poll, anyActive ? STATUS_POLL_ACTIVE_MS : STATUS_POLL_IDLE_MS);
    return () => clearInterval(timer);
  }, [poll, anyActive, runTag, trainTag]);

  const refresh = useCallback(() => setRefreshSeq((s) => s + 1), []);

  return { runActive, trainActive, refreshing, refresh };
}
