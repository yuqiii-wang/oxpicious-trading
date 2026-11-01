/**
 * AnalysisSummaryPage — the AI section's body (the former "Analysis
 * Summary"/"Thread Graph" tab strip is gone; the thread graph now lives
 * in the central result panel behind the LangGraph/Result toggle):
 * the classification-nav strip on top (sec_type toggle — all / industry /
 * index / etf / stock — plus the shared sector→industry + strategy→theme
 * navigator and the L3 security chips; "all" = the locked exchange-only
 * nav whose run scope stays absent, i.e. market-wide), then two horizontal
 * foldables on
 * the left — Scopes (the analysis-item tree) and Prompts (the skills +
 * system-prompt slots; each folds into a slim vertical strip, a click
 * expands it to the right, and both stretch to the summary card's
 * height) — and the central result panel toggling between the run's
 * RESULT document and its LangGraph thread graph. The Result view reads
 * the picked thread's kind final-result context (the runner's
 * HARDCODED result node's fixed output — persisted with the run, so a
 * history run opened from the strip displays its result document too);
 * the in-memory generate response is the fallback only when the run
 * left no thread (plan degraded / market-wide no-plan run).
 *
 * Generate posts the selection — the analysis items (key + label +
 * insight identity), the picked skills- and plan-type text.ai_prompts
 * prompt_key values, and the nav's scope pick (sec_type + code /
 * industry id; absent = market-wide) — as JSON to POST /api/ai/summary,
 * which runs the deep-agents daemon (read-only DB tool loop) and returns
 * the markdown document; the run's plan steps live only in the thread
 * graph (auto-selected by the toggle while it runs — see LIVE
 * GENERATION VIEW; the finished answer flips the toggle back to
 * Result). Beneath the classification nav, the Summary History section
 * shows the shared DateEventStrip (the date-event bar) over the planned
 * deep-agent runs (GET /api/ai/summary-runs, one dot per day with runs).
 * Clicking a dot opens that run's thread graph in the result panel
 * (ThreadGraphView): single-run days open it directly, multi-run days
 * first show a dropdown to pick the run; clicking the selected dot
 * again collapses the pick.
 * The strip follows the classification-nav pick: a selected code narrows
 * the runs to those scoped to it (text.deep_agent_identities — the
 * code <-> thread_id link the runner's plan node writes; a market-wide
 * pick shows every run), and changing the pick collapses any open graph.
 * The scope focuses the agent's metric
 * queries on the picked subject (see the SUMMARY_GOAL contract in
 * services/ai-summary.service.ts).
 *
 * While the run is in flight the Generate button turns into Stop on
 * hover (a disabled button ignores mouse events, so the hover is
 * tracked on the wrapper box): clicking it POSTs /api/ai/summary/stop,
 * the daemon cancels the langgraph thread, and the still-pending
 * generate resolves stopped — the auto-opened thread graph then shows
 * the settled thread (the in-progress step failed "stopped by user",
 * the never-started ones skipped).
 *
 * LIVE GENERATION VIEW (event channel): the daemon announces every run
 * and state mutation on its WebSocket stream (llm_agents/
 * deep_agents/events.py → daemon /ws → the bridge's /api/ai/events →
 * aiEvents.ts — one shared browser socket with reconnect). The click's
 * thread_started event is the PRIMARY open path: the daemon registers
 * the run and persist its RUNNING PLANNING NODE (an ordinal-0 'plan'
 * state row, in_progress) in the same instants, so the handler creates
 * the click's Summary History record (refreshRuns finds the row —
 * event date + time) and loads the run's graph, which opens straight
 * onto the running planning node (no 404 wait: the thread read has its
 * first row from the first moment). State events for the open thread
 * bump a refresh signal that refetches its graph immediately
 * (coalesced); thread_settled lands the final strip counts. The strip's
 * own 3s re-read while generating stays as the FALLBACK open path (a
 * silent channel — bridge restart mid-run) — its row-based effect
 * yields to the event path (liveRunOpenedRef). Once per generation: a
 * run opened this way stays put even if an older thread is still stuck
 * unsettled, and the user can collapse it freely.
 *
 * PROMPT MODE: clicking a prompt's row in the Prompts picker (besides
 * ticking its checkbox) swaps the main panel to that prompt's content
 * view (PromptContent — title + type/version/key line, note, and the
 * instruction text). The Generate button is replaced by Edit; Save PUTs
 * the edited instruction (PUT /api/ai/prompts/:prompt_key), the server
 * replaces the row's text in place and bumps its version by one, and
 * the picker + view follow the refreshed row. Clicking the open row
 * again (or a failed-save-free Cancel) returns to the summary view.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  IconButton,
  MenuItem,
  Paper,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import {
  Cancel,
  Close,
  Edit,
  PlayArrow,
  Save,
  Stop,
} from "@mui/icons-material";
import CodeSearchBar from "@/components/CodeSearchBar";
import RefreshButton from "@/components/RefreshButton";
import DateEventStrip, {
  type DateEvent,
} from "@/shared/components/date-events/DateEventStrip";
import MarkdownView from "@/shared/markdown/MarkdownView";
import { MUTED_PALETTE } from "@/theme/chart-palette";
import SecClassificationNav from "@/shared/components/sec-classification/SecClassificationNav";
import { SecTypeToggle, useSecNav } from "@/shared/components/sec-nav";
import type { SecNavSecType } from "@/shared/components/sec-nav";
import {
  deleteSummaryRun,
  fetchAiPrompts,
  fetchSummaryRuns,
  generateAnalysisSummary,
  stopAnalysisSummary,
  updateAiPrompt,
  type AiPrompt,
  type AiSummaryRun,
} from "@/lib/api-client/aiSummary";
import { subscribeAiEvents } from "@/lib/api-client/aiEvents";
import AnalysisItemsTree from "./AnalysisItemsTree";
import PromptContent from "./PromptContent";
import SkillsPromptsPanel from "./SkillsPromptsPanel";
import ThreadGraphView from "./ThreadGraph";
import { runLabel } from "./ThreadGraph/runLabel";
import { useThreadDetail } from "./ThreadGraph/useThreadDetail";
import {
  ALL_ANALYSIS_ITEM_KEYS,
  ANALYSIS_CATEGORIES,
  ASK_INSIGHT_BY_ITEM,
  SUMMARY_PLACEHOLDER_MD,
  SUMMARY_SEC_TYPES,
  SUMMARY_THEMES_SOURCES,
  type AnalysisCategoryEntry,
} from "./constants";

/** itemKind per sec_type (the SecNavShell mapping); "all" has no L3 row. */
function itemKindFor(
  secType: SecNavSecType,
): "ETF" | "Index" | "Stock" | "Industry" | undefined {
  return secType === "etf"
    ? "ETF"
    : secType === "index"
      ? "Index"
      : secType === "industry"
        ? "Industry"
        : secType === "stock"
          ? "Stock"
          : undefined;
}

/** The picker's selectable prompt types — the skills approach
 *  instructions and the plan prompt (system rows stay unmanaged). */
function isSelectablePrompt(prompt: AiPrompt): boolean {
  return prompt.prompt_type_label === "skills" || prompt.prompt_type_label === "plan";
}

/** A run row with steps that are neither completed/failed/skipped —
 *  the in-flight signature (the runner inserts the plan rows pending
 *  at run start; finalize settles the leftovers either way). */
function hasUnsettledSteps(r: AiSummaryRun): boolean {
  return r.steps_total > r.steps_completed + r.steps_failed + r.steps_skipped;
}

export default function AnalysisSummaryPage() {
  // ---- Scope nav (classification-nav kit; the pick focuses the run) ----
  const nav = useSecNav({
    themesSources: SUMMARY_THEMES_SOURCES,
    defaultSecType: "index",
    dataLabel: "summary scope",
  });

  // "Select all" default — the seed set is the full leaf universe.
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(
    () => new Set(ALL_ANALYSIS_ITEM_KEYS),
  );
  // Prompts come from text.ai_prompts (GET /api/ai/prompts); the picker
  // lists the selectable rows (skills + plan) and the first load selects
  // every one.
  const [prompts, setPrompts] = useState<AiPrompt[]>([]);
  const [promptsLoading, setPromptsLoading] = useState(true);
  const [selectedPrompts, setSelectedPrompts] = useState<Set<string>>(
    () => new Set<string>(),
  );
  const promptsLoadedRef = useRef(false);

  const [generating, setGenerating] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  // Neutral feedback (the stop took effect) — the info Alert beside the
  // error one.
  const [noticeMsg, setNoticeMsg] = useState<string | null>(null);
  const [summaryMd, setSummaryMd] = useState<string | null>(null);
  // Hover over the generator button — while generating, hovering turns
  // it into Stop. Tracked on the wrapper box because a DISABLED button
  // (the generating state) swallows mouse events.
  const [generateHovered, setGenerateHovered] = useState(false);

  // ---- Prompt view (the main panel's prompt mode) ----
  // The opened prompt (clicking its picker row, not the checkbox) — the
  // main panel shows its content with Edit/Save in place of Generate.
  const [openPromptKey, setOpenPromptKey] = useState<string | null>(null);
  const [editingPrompt, setEditingPrompt] = useState(false);
  const [draftInstruction, setDraftInstruction] = useState("");
  const [savingPrompt, setSavingPrompt] = useState(false);
  const openPrompt =
    prompts.find((p) => p.prompt_key === openPromptKey) ?? null;

  // ---- Summary History (the date-event bar over the planned runs) ----
  const [runs, setRuns] = useState<AiSummaryRun[]>([]);
  const [runsLoading, setRunsLoading] = useState(true);
  const [selectedRunDate, setSelectedRunDate] = useState<string | null>(null);
  // The run whose thread graph the strip's dot click opened (null until a
  // single-run day is clicked or a multi-run day's dropdown pick lands).
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null);
  // Live generation tracking: the thread ids known when THIS generation
  // started (so the in-flight run is "the new, unsettled row") and
  // whether its graph has been auto-opened yet (once per generation —
  // a later update must not re-open after the user collapses it).
  const knownThreadsRef = useRef<Set<string>>(new Set());
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
  // bump instead of waiting out its 3s poll.
  const [graphSignal, setGraphSignal] = useState(0);
  const graphSignalTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ---- The Result view's document: the picked thread's kind
  // final-result context (the runner's HARDCODED result node's fixed
  // output — persisted with the run, so history runs opened from the
  // strip display their result too). The detail is fetched only while
  // the result view is shown (the LangGraph view polls its own copy);
  // when no thread is picked, the in-memory generate response is the
  // fallback (a plan-degraded run carries no thread).
  const resultThreadId = liveThreadId ?? selectedThreadId;
  const { detail: resultDetail, loading: resultLoading } = useThreadDetail(
    resultView === "result" && openPrompt === null ? resultThreadId : null,
    generating,
  );
  const finalResultPayload = useMemo(() => {
    if (resultThreadId === null) return null;
    const ctx =
      resultDetail?.contexts.find((c) => c.kind === "final-result") ?? null;
    return ctx !== null
      ? (ctx.payload as { answer?: string; error?: string })
      : null;
  }, [resultDetail, resultThreadId]);

  const refreshRuns = useCallback(() => {
    // The strip follows the nav pick: a selected code narrows the runs to
    // those scoped to it (the identity link's indexed lookup); a
    // market-wide pick (no code — including the "all" mode) shows every
    // run. secType rides only alongside a code — a bare sec_type filter
    // has no UI meaning and "all" is not a label_dim sec_type.
    fetchSummaryRuns(
      nav.searchCode
        ? { secType: nav.secType, code: nav.searchCode }
        : undefined,
    )
      .then(setRuns)
      .catch(() => setRuns([]))
      .finally(() => setRunsLoading(false));
  }, [nav.secType, nav.searchCode]);

  useEffect(() => {
    refreshRuns();
  }, [refreshRuns]);

  // ---- Live generation view (see the header's LIVE GENERATION VIEW) ----
  // While a run is in flight, re-read the strip every 3s (same cadence
  // as ThreadGraphView's own poll) — uncached reads, so the in-flight
  // run's dot appears the moment its plan persists at run start.
  useEffect(() => {
    if (!generating) return;
    const timer = setInterval(refreshRuns, 3000);
    return () => clearInterval(timer);
  }, [generating, refreshRuns]);

  // The in-flight run's graph auto-opens once per generation: the first
  // NEW run row (not in the pre-generation snapshot) whose steps are not
  // settled yet. The known-ids snapshot is what makes "new" precise — an
  // older thread stuck unsettled (a crashed daemon never reached
  // finalize) must never steal the opening. Latest first_at wins in the
  // unlikely case of several unknown rows; ThreadGraphView then live-
  // polls the runner chain itself until the run settles.
  useEffect(() => {
    if (!generating || liveRunOpenedRef.current) return;
    const candidates = runs.filter(
      (r) => hasUnsettledSteps(r) && !knownThreadsRef.current.has(r.thread_id),
    );
    if (candidates.length === 0) return;
    const live = candidates.reduce((a, b) =>
      a.first_at.localeCompare(b.first_at) < 0 ? b : a,
    );
    liveRunOpenedRef.current = true;
    setSelectedRunDate(live.run_date);
    setSelectedThreadId(live.thread_id);
  }, [generating, runs]);

  // The generation state mirrors for the event handler (the channel
  // subscription must not re-mount on every flip), and the open-thread
  // id mirrors for its per-thread refreshes.
  useEffect(() => {
    generatingRef.current = generating;
    if (!generating) setLiveThreadId(null);
  }, [generating]);
  useEffect(() => {
    openThreadIdRef.current = liveThreadId ?? selectedThreadId;
  }, [liveThreadId, selectedThreadId]);

  // ---- The AI event channel (see the header's LIVE GENERATION VIEW):
  // the daemon's WebSocket stream through the bridge — THE primary
  // path. thread_started (the run's registration; the running planning
  // node row persists in the same instants) creates + loads the
  // Summary History record on the click; the state events for the open
  // thread pull its graph forward immediately; thread_settled lands
  // the final strip counts. The row-based effect above stays as the
  // fallback for a silent channel (bridge restart mid-run), and
  // ThreadGraphView's own 3s poll backs the whole graph view. ----
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
        // click's Summary History record (event date + time).
        refreshRuns();
        return;
      }
      if (ev.type === "thread_deleted") {
        // A run entry was deleted (this or another client — the bridge
        // broadcasts on the DELETE's success): the strip re-reads, and
        // an open graph of the deleted thread collapses. The push IS
        // the refresh — every read here is no-store, but a settled
        // page's poll loop has stopped, so without this frame the page
        // would hold the deleted thread's strip counts and graph
        // forever. Collapse, not re-pick (idempotent against the local
        // deleteRun's own collapse).
        refreshRuns();
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
        // Final counts for the strip AND one last graph refresh — the
        // settle is the definitive "the canvas is final now" marker
        // (belt for a settle burst the debounce window could have
        // straddled, and for watching-without-generating views where
        // the poll loop may already have stopped). The still-pending
        // generate POST resolves about now and flips the view itself.
        refreshRuns();
        if (ev.thread_id === openThreadIdRef.current) {
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
        ev.thread_id === openThreadIdRef.current
      ) {
        // A tick on the OPEN thread — pull its graph forward now
        // (coalesced: a plan persist bursts one event per step).
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
  }, [refreshRuns]);

  // A nav pick change invalidates the open run graph (the selected thread
  // may not be in the narrowed list) — collapse bar + graph. No-op on
  // mount (nothing open yet).
  useEffect(() => {
    setSelectedRunDate(null);
    setSelectedThreadId(null);
  }, [nav.secType, nav.searchCode]);

  useEffect(() => {
    let cancelled = false;
    fetchAiPrompts()
      .then((rows) => {
        if (cancelled) return;
        setPrompts(rows);
        if (!promptsLoadedRef.current) {
          promptsLoadedRef.current = true;
          setSelectedPrompts(
            new Set(rows.filter(isSelectablePrompt).map((p) => p.prompt_key)),
          );
        }
      })
      .catch(() => {
        if (!cancelled) setPrompts([]);
      })
      .finally(() => {
        if (!cancelled) setPromptsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const toggleItem = (key: string, checked: boolean) => {
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (checked) {
        next.add(key);
      } else {
        next.delete(key);
      }
      return next;
    });
  };

  const toggleCategory = (category: AnalysisCategoryEntry, checked: boolean) => {
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      for (const item of category.items) {
        const key = `${category.slug}/${item.slug}`;
        if (checked) {
          next.add(key);
        } else {
          next.delete(key);
        }
      }
      return next;
    });
  };

  const toggleAll = (checked: boolean) => {
    setSelectedKeys(checked ? new Set(ALL_ANALYSIS_ITEM_KEYS) : new Set());
  };

  const togglePrompt = (key: string, checked: boolean) => {
    setSelectedPrompts((prev) => {
      const next = new Set(prev);
      if (checked) {
        next.add(key);
      } else {
        next.delete(key);
      }
      return next;
    });
  };

  // Open/close the prompt content view (clicking its picker row again
  // closes); opening always lands in read mode.
  const openPromptByKey = (key: string | null) => {
    setOpenPromptKey(key);
    setEditingPrompt(false);
    setSavingPrompt(false);
  };

  const startPromptEdit = () => {
    if (!openPrompt) return;
    setDraftInstruction(openPrompt.instruction);
    setEditingPrompt(true);
  };

  // The prompt view's Save: PUT the edited instruction — the server
  // replaces the row's text in place and bumps its version by one; the
  // refreshed row swaps into state (the picker + the open view follow).
  const savePromptEdit = async () => {
    if (!openPrompt) return;
    setSavingPrompt(true);
    setErrorMsg(null);
    try {
      const updated = await updateAiPrompt(
        openPrompt.prompt_key,
        draftInstruction.trim(),
      );
      setPrompts((prev) =>
        prev.map((p) => (p.prompt_key === updated.prompt_key ? updated : p)),
      );
      setEditingPrompt(false);
      setNoticeMsg(
        `Saved "${updated.title}" — now at version ${updated.version}`,
      );
    } catch (err) {
      setErrorMsg(
        `Failed to save the prompt: ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
    } finally {
      setSavingPrompt(false);
    }
  };

  // The scope pick: the nav's L3 selection (or code-search pick), typed
  // per sec_type — a security code, or an industry id in industry mode.
  // "all" mode has neither — the scope stays ABSENT (market-wide).
  const scopeCode = nav.searchCode;
  const scopeName = scopeCode ? nav.findItemName(scopeCode) : undefined;
  const scopeLabel = scopeCode
    ? `${itemKindFor(nav.secType)} ${scopeCode}${scopeName ? ` ${scopeName}` : ""}`
    : nav.secType === "all"
      ? "all (market-wide)"
      : "market-wide";

  // ---- Date-event bar: one dot per day that has planned runs ----
  const runEvents: DateEvent[] = useMemo(() => {
    const byDate = new Map<
      string,
      { total: number; completed: number; failed: number; skipped: number }
    >();
    for (const r of runs) {
      const acc =
        byDate.get(r.run_date) ??
        { total: 0, completed: 0, failed: 0, skipped: 0 };
      acc.total += 1;
      acc.completed += r.steps_completed;
      acc.failed += r.steps_failed;
      acc.skipped += r.steps_skipped;
      byDate.set(r.run_date, acc);
    }
    return Array.from(byDate.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([date, s]) => ({
        date,
        id: date,
        type: "runs",
        title: `${s.total} run${s.total > 1 ? "s" : ""}`,
        detail:
          `${s.completed} step${s.completed === 1 ? "" : "s"} completed` +
          (s.failed ? ` · ${s.failed} failed` : "") +
          (s.skipped ? ` · ${s.skipped} skipped` : ""),
      }));
  }, [runs]);

  // A dot click opens the day's run graph: single-run days resolve the
  // thread immediately, multi-run days leave it null until the dropdown
  // pick; clicking the selected dot again collapses bar + graph. The
  // merged result panel follows the pick on its LangGraph view.
  const handleRunDotClick = useCallback(
    (e: DateEvent) => {
      const date = String(e.id);
      if (date === selectedRunDate) {
        setSelectedRunDate(null);
        setSelectedThreadId(null);
        return;
      }
      setSelectedRunDate(date);
      setResultView("langgraph");
      // The date's DEFAULT event is its latest-time run (single-run days:
      // that run; multi-run days: the newest by first_at — the dropdown
      // still lists every run of the day for switching).
      const dayRuns = runs
        .filter((r) => r.run_date === date)
        .sort((a, b) => a.first_at.localeCompare(b.first_at));
      setSelectedThreadId(
        dayRuns.length > 0 ? dayRuns[dayRuns.length - 1].thread_id : null,
      );
    },
    [runs, selectedRunDate],
  );

  const runsOnSelectedDate = useMemo(
    () =>
      selectedRunDate === null
        ? []
        : runs
            .filter((r) => r.run_date === selectedRunDate)
            .sort((a, b) => a.first_at.localeCompare(b.first_at)),
    [runs, selectedRunDate],
  );

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
      refreshRuns();
      if (selectedThreadId === threadId) {
        setSelectedRunDate(null);
        setSelectedThreadId(null);
      }
    },
    [refreshRuns, selectedThreadId],
  );

  const generate = async () => {
    // The live view's baseline: whatever threads exist now, so the run
    // about to start is identifiable as the first new unsettled row.
    knownThreadsRef.current = new Set(runs.map((r) => r.thread_id));
    liveRunOpenedRef.current = false;
    setGenerating(true);
    // The event handler may see thread_started before the mirroring
    // effect below runs — the click itself claims the generation.
    generatingRef.current = true;
    // The run's thread graph is what to watch while it executes.
    setResultView("langgraph");
    setErrorMsg(null);
    setNoticeMsg(null);
    try {
      const items = ANALYSIS_CATEGORIES.flatMap((category) =>
        category.items
          .map((item) => ({ category, item }))
          .filter(({ item }) => selectedKeys.has(`${category.slug}/${item.slug}`))
          .map(({ category, item }) => ({
            key: `${category.slug}/${item.slug}`,
            label: item.title,
            description: item.description,
            insight: ASK_INSIGHT_BY_ITEM[`${category.slug}/${item.slug}`],
          })),
      );
      const promptKeys = prompts
        .filter(isSelectablePrompt)
        .map((p) => p.prompt_key)
        .filter((key) => selectedPrompts.has(key));
      const res = await generateAnalysisSummary({
        analysisItems: items,
        prompts: promptKeys.length > 0 ? promptKeys : undefined,
        scope: scopeCode && nav.secType !== "all"
          ? {
              secType: nav.secType,
              ...(nav.secType === "industry"
                ? { industry: scopeCode }
                : { code: scopeCode }),
              name: scopeName,
            }
          : undefined,
      });
      if (res.stopped) {
        // The Stop click won: the daemon cancelled the thread and its
        // settled plan rides the auto-opened thread graph above.
        setNoticeMsg("Summary generation stopped");
        refreshRuns();
        return;
      }
      if (!res.success) {
        setErrorMsg(res.error ?? "Summary generation failed");
        return;
      }
      // The thread's final-result context (the result node's fixed
      // output, written before finalize) is the Result view's document;
      // summaryMd is only the fallback for a run that left no thread.
      setSummaryMd(res.answer);
      setResultView("result");
      // The run just settled — its thread joins the history strip.
      refreshRuns();
    } catch (err) {
      setErrorMsg(String(err instanceof Error ? err.message : err));
    } finally {
      setGenerating(false);
    }
  };

  // The generating button's hover identity: asks the server to cancel
  // the run; the still-pending generate request then resolves stopped
  // and flips the button back — nothing else to do here.
  const stopGeneration = async () => {
    try {
      await stopAnalysisSummary();
    } catch (err) {
      setErrorMsg(
        `Failed to stop the generation: ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
    }
  };

  return (
    <Box>
      {/* ---- Scope picker: sec_type toggle + code search + refresh ---- */}
      <Box sx={{ display: "flex", alignItems: "center", gap: 1.5, flexWrap: "wrap", mb: 1 }}>
        <SecTypeToggle
          value={nav.secType}
          onChange={nav.setSecType}
          options={SUMMARY_SEC_TYPES}
        />
        {nav.secType !== "all" && (
          <CodeSearchBar
            activeCode={nav.searchCode}
            onSearch={nav.handleSearch}
            onClear={nav.handleClearSearch}
            placeholder={
              nav.secType === "industry"
                ? "Industry id (e.g. BANKS)"
                : nav.secType === "etf"
                  ? "ETF code (e.g. 510050)"
                  : nav.secType === "stock"
                    ? "Stock code (e.g. 600000)"
                    : "Index code (e.g. 000300)"
            }
          />
        )}
        <RefreshButton
          onClick={nav.refresh}
          loading={nav.loading}
          label="Refresh"
          tooltip="Refresh classification trees (bypass cache)"
        />
        <Typography variant="caption" color="text.secondary" sx={{ ml: "auto" }}>
          Summary scope: {scopeLabel}
        </Typography>
      </Box>

      {/* ---- Classification nav ("all" = the locked exchange-only row;
           otherwise the scope pick comes from its L3 row) ---- */}
      <SecClassificationNav
        allMode={nav.secType === "all"}
        sectors={nav.sectors}
        sectorId={nav.sectorId}
        industrySlug={nav.industrySlug}
        exchange={nav.exchange}
        onSectorChange={nav.handleSectorChange}
        onIndustryChange={nav.handleIndustryChange}
        onExchangeChange={nav.handleExchangeChange}
        strategies={nav.strategies}
        strategyId={nav.strategyId}
        themeSlug={nav.themeSlug}
        onStrategyChange={nav.handleStrategyChange}
        onThemeChange={nav.handleThemeChange}
        itemKind={itemKindFor(nav.secType)}
        selectedItemCode={nav.searchCode}
        onItemSelected={nav.onItemSelected}
        onClearItemSelection={nav.onClearItemSelection}
        loading={nav.loading}
      />
      {nav.error && (
        <Alert severity="error" variant="filled" sx={{ mb: 2 }}>
          Failed to load classification: {nav.error}
        </Alert>
      )}

      {/* ---- Summary History: the date-event bar + the picked run's
           thread graph (the former Thread Graph tab, folded in) ---- */}
      <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
        <Typography variant="caption" color="text.secondary">
          Summary History
          {scopeCode ? ` — scoped to ${scopeLabel}` : " — all runs"}
        </Typography>
        <DateEventStrip
          events={runEvents}
          typeMeta={{
            runs: { label: "Summary runs", color: MUTED_PALETTE[0] },
          }}
          selectedId={selectedRunDate}
          onEventClick={handleRunDotClick}
          height={72}
          showLegend={false}
          emptyText={
            runsLoading
              ? "Loading run history…"
              : scopeCode
                ? "No runs scoped to this subject yet — generate one with the scope pick and a plan prompt."
                : "No planned runs yet — generate a summary with a plan prompt."
          }
          enableZoom
          defaultWindowDays={92}
        />
        {selectedRunDate !== null && runsOnSelectedDate.length > 1 && (
          <TextField
            select
            size="small"
            label={`Runs on ${selectedRunDate}`}
            value={selectedThreadId ?? ""}
            onChange={(e) => {
              setSelectedThreadId(e.target.value);
              setResultView("langgraph");
            }}
            SelectProps={{
              // The closed field shows just the picked run's label — the
              // delete cross belongs to the dropdown's options only
              // (MUI would otherwise clone the selected option's children,
              // cross button included, into the closed display).
              renderValue: (picked) => {
                const pickedRun = runsOnSelectedDate.find(
                  (r) => r.thread_id === String(picked),
                );
                return pickedRun ? runLabel(pickedRun) : String(picked);
              },
            }}
            sx={{ mt: 1, minWidth: 340, maxWidth: 520 }}
            helperText="Several runs that day — pick one to open its thread graph"
          >
            {runsOnSelectedDate.map((r) => (
              <MenuItem
                key={r.thread_id}
                value={r.thread_id}
                sx={{
                  // The delete cross rides at the option's tail and only
                  // shows while the pointer is on the option.
                  "& .run-delete": { visibility: "hidden" },
                  "&:hover .run-delete": { visibility: "visible" },
                }}
              >
                <Box
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    gap: 1,
                    width: "100%",
                    minWidth: 0,
                  }}
                >
                  <Typography variant="inherit" noWrap sx={{ flex: 1, minWidth: 0 }}>
                    {runLabel(r)}
                  </Typography>
                  <IconButton
                    className="run-delete"
                    size="small"
                    edge="end"
                    aria-label="Delete run"
                    disabled={r.thread_id === liveThreadId}
                    onClick={(e) => {
                      // Not a pick — purge the run and keep the menu
                      // open instead of selecting it.
                      e.stopPropagation();
                      void deleteRun(r.thread_id);
                    }}
                  >
                    <Close fontSize="small" />
                  </IconButton>
                </Box>
              </MenuItem>
            ))}
          </TextField>
        )}
      </Paper>

      <Box
        sx={{
          display: "flex",
          gap: 2,
          alignItems: "stretch",
          flexWrap: { xs: "wrap", md: "nowrap" },
        }}
      >
        <AnalysisItemsTree
          selectedKeys={selectedKeys}
          onToggleItem={toggleItem}
          onToggleCategory={toggleCategory}
          onToggleAll={toggleAll}
        />
        <SkillsPromptsPanel
          prompts={prompts.filter(isSelectablePrompt)}
          loading={promptsLoading}
          selectedPrompts={selectedPrompts}
          onTogglePrompt={togglePrompt}
          openPromptKey={openPromptKey}
          onOpenPrompt={openPromptByKey}
        />
        <Paper variant="outlined" sx={{ p: 3, minWidth: 0, flexGrow: 1 }}>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 1,
              mb: 2,
            }}
          >
            <Box>
              {errorMsg !== null && (
                <Alert severity="error" onClose={() => setErrorMsg(null)}>
                  {errorMsg}
                </Alert>
              )}
              {noticeMsg !== null && (
                <Alert severity="info" onClose={() => setNoticeMsg(null)}>
                  {noticeMsg}
                </Alert>
              )}
            </Box>
            {/* LangGraph / Result toggle — the merged run-observability
                view vs the generated document (prompt mode hides it). */}
            {!openPrompt && (
              <ToggleButtonGroup
                exclusive
                size="small"
                value={resultView}
                onChange={(_, v) => v !== null && setResultView(v)}
                sx={{ flexShrink: 0 }}
              >
                <ToggleButton value="langgraph">LangGraph</ToggleButton>
                <ToggleButton value="result">Result</ToggleButton>
              </ToggleButtonGroup>
            )}
            {openPrompt ? (
              // Prompt mode — the content view's actions replace the
              // Generate button: Edit to start, Save (version +1) / Cancel
              // while editing.
              editingPrompt ? (
                <Box sx={{ display: "flex", gap: 1, flexShrink: 0 }}>
                  <Button
                    startIcon={<Cancel />}
                    disabled={savingPrompt}
                    onClick={() => setEditingPrompt(false)}
                  >
                    Cancel
                  </Button>
                  <Button
                    variant="contained"
                    startIcon={
                      savingPrompt ? (
                        <CircularProgress size={16} color="inherit" />
                      ) : (
                        <Save />
                      )
                    }
                    disabled={
                      savingPrompt ||
                      draftInstruction.trim() === "" ||
                      draftInstruction.trim() === openPrompt.instruction
                    }
                    onClick={savePromptEdit}
                  >
                    Save
                  </Button>
                </Box>
              ) : (
                <Button
                  variant="outlined"
                  startIcon={<Edit />}
                  onClick={startPromptEdit}
                  sx={{ flexShrink: 0 }}
                >
                  Edit
                </Button>
              )
            ) : (
              <Box
                onMouseEnter={() => setGenerateHovered(true)}
                onMouseLeave={() => setGenerateHovered(false)}
                sx={{ flexShrink: 0 }}
              >
                <Button
                  variant="contained"
                  color={generating && generateHovered ? "error" : "primary"}
                  startIcon={
                    generating ? (
                      generateHovered ? (
                        <Stop />
                      ) : (
                        <CircularProgress size={16} color="inherit" />
                      )
                    ) : (
                      <PlayArrow />
                    )
                  }
                  disabled={
                    generating ? !generateHovered : selectedKeys.size === 0
                  }
                  onClick={generating ? stopGeneration : generate}
                >
                  {generating
                    ? generateHovered
                      ? "Stop"
                      : "Generating…"
                    : "Generate"}
                </Button>
              </Box>
            )}
          </Box>
          {openPrompt ? (
            <PromptContent
              prompt={openPrompt}
              editing={editingPrompt}
              draft={draftInstruction}
              onDraftChange={setDraftInstruction}
            />
          ) : resultView === "langgraph" ? (
            <ThreadGraphView
              threadId={liveThreadId ?? selectedThreadId}
              expectLive={generating}
              onSettled={refreshRuns}
              refreshSignal={graphSignal}
            />
          ) : finalResultPayload !== null ? (
            finalResultPayload.error !== undefined ? (
              <Alert severity="error">
                This run recorded a failure instead of a result:{" "}
                {finalResultPayload.error}
              </Alert>
            ) : (
              <MarkdownView source={finalResultPayload.answer ?? ""} />
            )
          ) : resultThreadId !== null ? (
            <Typography variant="body2" color="text.secondary" sx={{ py: 2 }}>
              {resultDetail === null && (resultLoading || generating)
                ? "Waiting for the run's result…"
                : "This run left no recorded result — it was stopped before the agent finished, or it predates result recording."}
            </Typography>
          ) : summaryMd !== null ? (
            <MarkdownView source={summaryMd} />
          ) : (
            <MarkdownView source={SUMMARY_PLACEHOLDER_MD} />
          )}
        </Paper>
      </Box>
    </Box>
  );
}
