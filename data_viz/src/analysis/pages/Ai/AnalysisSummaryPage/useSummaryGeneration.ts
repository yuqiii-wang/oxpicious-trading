/** The generation lifecycle — the Generate dialog confirm's run POST,
 *  its pre-confirm state sync, and the two stop paths (the toolbar's
 *  live-run stop and the ThreadGraph canvas' chain-root stop). */
import type { Dispatch, SetStateAction } from "react";
import {
  generateAnalysisSummary,
  stopAnalysisSummary,
  fetchSummaryRuns,
  type AiPrompt,
  type AiSummaryRun,
  type AiSummaryScope,
  type PromptLabelSets,
} from "@/lib/api-client/aiSummary";
import type { GenerateQueryValues } from "../GenerateQueryDialog";
import { dateUnsettled, isSelectablePrompt } from "./helpers";
import type { SummaryHistory } from "./useSummaryHistory";

/** The refs + callbacks the generation handlers share with the history
 *  layer (a Pick of useSummaryHistory's return). */
type SummaryHistorySlice = Pick<
  SummaryHistory,
  | "runDates"
  | "stripFilters"
  | "knownThreadsRef"
  | "preGenDatesRef"
  | "liveRunOpenedRef"
  | "generatingRef"
  | "refreshHistory"
  | "bumpGraphSignal"
  | "liveThreadId"
  | "serverActiveRuns"
  | "setResultView"
>;

export interface SummaryGenerationOptions {
  history: SummaryHistorySlice;
  /** The request's scope mode ("all" → null = market-wide). */
  scopeSecType: AiSummaryScope["secType"] | null;
  /** code → display name + the suffix-insensitive key (the request's
   *  scope entries' name field). */
  subjectNameMap: Map<string, string>;
  subjectKey: (code: string) => string;
  setScopeCodes: Dispatch<SetStateAction<string[]>>;
  prompts: AiPrompt[];
  selectedPrompts: Set<string>;
  setSelectedPrompts: Dispatch<SetStateAction<Set<string>>>;
  goalPrompts: AiPrompt[];
  promptLabelOverrides: Record<string, PromptLabelSets>;
  planTemplateId: number | null;
  setGenerateDialogOpen: Dispatch<SetStateAction<boolean>>;
  setForcedTables: Dispatch<SetStateAction<string[]>>;
  setGenerating: Dispatch<SetStateAction<boolean>>;
  setSummaryMd: Dispatch<SetStateAction<string | null>>;
  setErrorMsg: Dispatch<SetStateAction<string | null>>;
  setNoticeMsg: Dispatch<SetStateAction<string | null>>;
}

export function useSummaryGeneration({
  history,
  scopeSecType,
  subjectNameMap,
  subjectKey,
  setScopeCodes,
  prompts,
  selectedPrompts,
  setSelectedPrompts,
  goalPrompts,
  promptLabelOverrides,
  planTemplateId,
  setGenerateDialogOpen,
  setForcedTables,
  setGenerating,
  setSummaryMd,
  setErrorMsg,
  setNoticeMsg,
}: SummaryGenerationOptions) {
  // The run starts from the Generate dialog's confirm — its query text
  // rides as the focus instruction, its subject tags as the scope codes
  // and its goal pick as the effective goal key (all passed explicitly:
  // the confirm's state updates land on the NEXT render, so this
  // closure must not read them back).
  const generate = async (
    question: string,
    codes: string[],
    goalKey: string | null,
    forcedTableNames: string[],
  ) => {
    // The click's feedback is INSTANT: the live view (the thread graph's
    // optimistic planning canvas — the running planning node) flips in
    // this frame, before any fetch. The pre-click date-bucket snapshot
    // is synchronous state (the fallback open's "new bucket" test), and
    // the landing day's row set — the new run is "the first NEW
    // unsettled row" against it — follows one indexed day read later;
    // the event channel's thread_started opens the graph without
    // waiting for it.
    history.liveRunOpenedRef.current = false;
    history.preGenDatesRef.current = new Map(
      history.runDates.map((d) => [d.run_date, dateUnsettled(d)]),
    );
    setGenerating(true);
    // The event handler may see thread_started before the mirroring
    // effect below runs — the click itself claims the generation.
    history.generatingRef.current = true;
    // The run's thread graph is what to watch while it executes.
    history.setResultView("langgraph");
    setErrorMsg(null);
    setNoticeMsg(null);
    const today = new Date().toISOString().slice(0, 10);
    const baseline = await fetchSummaryRuns({
      ...history.stripFilters(),
      date: today,
    }).catch(() => [] as AiSummaryRun[]);
    history.knownThreadsRef.current = new Set(baseline.map((r) => r.thread_id));
    try {
      // The SELECTED TABLES are the run's item list (2026-11-11 — the
      // Scopes panel's nested tick tree replaced the analysis-item
      // tree): one section per ticked table, the table name the key and
      // label. The SAME list rides forced_tables, so the KG surface
      // (kg_search / kg_expand / the data hints) serves exactly what
      // the document walks.
      const items = forcedTableNames.map((table) => ({
        key: table,
        label: table,
      }));
      // The goal pick applied on top of the panel selection — the
      // confirm's selectedPrompts update is not visible to this closure
      // yet, so the dialog's key rides explicitly (clear the other goal
      // rows, add the picked one; null = Auto, every goal row cleared).
      const effectiveSelected = new Set(selectedPrompts);
      for (const p of goalPrompts) effectiveSelected.delete(p.prompt_key);
      if (goalKey !== null) effectiveSelected.add(goalKey);
      const promptKeys = prompts
        .filter(isSelectablePrompt)
        .map((p) => p.prompt_key)
        .filter((key) => effectiveSelected.has(key));
      // The session's run-only label changes ride the request — the
      // agent applies them (and the force-load vocabulary they carry)
      // for THIS run only.
      const labelOverrides =
        Object.keys(promptLabelOverrides).length > 0 ? promptLabelOverrides : undefined;
      const res = await generateAnalysisSummary({
        analysisItems: items,
        prompts: promptKeys.length > 0 ? promptKeys : undefined,
        promptLabels: labelOverrides,
        // The dialog's query text — the run's focus instruction the
        // planning session / plan template's {{question}} carries
        // (empty = absent, the planning runs unprompted).
        question: question !== "" ? question : undefined,
        // The dialog's subject tags ride as the scope list — every
        // picked code one AiSummaryScope entry (industry ids in industry
        // mode); empty or "all" sends none (market-wide).
        scopes:
          scopeSecType !== null && codes.length > 0
            ? codes.map((code) => ({
                secType: scopeSecType,
                ...(scopeSecType === "industry"
                  ? { industry: code }
                  : { code }),
                name: subjectNameMap.get(subjectKey(code)),
              }))
            : undefined,
        // The dialog's forced-table scope — the run's KG surface serves
        // only these tables (the serving layer filters structurally);
        // empty sends none (no limitation).
        forcedTables:
          forcedTableNames.length > 0 ? forcedTableNames : undefined,
        // A picked template bypasses the LLM planning node — its steps
        // materialize with THIS run's scope/question args.
        planTemplateId: planTemplateId ?? undefined,
      });
      if (res.stopped) {
        // The Stop click won: the daemon cancelled the thread and its
        // settled plan rides the auto-opened thread graph above. Pull
        // the graph forward now AND after the async settle has had time
        // to land — the stop POST resolves before the cancelled run's
        // cancellation handler finishes settling the chain rows, so the
        // canvas must not wait out its 60s poll holding the Stop button
        // + live chip on a thread that is already stopped.
        setNoticeMsg("Summary generation stopped");
        history.refreshHistory();
        history.bumpGraphSignal();
        history.bumpGraphSignal(2500);
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
      history.setResultView("result");
      // The run just settled — its thread joins the history strip.
      history.refreshHistory();
    } catch (err) {
      setErrorMsg(String(err instanceof Error ? err.message : err));
    } finally {
      setGenerating(false);
    }
  };

  // The pre-run popup's confirm: sync the dialog's four knobs into the
  // page state — the plan goal into the prompt selection (the same
  // exclusive set the Prompts panel's radios drive), the codes into the
  // scope tags (the history-strip filter + the reactive goal effect
  // follow the new pick), the tables into the forced-table scope —
  // then start the run with the dialog's query text. Cancel never
  // lands here: the panels keep their prior state.
  const handleGenerateConfirm = (values: GenerateQueryValues) => {
    setGenerateDialogOpen(false);
    setSelectedPrompts((prev) => {
      const next = new Set(prev);
      for (const p of goalPrompts) next.delete(p.prompt_key);
      if (values.goalKey !== null) next.add(values.goalKey);
      return next;
    });
    setScopeCodes(values.codes);
    setForcedTables(values.forcedTables);
    void generate(
      values.question,
      values.codes,
      values.goalKey,
      values.forcedTables,
    );
  };

  // The Stop button's handler: asks the server to cancel the live run
  // — by thread id when the page knows one (the active-runs poll or
  // this page's own live thread), else the thread-less "every active
  // run" form. A page-started run's still-pending generate request
  // then resolves stopped and flips the button back; a foreign run's
  // flip comes from the active-runs poll draining.
  const stopGeneration = async () => {
    const knownThreadId =
      history.liveThreadId ?? history.serverActiveRuns[0]?.thread_id ?? undefined;
    try {
      await stopAnalysisSummary(knownThreadId);
      // The toolbar stop must drop the watched graph's Stop button +
      // live chip now AND once the async settle lands (the stop POST
      // resolves before the cancelled run finishes settling its rows) —
      // the event channel that used to push this is disabled, so these
      // bumps ARE the refresh path.
      history.bumpGraphSignal();
      history.bumpGraphSignal(2500);
    } catch (err) {
      setErrorMsg(
        `Failed to stop the generation: ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
    }
  };

  // The Thread Graph canvas' own Stop (ThreadGraphView onStop): stops
  // the CHAIN ROOT the canvas is showing — a viewed history run the
  // daemon still has live (or this page's own), even when the toolbar
  // button still reads Generate (its gating is the 60s active-runs
  // poll, not the canvas). Same endpoint as stopGeneration; the view
  // owns the button's busy state and its post-stop refresh. The stop
  // POST resolves only AFTER the daemon confirmed the cancellation (the
  // unwind + the whole-chain settle), so stopped > 0 here means the
  // chain IS settled — the canvas' one refresh draws it. A page-started
  // run additionally resolves its pending generate() stopped and shows
  // its own notice.
  const stopGraphRun = async (superThreadId: string) => {
    try {
      const res = await stopAnalysisSummary(superThreadId);
      setNoticeMsg(
        res.stopped > 0
          ? "Stop requested — the chain is settling…"
          : "No live run found — it settled before the stop landed",
      );
      // Belt for the view's own refresh pulses: the settle may land
      // after even the 5s pulse (a slow chain settle), so the button
      // never hangs on a thread that is already stopped.
      history.bumpGraphSignal(3000);
      history.bumpGraphSignal(7000);
    } catch (err) {
      setErrorMsg(
        `Failed to stop the run: ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
    }
  };

  return { generate, handleGenerateConfirm, stopGeneration, stopGraphRun };
}
