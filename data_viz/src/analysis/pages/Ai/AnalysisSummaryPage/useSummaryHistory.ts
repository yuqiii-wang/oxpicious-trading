/** The page's Summary History layer — the two-phase run history (the
 *  date-event bar + a picked day's per-thread rows), the live-generation
 *  tracking (the auto-open + the mirrors), the server active-runs poll,
 *  the (dormant) AI event subscription, the open-graph collapse rules,
 *  and the Result view's final-result document fetch. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DateEvent } from "@/shared/components/date-events/DateEventStrip";
import type { SecNavSecType } from "@/shared/components/sec-nav";
import {
  deleteSummaryRun,
  fetchActiveSummaryRuns,
  fetchSummaryRunDates,
  fetchSummaryRuns,
  type AiActiveSummaryRun,
  type AiSummaryRun,
  type AiSummaryRunDate,
  type AiSummaryRunFilters,
} from "@/lib/api-client/aiSummary";
import { AI_EVENTS_FALLBACK_POLL_MS, subscribeAiEvents } from "@/lib/api-client/aiEvents";
import { useThreadDetail } from "../ThreadGraph/useThreadDetail";
import { dateUnsettled, hasUnsettledSteps } from "./helpers";

export interface SummaryHistoryOptions {
  /** The nav's sec_type — the strip filters' scope mode ("all" filters
   *  nothing). */
  secType: SecNavSecType;
  /** The picked subject codes — the strip filters' narrowing. */
  scopeCodes: string[];
  /** A generation is in flight (drives the strip re-read poll, the
   *  auto-open effect and the Result view's expectLive). */
  generating: boolean;
  /** The prompt view is open (the Result view's thread fetch pauses —
   *  the prompt mode owns the panel). */
  promptViewOpen: boolean;
  /** The page's error/notice surfaces (the search + delete paths write
   *  them). */
  setErrorMsg: React.Dispatch<React.SetStateAction<string | null>>;
  setNoticeMsg: React.Dispatch<React.SetStateAction<string | null>>;
}

/** The thread-graph view of one final-result payload — the runner's
 *  result node's fixed output document. */
export interface FinalResultPayload {
  answer?: string;
  error?: string;
}

export function useSummaryHistory({
  secType,
  scopeCodes,
  generating,
  promptViewOpen,
  setErrorMsg,
  setNoticeMsg,
}: SummaryHistoryOptions) {
  // ---- Summary History (the date-event bar over the planned runs) ----
  // TWO-PHASE load: the strip renders from the dates read alone (one
  // aggregate row per day — GET /summary-runs/dates, no per-thread rows)
  // and a dot click / live-open fetches that day's per-thread rows (GET
  // /summary-runs?date=) for the dropdown + the default pick — the full
  // per-thread aggregation over every thread never runs on page load.
  const [runDates, setRunDates] = useState<AiSummaryRunDate[]>([]);
  const [datesLoading, setDatesLoading] = useState(true);
  const [selectedRunDate, setSelectedRunDate] = useState<string | null>(null);
  // The run whose thread graph the strip's dot click opened (null until a
  // single-run day is clicked or a multi-run day's dropdown pick lands).
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null);
  // The picked day's per-thread rows (phase 2; empty when nothing open).
  const [dayRuns, setDayRuns] = useState<AiSummaryRun[]>([]);
  // The Summary History's search-by-thread-id box (the paper header's
  // right side): the typed prefix and its in-flight flag.
  const [threadSearch, setThreadSearch] = useState("");
  const [threadSearching, setThreadSearching] = useState(false);
  // Live generation tracking: the thread ids known when THIS generation
  // started (the click fetches the landing day's rows — the new run is
  // "the new, unsettled row" of that day) plus the date buckets' settled
  // state at the click (a bucket already unsettled before the click — a
  // crashed run's stuck thread — must never steal the fallback open),
  // and whether the graph has been auto-opened yet (once per generation
  // — a later update must not re-open after the user collapses it).
  const knownThreadsRef = useRef<Set<string>>(new Set());
  const preGenDatesRef = useRef<Map<string, boolean>>(new Map());
  const liveRunOpenedRef = useRef(false);
  // The in-flight run's thread id — known the instant the daemon
  // registers the run (the event channel's thread_started lands with
  // the click; the running planning node row persists in the same
  // instants, so the Summary History record exists immediately too).
  const [liveThreadId, setLiveThreadId] = useState<string | null>(null);
  // The generate state as the event handler sees it (the subscription
  // effect must not re-mount per generate flip).
  const generatingRef = useRef(false);
  // The thread the result panel currently shows (the event channel's
  // per-thread refreshes key on it).
  const openThreadIdRef = useRef<string | null>(null);
  // The picked day, mirrored for the event handler (the subscription
  // stays mounted across picks; its refreshes read the ref).
  const selectedRunDateRef = useRef<string | null>(null);
  // The result panel's view: the run's result document or its LangGraph
  // thread graph (merged into the panel behind this toggle). A
  // generation auto-switches to the graph and the finished answer
  // switches back; the strip's dot clicks land on the graph.
  const [resultView, setResultView] = useState<"langgraph" | "result">(
    "result",
  );
  // The event channel's graph-refresh signal: every daemon state event
  // for the OPEN thread bumps it (coalesced — a plan persist bursts one
  // event per step) and ThreadGraphView refetches its detail on the
  // bump instead of waiting out its 60s poll.
  const [graphSignal, setGraphSignal] = useState(0);
  const graphSignalTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // One coalesced graph-refresh bump (the dormant event handler's exact
  // pattern, extracted) — used by every stop path below: the canvas must
  // drop its Stop button + live chip the moment the run settles, not
  // wait out the 60s poll.
  const bumpGraphSignal = (delayMs = 250) => {
    if (graphSignalTimer.current !== null) {
      clearTimeout(graphSignalTimer.current);
    }
    graphSignalTimer.current = setTimeout(() => {
      graphSignalTimer.current = null;
      setGraphSignal((s) => s + 1);
    }, delayMs);
  };

  // ---- The Result view's document: the picked thread's kind
  // final-result context (the runner's HARDCODED result node's fixed
  // output — persisted with the run, so history runs opened from the
  // strip display their result too). The detail is fetched only while
  // the result view is shown (the LangGraph view polls its own copy);
  // when no thread is picked, the in-memory generate response is the
  // fallback (a plan-degraded run carries no thread).
  const resultThreadId = liveThreadId ?? selectedThreadId;
  const { detail: resultDetail, loading: resultLoading } = useThreadDetail(
    resultView === "result" && !promptViewOpen ? resultThreadId : null,
    generating,
  );
  const finalResultPayload = useMemo(() => {
    if (resultThreadId === null) return null;
    const ctx =
      resultDetail?.contexts.find((c) => c.kind === "final-result") ?? null;
    return ctx !== null
      ? (ctx.payload as FinalResultPayload)
      : null;
  }, [resultDetail, resultThreadId]);

  // The strip's nav filters — the scope pick scopes EVERY run-history
  // read (the dates, a day's rows, the live baseline) the same way: the
  // picked subjects narrow the runs to those scoped to ANY of them PLUS
  // the market-wide ones (the identity link's indexed EXISTS, OR no
  // identity row at all); an empty pick (or the "all" mode) shows every
  // run. secType rides only alongside codes — a bare sec_type filter has
  // no UI meaning and "all" is not a label_dim sec_type.
  const stripFilters = useCallback(
    (): AiSummaryRunFilters =>
      secType !== "all" && scopeCodes.length > 0
        ? { secType, codes: scopeCodes }
        : {},
    [secType, scopeCodes],
  );

  // Phase 1 — the date buckets (the whole strip's source; light enough
  // to re-read on every poll).
  const refreshDates = useCallback(() => {
    fetchSummaryRunDates(stripFilters())
      .then(setRunDates)
      .catch(() => setRunDates([]))
      .finally(() => setDatesLoading(false));
  }, [stripFilters]);

  useEffect(() => {
    refreshDates();
  }, [refreshDates]);

  // Phase 2 — the picked day's per-thread rows (the dropdown + the
  // default pick's source; an indexed one-day read).
  const loadDayRuns = useCallback(
    (date: string) => {
      fetchSummaryRuns({ ...stripFilters(), date })
        .then(setDayRuns)
        .catch(() => setDayRuns([]));
    },
    [stripFilters],
  );

  // Load (or clear) the day rows as the pick changes; the event/delete
  // handlers re-load the open day explicitly when its rows go stale.
  // The thread-id search loads its landing day ITSELF — unfiltered, since
  // a direct id lookup ignores the strip's scope pick — and marks the
  // date here so this effect's strip-filtered load doesn't clobber it
  // (consumed once: a later dot click on the same day loads normally).
  const searchLoadedDateRef = useRef<string | null>(null);
  useEffect(() => {
    if (selectedRunDate === null) {
      setDayRuns([]);
    } else if (
      searchLoadedDateRef.current !== null &&
      searchLoadedDateRef.current === selectedRunDate
    ) {
      searchLoadedDateRef.current = null;
    } else {
      loadDayRuns(selectedRunDate);
    }
  }, [selectedRunDate, loadDayRuns]);

  // When a day's rows land with no run picked yet (the dot-click path),
  // resolve the day's DEFAULT pick — its latest-time run (single-run
  // days: that run; multi-run days: the newest — the dropdown still
  // lists every run of the day for switching). A user dropdown pick (a
  // concrete id) and the live-open paths set the id themselves and are
  // never overridden.
  useEffect(() => {
    if (selectedRunDate === null || selectedThreadId !== null) return;
    if (dayRuns.length === 0) return;
    const sorted = [...dayRuns].sort((a, b) =>
      a.first_at.localeCompare(b.first_at),
    );
    setSelectedThreadId(sorted[sorted.length - 1].thread_id);
  }, [dayRuns, selectedRunDate, selectedThreadId]);

  // The post-mutation refresh: the strip's buckets + the picked day's
  // rows (a generate settle, a delete, the graph view's own settle all
  // land here). Reads the picked-day mirror, so the callback stays
  // stable across picks (the event subscription keeps its handler).
  const refreshHistory = useCallback(() => {
    refreshDates();
    const picked = selectedRunDateRef.current;
    if (picked !== null) loadDayRuns(picked);
  }, [refreshDates, loadDayRuns]);

  // ---- Live generation view (see the page header's LIVE GENERATION
  // VIEW) ---- While a run is in flight, re-read the strip's date
  // buckets at the AI_EVENTS_FALLBACK_POLL_MS cadence (60s — same
  // cadence as ThreadGraphView's own poll; the event channel that used
  // to make these instant is intentionally disabled, 2026-11-11) —
  // uncached reads, so the in-flight run's dot appears within one poll
  // of its plan persisting at run start. The read is the light per-day
  // aggregate, not the per-thread rows.
  useEffect(() => {
    if (!generating) return;
    const timer = setInterval(refreshDates, AI_EVENTS_FALLBACK_POLL_MS);
    return () => clearInterval(timer);
  }, [generating, refreshDates]);

  // The daemon's in-flight runs (see serverActiveRuns below): one
  // immediate read on mount + a 60s poll (AI_EVENTS_FALLBACK_POLL_MS —
  // the event channel that used to carry this is intentionally
  // disabled), ALWAYS on — a run started outside this page must flip
  // the toolbar's Stop button within a poll of the page being opened,
  // and the button must flip back when the run settles however it ends.
  // A failed/timeout read keeps the previous array (a transient daemon
  // restart must not blink the button).
  const [serverActiveRuns, setServerActiveRuns] = useState<
    AiActiveSummaryRun[]
  >([]);
  useEffect(() => {
    let mounted = true;
    const read = () => {
      fetchActiveSummaryRuns()
        .then((runs) => {
          if (mounted) setServerActiveRuns(runs);
        })
        .catch(() => {});
    };
    read();
    const timer = setInterval(read, AI_EVENTS_FALLBACK_POLL_MS);
    return () => {
      mounted = false;
      clearInterval(timer);
    };
  }, []);

  // The in-flight run's graph auto-opens once per generation: the first
  // date bucket that turned (or appeared) UNSETTLED after the click —
  // the bucket-level signature of the new run's plan persisting — then
  // that day's newest unknown unsettled row. The pre-click snapshot is
  // what makes "new" precise: a bucket already unsettled before the
  // click (an older thread stuck unsettled — a crashed daemon never
  // reached finalize) must never steal the opening. Latest first_at
  // wins among the day's candidates; ThreadGraphView then live-polls
  // the runner chain itself until the run settles.
  useEffect(() => {
    if (!generating || liveRunOpenedRef.current) return;
    const bucket = runDates.find(
      (d) =>
        dateUnsettled(d) && preGenDatesRef.current.get(d.run_date) !== true,
    );
    if (bucket === undefined) return;
    let cancelled = false;
    fetchSummaryRuns({ ...stripFilters(), date: bucket.run_date })
      .then((rows) => {
        if (cancelled || liveRunOpenedRef.current) return;
        const candidates = rows.filter(
          (r) =>
            hasUnsettledSteps(r) && !knownThreadsRef.current.has(r.thread_id),
        );
        if (candidates.length === 0) return;
        const live = candidates.reduce((a, b) =>
          a.first_at.localeCompare(b.first_at) < 0 ? b : a,
        );
        liveRunOpenedRef.current = true;
        setSelectedRunDate(live.run_date);
        setSelectedThreadId(live.thread_id);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [generating, runDates, stripFilters]);

  // The generation state mirrors for the event handler (the channel
  // subscription must not re-mount on every flip), and the open-thread
  // id + the picked day mirror for its per-thread refreshes.
  useEffect(() => {
    generatingRef.current = generating;
    if (!generating) setLiveThreadId(null);
  }, [generating]);
  useEffect(() => {
    openThreadIdRef.current = liveThreadId ?? selectedThreadId;
  }, [liveThreadId, selectedThreadId]);
  useEffect(() => {
    selectedRunDateRef.current = selectedRunDate;
  }, [selectedRunDate]);

  // ---- The AI event channel (see the page header's LIVE GENERATION
  // VIEW): the daemon's WebSocket stream through the bridge — was THE
  // primary path. 2026-11-11 the channel is INTENTIONALLY DISABLED
  // (AI_EVENTS_CHANNEL_DISABLED in aiEvents.ts — bypassed, not
  // removed): no socket is opened, so no frame ever arrives and the
  // handler below sits dormant. It STAYS for the re-enable day —
  // thread_started (the run's registration; the running planning node
  // row persists in the same instants) creates + loads the Summary
  // History record on the click; the state events for the open thread
  // pull its graph forward immediately; thread_settled lands the final
  // strip counts. Until then the polls are the only refresh path: the
  // generating-strip + active-runs loops above and ThreadGraphView's
  // own 60s poll back the whole graph view. ----
  useEffect(() => {
    const unsubscribe = subscribeAiEvents((ev) => {
      if (ev.type === "thread_started") {
        // OUR new run (a generation in flight, first unknown id, opened
        // once): create the record, load the run. Another client's run
        // surfaces through the ordinary strip refresh instead.
        if (
          !generatingRef.current ||
          liveRunOpenedRef.current ||
          typeof ev.thread_id !== "string" ||
          knownThreadsRef.current.has(ev.thread_id)
        ) {
          return;
        }
        liveRunOpenedRef.current = true;
        setLiveThreadId(ev.thread_id);
        setResultView("langgraph");
        // The strip's date bucket for a run that just started is the
        // current UTC day (the row's run_date is the GMT day of its
        // first state — the same instant, so the same date).
        setSelectedRunDate(new Date().toISOString().slice(0, 10));
        setSelectedThreadId(ev.thread_id);
        // The planning node row already exists — this read creates the
        // click's Summary History record (event date + time): the strip's
        // buckets, plus the landing day's rows for the dropdown.
        refreshDates();
        loadDayRuns(new Date().toISOString().slice(0, 10));
        return;
      }
      if (ev.type === "thread_deleted") {
        // A run entry was deleted (this or another client — the bridge
        // broadcasts on the DELETE's success): the strip re-reads (and
        // the picked day's rows with it), and an open graph of the
        // deleted thread collapses. The push IS the refresh — every read
        // here is no-store, but a settled page's poll loop has stopped,
        // so without this frame the page would hold the deleted thread's
        // strip counts and graph forever. Collapse, not re-pick
        // (idempotent against the local deleteRun's own collapse).
        refreshHistory();
        if (
          typeof ev.thread_id === "string" &&
          ev.thread_id === openThreadIdRef.current
        ) {
          setSelectedRunDate(null);
          setSelectedThreadId(null);
        }
        return;
      }
      if (ev.type === "thread_settled") {
        // Final counts for the strip (and the picked day's dropdown
        // rows) AND one last graph refresh — the settle is the
        // definitive "the canvas is final now" marker (belt for a settle
        // burst the debounce window could have straddled, and for
        // watching-without-generating views where the poll loop may
        // already have stopped). The still-pending generate POST
        // resolves about now and flips the view itself.
        refreshHistory();
        if (ev.thread_id === openThreadIdRef.current ||
            ev.super_thread_id === openThreadIdRef.current) {
          if (graphSignalTimer.current !== null) {
            clearTimeout(graphSignalTimer.current);
          }
          graphSignalTimer.current = setTimeout(() => {
            graphSignalTimer.current = null;
            setGraphSignal((s) => s + 1);
          }, 250);
        }
        return;
      }
      if (
        (ev.type === "state_created" || ev.type === "state_updated" ||
          ev.type === "node_finished" ||
          ev.type === "states_settled" || ev.type === "result_recorded") &&
        (ev.thread_id === openThreadIdRef.current ||
          ev.super_thread_id === openThreadIdRef.current)
      ) {
        // A tick on the OPEN RUN — pull its graph forward now
        // (coalesced: a plan persist bursts one event per step). The
        // match is by the chain root OR any member thread: the
        // three-graph chain publishes its custom/result segments'
        // node events under their own thread ids (stamped with the
        // root on the frame), so every segment's update lands here —
        // not just the plan segment's.
        if (graphSignalTimer.current !== null) {
          clearTimeout(graphSignalTimer.current);
        }
        graphSignalTimer.current = setTimeout(() => {
          graphSignalTimer.current = null;
          setGraphSignal((s) => s + 1);
        }, 250);
      }
    });
    return unsubscribe;
  }, [refreshDates, loadDayRuns, refreshHistory]);

  // A nav pick change invalidates the open run graph (the selected thread
  // may not be in the narrowed list) — collapse bar + graph. No-op on
  // mount (nothing open yet). Keyed on the flattened pick so the effect
  // does not re-run per render.
  const scopePickKey = scopeCodes.join("|");
  useEffect(() => {
    setSelectedRunDate(null);
    setSelectedThreadId(null);
  }, [secType, scopePickKey]);

  // ---- Date-event bar: one dot per day that has planned runs ----
  // The buckets ARE the phase-1 read — the server aggregates per day and
  // returns them newest-first; the strip derives its axis range from the
  // array's ends, so map in CHRONOLOGICAL order (the read's DESC order
  // would invert the axis and drop the newest days off it entirely).
  const runEvents: DateEvent[] = useMemo(
    () =>
      [...runDates]
        .sort((a, b) => a.run_date.localeCompare(b.run_date))
        .map((d) => ({
          date: d.run_date,
          id: d.run_date,
          type: "runs",
          title: `${d.runs_total} run${d.runs_total > 1 ? "s" : ""}`,
          detail:
            `${d.steps_completed} step${d.steps_completed === 1 ? "" : "s"} completed` +
            (d.steps_failed ? ` · ${d.steps_failed} failed` : "") +
            (d.steps_skipped ? ` · ${d.steps_skipped} skipped` : ""),
        })),
    [runDates],
  );

  // A dot click opens the day's run graph: the day's rows load on the
  // pick (phase 2) and the resolve effect defaults the thread to the
  // day's latest-time run; multi-run days then show the dropdown for
  // switching; clicking the selected dot again collapses bar + graph.
  // The merged result panel follows the pick on its LangGraph view.
  const handleRunDotClick = useCallback(
    (e: DateEvent) => {
      const date = String(e.id);
      if (date === selectedRunDate) {
        setSelectedRunDate(null);
        setSelectedThreadId(null);
        return;
      }
      setSelectedRunDate(date);
      setSelectedThreadId(null);
      setResultView("langgraph");
    },
    [selectedRunDate],
  );

  const runsOnSelectedDate = useMemo(
    () =>
      [...dayRuns].sort((a, b) => a.first_at.localeCompare(b.first_at)),
    [dayRuns],
  );

  // The Summary History's search-by-thread-id: a direct lookup (the read
  // carries no nav/date filters — a pasted id must find its run whatever
  // the strip scopes to), opening the matched run's thread graph — the
  // newest when the prefix hits several. The landing day's dropdown rows
  // load unfiltered for the same reason, marked in searchLoadedDateRef so
  // the pick effect above doesn't re-load them with the strip's filters.
  const searchRunByThreadId = useCallback(async () => {
    const q = threadSearch.trim();
    if (q === "" || threadSearching) return;
    setThreadSearching(true);
    try {
      const rows = await fetchSummaryRuns({ threadId: q });
      if (rows.length === 0) {
        setErrorMsg(`No run found for thread id “${q}”`);
        return;
      }
      const picked = rows[0];
      searchLoadedDateRef.current = picked.run_date;
      setSelectedRunDate(picked.run_date);
      setSelectedThreadId(picked.thread_id);
      setResultView("langgraph");
      fetchSummaryRuns({ date: picked.run_date })
        .then(setDayRuns)
        .catch(() => setDayRuns([]));
      if (rows.length > 1) {
        setNoticeMsg(`${rows.length} runs match “${q}” — opened the newest`);
      }
    } catch (err) {
      setErrorMsg(
        `Thread search failed: ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
    } finally {
      setThreadSearching(false);
    }
  }, [threadSearch, threadSearching, setErrorMsg, setNoticeMsg]);

  // The dropdown's hover-delete cross: purge the run's thread server-side
  // and follow the pick — the deleted run's open graph collapses (the
  // thread_deleted broadcast does the same on every OTHER open page);
  // the menu itself stays open so several junk runs can go in one
  // sitting. Collapse, not re-pick: the broadcast echo can land either
  // side of this handler, and both doing the same idempotent collapse
  // keeps the pick consistent.
  const deleteRun = useCallback(
    async (threadId: string) => {
      try {
        await deleteSummaryRun(threadId);
      } catch (err) {
        setErrorMsg(
          `Failed to delete the run: ${
            err instanceof Error ? err.message : String(err)
          }`,
        );
        return;
      }
      refreshHistory();
      if (selectedThreadId === threadId) {
        setSelectedRunDate(null);
        setSelectedThreadId(null);
      }
    },
    [refreshHistory, selectedThreadId, setErrorMsg],
  );

  return {
    runDates,
    datesLoading,
    selectedRunDate,
    selectedThreadId,
    setSelectedThreadId,
    threadSearch,
    setThreadSearch,
    threadSearching,
    searchRunByThreadId,
    runEvents,
    handleRunDotClick,
    runsOnSelectedDate,
    deleteRun,
    liveThreadId,
    resultView,
    setResultView,
    graphSignal,
    refreshHistory,
    bumpGraphSignal,
    resultThreadId,
    resultDetail,
    resultLoading,
    finalResultPayload,
    serverActiveRuns,
    stripFilters,
    refreshDates,
    loadDayRuns,
    knownThreadsRef,
    preGenDatesRef,
    liveRunOpenedRef,
    generatingRef,
  };
}

export type SummaryHistory = ReturnType<typeof useSummaryHistory>;
