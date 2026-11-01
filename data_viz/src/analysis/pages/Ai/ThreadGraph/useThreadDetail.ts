/**
 * useThreadDetail — the Thread Graph view's poll loop: fetch one thread's
 * graph now, then keep polling every few seconds while the run is live
 * (any state pending/in_progress — threadIsActive). A settled thread is
 * fetched once (finalize settles every state, so the graph is final) and
 * the timer stops; a threadId switch resets immediately.
 *
 * `expectLive` keeps the loop alive while detail is still null (the
 * thread route 404s until the runner's plan rows persist): a generation
 * in flight registers its thread id on the daemon the moment it starts,
 * so the graph opens on the click and fills in when the plan lands.
 * Once expectLive drops (the generation ended) a still-null thread
 * stops the loop after one last poll.
 *
 * `refreshSignal` is the out-of-band push path: the AI event channel
 * (aiEvents.ts — the daemon's WS stream) bumps it on every
 * state-created/updated event for the open thread, and each bump
 * fetches NOW instead of waiting out the poll interval (coalesced at
 * the source). The poll loop remains the fallback for a silent channel.
 */
import { useEffect, useRef, useState } from "react";
import {
  fetchAgentThread,
  threadIsActive,
  type AgentThreadDetail,
} from "@/lib/api-client/aiThreads";

const POLL_MS = 3000;

export interface ThreadDetailState {
  detail: AgentThreadDetail | null;
  loading: boolean;
  error: string | null;
  /** Server-time of the last successful fetch's states (polling indicator). */
  fetchedAt: Date | null;
}

export function useThreadDetail(
  threadId: string | null,
  expectLive = false,
  refreshSignal = 0,
): ThreadDetailState {
  const [detail, setDetail] = useState<AgentThreadDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fetchedAt, setFetchedAt] = useState<Date | null>(null);
  // The live flag the interval is keyed on: keep polling until a fetch
  // reports the thread settled. Ref so the timer callback sees the value
  // the latest fetch produced, not the render-time snapshot.
  const activeRef = useRef(true);
  // expectLive rides a ref so flipping it mid-poll doesn't reset the loop.
  const expectLiveRef = useRef(expectLive);
  useEffect(() => {
    expectLiveRef.current = expectLive;
  }, [expectLive]);
  // The poll loop's movable parts, shared with the refreshSignal effect:
  // the pending timer (a push fetch cancels it — the fetch's own finally
  // reschedules) and the fetch itself (a push while one is in flight is
  // dropped; the in-flight fetch's finally re-arms the poll within one
  // interval).
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const tickRef = useRef<(() => Promise<void>) | null>(null);
  const busyRef = useRef(false);

  useEffect(() => {
    setDetail(null);
    setError(null);
    setFetchedAt(null);
    activeRef.current = true;
    if (timerRef.current !== null) clearTimeout(timerRef.current);
    timerRef.current = null;
    tickRef.current = null;
    busyRef.current = false;
    if (threadId === null) return;

    let cancelled = false;
    // Spinner only until this thread's first payload lands — later polls
    // are background refreshes (the chart stays mounted).
    let first = true;

    const tick = async () => {
      if (cancelled || busyRef.current) return;
      busyRef.current = true;
      if (first) setLoading(true);
      try {
        const next = await fetchAgentThread(threadId);
        if (cancelled) return;
        setDetail(next);
        setError(null);
        setFetchedAt(new Date());
        // null (404 — plan rows not persisted yet) keeps polling only
        // while the parent expects the run to be live.
        activeRef.current =
          next !== null ? threadIsActive(next) : expectLiveRef.current;
      } catch (err) {
        if (!cancelled) {
          setError(String(err instanceof Error ? err.message : err));
          activeRef.current = true; // transient failure — keep trying
        }
      } finally {
        if (!cancelled) {
          first = false;
          setLoading(false);
          if (activeRef.current) {
            timerRef.current = setTimeout(tick, POLL_MS);
          }
        }
      }
    };
    tickRef.current = tick;

    void tick();
    return () => {
      cancelled = true;
      if (timerRef.current !== null) clearTimeout(timerRef.current);
      timerRef.current = null;
      tickRef.current = null;
    };
  }, [threadId]);

  // The event-channel push: fetch immediately, unconditionally on a
  // bump — the LAST event of a run (result_recorded / thread_settled)
  // can land after the previous fetch already saw all states settled
  // (activeRef false, the poll loop stopping), and skipping it would
  // leave the canvas one refresh stale (e.g. the result circle still
  // "waiting"). Signals only fire on real daemon events for this
  // thread, so the extra fetches are bounded. refreshSignal 0 is the
  // mount value, never a push.
  useEffect(() => {
    if (refreshSignal === 0 || threadId === null) return;
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    void tickRef.current?.();
  }, [refreshSignal, threadId]);

  return { detail, loading, error, fetchedAt };
}
