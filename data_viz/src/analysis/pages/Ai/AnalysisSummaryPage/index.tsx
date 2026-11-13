/**
 * AnalysisSummaryPage — the AI section's body (the former "Analysis
 * Summary"/"Thread Graph" tab strip is gone; the thread graph now lives
 * in the central result panel behind the LangGraph/Result toggle):
 * the classification-nav strip on top (sec_type toggle — all / industry /
 * index / etf / stock — plus the shared sector→industry + strategy→theme
 * navigator and the L3 security chips, whose L3 row is MULTI-SELECT:
 * every tick lands in the Scopes panel's SUMMARY SUBJECTS tag bar, the
 * default-linked scope pick; "all" = the locked exchange-only nav whose
 * run scope stays absent, i.e. market-wide), then two horizontal
 * foldables on
 * the left — Scopes (the summary-subject tags + their input bar, then
 * the analysis-item tree) and Prompts (the skills +
 * plan rows with their picked checkboxes, the tool rows' view/edit-only
 * Tools section, and the system-prompt slot; each folds into a slim
 * vertical strip, a click expands it to the right, and both stretch to
 * the summary card's height — the fold states persist in localStorage
 * (usePanelFold): Scopes and Prompts start expanded, Run Limits folded) — and the central result panel toggling
 * between the run's RESULT document and its LangGraph thread graph. The
 * Result view reads
 * the picked thread's kind final-result context (the runner's
 * HARDCODED result node's fixed output — persisted with the run, so a
 * history run opened from the strip displays its result document too);
 * the in-memory generate response is the fallback only when the run
 * left no thread (plan degraded / market-wide no-plan run).
 *
 * Generate posts the selection — the ticked tables (the Scopes panel's
 * KG table tree, one analysis_items entry per table AND the same list
 * as forced_tables — the run's KG scope), the picked skills- and
 * plan-type llm.ai_prompts
 * prompt_key values, and the scope subjects (sec_type + EVERY picked
 * code / industry id — the classification nav's multi-selected L3 items,
 * DEFAULT-LINKED into the Scopes panel's tag bar, where the input bar
 * adds typed codes, a tag's cross removes its pick, and the top search
 * bar tags its resolved code too; empty / "all" = market-wide) — as JSON
 * to POST /api/ai/summary, which runs the deep-agents daemon (read-only
 * DB tool loop) and returns the markdown document; the run's plan steps
 * live only in the thread graph (auto-selected by the toggle while it
 * runs — see LIVE GENERATION VIEW; the finished answer flips the toggle
 * back to Result). Beneath the classification nav, the Summary History
 * section shows the shared DateEventStrip (the date-event bar) over the
 * planned deep-agent runs, loaded in TWO phases so the bar renders fast:
 * phase 1 fetches only the per-day aggregates (GET /api/ai/summary-runs/dates —
 * one dot per day with runs, no per-thread rows) and phase 2 fetches a
 * picked day's per-thread rows (GET /api/ai/summary-runs?date=) when a
 * dot is clicked or a run goes live.
 * Clicking a dot opens that run's thread graph in the result panel
 * (ThreadGraphView): single-run days open it directly, multi-run days
 * first show a dropdown to pick the run; clicking the selected dot
 * again collapses the pick. The paper header's search box looks a run
 * up by its thread id directly (a case-insensitive PREFIX — a pasted
 * full uuid4().hex id is the exact case; the lookup ignores the scope
 * pick and its landing day's dropdown rows load unfiltered), opening
 * the matched run's graph — the newest when the prefix hits several.
 * The strip follows the scope pick: picked subjects narrow the runs to
 * those scoped to ANY of them PLUS the market-wide ones (threads with
 * no scope pick — llm.deep_agent_identities is the code <-> thread_id
 * link the runner's plan node writes, one row per picked subject; a
 * market-wide summary covers every subject, so its runs stay visible
 * under any pick, badged in the dropdown), and an empty pick ("all" mode
 * or no tags) shows every run. Changing the pick collapses any open
 * graph.
 * The scope focuses the agent's metric
 * queries on the picked subject (see the SUMMARY_GOAL contract in
 * services/ai-summary.service.ts).
 *
 * The Generate click opens the run's pre-run popup (GenerateQueryDialog)
 * instead of posting directly: the query text — the request's `question`
 * focus instruction, defaulting to the codes/market under the scope pick
 * and following the dialog's code edits until the user edits the text —
 * the scope subjects (the same tag bar the Scopes panel keeps, seeded
 * from the pick; hidden under "all"), and the Plan-goal radio (the
 * Prompts panel's exclusive study-scope choice, defaulting to its
 * current pick and REACTING to the dialog's own codes: codes-family
 * rows need named codes, market-family rows the empty pick, a
 * ruled-out pick falls back to Auto). Confirm syncs the goal into the
 * prompt selection and the codes into the scope tags, then posts the
 * run; Cancel discards the edits.
 *
 * While the run is in flight the Generate button turns into Stop on
 * hover (a disabled button ignores mouse events, so the hover is
 * tracked on the wrapper box): clicking it POSTs /api/ai/summary/stop,
 * the daemon cancels the langgraph thread, and the still-pending
 * generate resolves stopped — the auto-opened thread graph then shows
 * the settled thread (the in-progress step failed "stopped by user",
 * the never-started ones skipped).
 *
 * LIVE GENERATION VIEW (the polls — see below for the disabled event
 * channel): the Generate click flips the
 * result panel to the graph view IN THE CLICK'S FRAME —
 * ThreadGraphView's optimistic planning canvas (a running plan node)
 * draws immediately, before the thread even registers — and the daemon
 * announces every run
 * and state mutation on its WebSocket stream (llm_agents/
 * deep_agents/events.py → daemon /ws → the bridge's /api/ai/events →
 * aiEvents.ts — one shared browser socket with reconnect; the channel
 * is INTENTIONALLY DISABLED as of 2026-11-11, AI_EVENTS_CHANNEL_DISABLED
 * in aiEvents.ts, bypassed not removed). The click's
 * thread_started event is the PRIMARY open path: the daemon registers
 * the run and persist its RUNNING PLANNING NODE (an ordinal-0 'plan'
 * state row, in_progress) in the same instants, so the handler creates
 * the click's Summary History record (refreshDates finds the row —
 * event date + time) and loads the run's graph, which opens straight
 * onto the running planning node (no 404 wait: the thread read has its
 * first row from the first moment). State events for the open thread
 * bump a refresh signal that refetches its graph immediately
 * (coalesced); thread_settled lands the final strip counts. 2026-11-11
 * the event channel is INTENTIONALLY DISABLED (aiEvents.ts — bypassed,
 * not removed; the handler sits dormant) and the polls are the only
 * refresh path: the strip's re-read while generating and the graph
 * view's own loop both run at AI_EVENTS_FALLBACK_POLL_MS (60s). Once
 * per generation: a
 * run opened this way stays put even if an older thread is still stuck
 * unsettled, and the user can collapse it freely.
 *
 * PROMPT MODE: clicking a prompt's row in the Prompts picker (besides
 * ticking its checkbox) swaps the main panel to that prompt's content
 * view (PromptContent — title + type/version/key line, note, the Labels
 * section, and the instruction text). The Generate button is replaced by
 * Edit; Save PUTs the edited instruction (PUT /api/ai/prompts/:prompt_key),
 * the server replaces the row's text in place and bumps its version by
 * one, and the picker + view follow the refreshed row. Clicking the open
 * row again (or a failed-save-free Cancel) returns to the summary view.
 *
 * LABELS (the prompt view's PromptLabels section): each row's
 * bound_labels (force-load keywords) + optional_labels (search
 * vocabulary) render as chip groups. Adding/removing a label asks how
 * far it lives: "Save permanently" PUTs the row's full effective arrays
 * to /api/ai/prompts/:key/labels (the picker + view follow the refreshed
 * row), while "Just this run" keeps the change in the page's
 * sessionLabels override state — dashed chips mark run-only additions,
 * struck-through chips run-only removals, the Generate request carries
 * the overrides as promptLabels (→ the agent's prompt_labels), and the
 * whole session delta is FORGOTTEN when the page unmounts (leaving the
 * UI page). The add controls suggest the registry's existing label
 * vocabulary (the scope-bound guides' phrases) and accept typed custom
 * phrases equally. The picker rows show their existing labels as TAGS
 * (bound tinted / optional plain, "+n" past four) with a "+" button
 * that opens the prompt focused on the labels input.
 *
 * Split into ./useScopeSubjects (nav + subject tags), ./usePrompts,
 * ./usePromptLabels, ./usePromptEditor, ./usePlanTemplates,
 * ./useSummaryHistory (the strip + live tracking + the dormant event
 * channel), ./useSummaryGeneration (the run lifecycle), ./helpers and
 * ./sections (ScopeNavSection / SummaryHistorySection / ResultPanel).
 */
import { useState } from "react";
import { Box } from "@mui/material";
import type { AiSummaryScope } from "@/lib/api-client/aiSummary";
import AnalysisItemsTree from "../AnalysisItemsTree";
import GenerateQueryDialog from "../GenerateQueryDialog";
import RunLimitsPanel from "../RunLimitsPanel";
import SkillsPromptsPanel from "../SkillsPromptsPanel";
import { isSelectablePrompt, isToolPrompt, itemKindFor } from "./helpers";
import { useScopeSubjects } from "./useScopeSubjects";
import { usePlanTemplates } from "./usePlanTemplates";
import { usePrompts } from "./usePrompts";
import { usePromptLabels } from "./usePromptLabels";
import { usePromptEditor } from "./usePromptEditor";
import { useSummaryHistory } from "./useSummaryHistory";
import { useSummaryGeneration } from "./useSummaryGeneration";
import ScopeNavSection from "./sections/ScopeNavSection";
import SummaryHistorySection from "./sections/SummaryHistorySection";
import ResultPanel from "./sections/ResultPanel";

export default function AnalysisSummaryPage() {
  // ---- Scope layer: the classification nav + the subject tags ----
  const scope = useScopeSubjects();
  const { nav } = scope;

  // ---- The page-level generation state ----
  const [generating, setGenerating] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  // Neutral feedback (the stop took effect) — the info Alert beside the
  // error one.
  const [noticeMsg, setNoticeMsg] = useState<string | null>(null);
  const [summaryMd, setSummaryMd] = useState<string | null>(null);
  // The Generate click opens the pre-run popup (the query / codes /
  // forced-table-scope / plan-goal confirm) instead of posting the run
  // directly; the popup's confirm is what starts the generation.
  const [generateDialogOpen, setGenerateDialogOpen] = useState(false);
  // ---- Forced table scope (the Generate dialog's KG-table knob) ----
  // The run's FORCED-TABLE-SCOPE: the DB tables the deep agent's KG
  // surface (kg_search / kg_expand / the materialized-data hints)
  // serves this run — the serving layer filters structurally to the
  // ticked names; empty = no limitation. The dialog edits this list;
  // the request rides it only when non-empty. The knob's option data
  // is the SHARED KG table corpus (useKgScopeTables inside
  // KgTableScopeSelect — one session fetch, the same list the
  // Knowledge Graph config page's dropdown renders), so the page
  // fetches nothing itself.
  const [forcedTables, setForcedTables] = useState<string[]>([]);

  // The scope subjects in the request's entry shape — one AiSummaryScope
  // per picked code (industry ids in industry mode), each carrying its
  // tree-resolved display name. Absent (empty pick or the "all" sec_type)
  // = market-wide.
  const scopeSecType =
    nav.secType === "all" ? null : (nav.secType as AiSummaryScope["secType"]);
  // Whether the pick NAMES codes — the same condition the request's
  // scopes ride on. An empty pick reads all codes → the whole market,
  // and it drives the Plan-goal radios: the codes-family goals apply
  // only here, the market-family goals only on the empty pick.
  const scopeHasCodes = scopeSecType !== null && scope.scopeCodes.length > 0;

  const planTemplates = usePlanTemplates(setErrorMsg);
  const promptsCatalog = usePrompts(scopeHasCodes);
  const promptLabels = usePromptLabels(
    promptsCatalog.prompts,
    promptsCatalog.setPrompts,
    setErrorMsg,
    setNoticeMsg,
  );
  const promptEditor = usePromptEditor(
    promptsCatalog.prompts,
    promptLabels.promptsEffective,
    promptsCatalog.setPrompts,
    setErrorMsg,
    setNoticeMsg,
  );

  // The picker row's "+" label button: open the prompt (read mode)
  // focused on its labels input — PromptLabels scrolls itself into view
  // and focuses the bound group's add control on the bump.
  const openPromptLabels = (identity: string) => {
    promptEditor.openPromptByKey(identity);
    promptLabels.bumpLabelsFocus();
  };

  const history = useSummaryHistory({
    secType: nav.secType,
    scopeCodes: scope.scopeCodes,
    generating,
    promptViewOpen: promptEditor.openPrompt !== null,
    setErrorMsg,
    setNoticeMsg,
  });

  const { handleGenerateConfirm, stopGeneration, stopGraphRun } =
    useSummaryGeneration({
      history,
      scopeSecType,
      subjectNameMap: scope.subjectNameMap,
      subjectKey: scope.subjectKey,
      setScopeCodes: scope.setScopeCodes,
      prompts: promptsCatalog.prompts,
      selectedPrompts: promptsCatalog.selectedPrompts,
      setSelectedPrompts: promptsCatalog.setSelectedPrompts,
      goalPrompts: promptsCatalog.goalPrompts,
      promptLabelOverrides: promptLabels.promptLabelOverrides,
      planTemplateId: planTemplates.planTemplateId,
      setGenerateDialogOpen,
      setForcedTables,
      setGenerating,
      setSummaryMd,
      setErrorMsg,
      setNoticeMsg,
    });

  // A run is live when this page started one OR the daemon reports any
  // in-flight run — the Stop button shows for either.
  const runLive = generating || history.serverActiveRuns.length > 0;

  return (
    <Box>
      <ScopeNavSection scope={scope} />

      {/* ---- Summary History: the date-event bar + the picked run's
           thread graph (the former Thread Graph tab, folded in) ---- */}
      <SummaryHistorySection
        subjects={scope.subjects}
        scopeLabel={scope.scopeLabel}
        threadSearch={history.threadSearch}
        onThreadSearchChange={history.setThreadSearch}
        threadSearching={history.threadSearching}
        onThreadSearch={history.searchRunByThreadId}
        runEvents={history.runEvents}
        datesLoading={history.datesLoading}
        selectedRunDate={history.selectedRunDate}
        onRunDotClick={history.handleRunDotClick}
        runsOnSelectedDate={history.runsOnSelectedDate}
        selectedThreadId={history.selectedThreadId}
        onThreadPick={(threadId) => {
          history.setSelectedThreadId(threadId);
          history.setResultView("langgraph");
        }}
        liveThreadId={history.liveThreadId}
        onDeleteRun={history.deleteRun}
      />

      <Box
        sx={{
          display: "flex",
          gap: 2,
          alignItems: "stretch",
          // Wrap below xl — two expanded panels + the strip + the result
          // card exceed even lg widths (at 1280 the result card was
          // crushed to ~150px and its toolbar spilled past the page
          // edge); only from xl up do all four fit side by side.
          flexWrap: { xs: "wrap", xl: "nowrap" },
        }}
      >
        <AnalysisItemsTree
          subjects={scope.subjects}
          onRemoveSubject={scope.removeScopeCode}
          onAddSubject={scope.addScopeCode}
          subjectSuggestions={scope.subjectSuggestions}
          subjectKind={itemKindFor(nav.secType)}
          forcedTables={forcedTables}
          onChangeForcedTables={setForcedTables}
        />
        <SkillsPromptsPanel
          prompts={promptLabels.promptsEffective.filter(isSelectablePrompt)}
          toolPrompts={promptLabels.promptsEffective.filter(isToolPrompt)}
          loading={promptsCatalog.promptsLoading}
          selectedPrompts={promptsCatalog.selectedPrompts}
          onTogglePrompt={promptsCatalog.togglePrompt}
          openPromptKey={promptEditor.openPromptKey}
          onOpenPrompt={promptEditor.openPromptByKey}
          onAddLabel={openPromptLabels}
          scopeHasCodes={scopeHasCodes}
        />
        <RunLimitsPanel />
        <ResultPanel
          errorMsg={errorMsg}
          onCloseError={() => setErrorMsg(null)}
          noticeMsg={noticeMsg}
          onCloseNotice={() => setNoticeMsg(null)}
          openPrompt={promptEditor.openPrompt}
          openBasePrompt={promptEditor.openBasePrompt}
          editingPrompt={promptEditor.editingPrompt}
          onEditingChange={promptEditor.setEditingPrompt}
          savingPrompt={promptEditor.savingPrompt}
          draftInstruction={promptEditor.draftInstruction}
          onDraftChange={promptEditor.setDraftInstruction}
          onStartPromptEdit={promptEditor.startPromptEdit}
          onSavePromptEdit={() => void promptEditor.savePromptEdit()}
          resultView={history.resultView}
          onResultViewChange={history.setResultView}
          planTemplates={planTemplates.planTemplates}
          planTemplateId={planTemplates.planTemplateId}
          onPlanTemplateChange={planTemplates.setPlanTemplateId}
          onRemovePlanTemplate={(id) => void planTemplates.removePlanTemplate(id)}
          generating={generating}
          runLive={runLive}
          onStopGeneration={() => void stopGeneration()}
          onOpenGenerateDialog={() => setGenerateDialogOpen(true)}
          sessionLabels={promptLabels.sessionLabels}
          labelSuggestions={promptLabels.labelSuggestions}
          labelsFocusSignal={promptLabels.labelsFocusSignal}
          onApplyLabelChange={(identity, group, label, add, permanent) =>
            void promptLabels.applyLabelChange(
              identity,
              group,
              label,
              add,
              permanent,
            )
          }
          graphThreadId={history.liveThreadId ?? history.selectedThreadId}
          onSettled={history.refreshHistory}
          graphSignal={history.graphSignal}
          onPlanTemplateSaved={planTemplates.refreshPlanTemplates}
          onGraphStop={stopGraphRun}
          resultThreadId={history.resultThreadId}
          resultDetail={history.resultDetail}
          resultLoading={history.resultLoading}
          finalResultPayload={history.finalResultPayload}
          summaryMd={summaryMd}
        />
      </Box>

      {/* ---- The Generate button's pre-run popup: the query / codes /
           forced-table-scope / plan-goal confirm (its confirm starts
           the run) ---- */}
      <GenerateQueryDialog
        open={generateDialogOpen}
        onClose={() => setGenerateDialogOpen(false)}
        onConfirm={handleGenerateConfirm}
        goalPrompts={promptsCatalog.goalPrompts}
        selectedGoalKey={promptsCatalog.selectedGoalKey}
        initialCodes={scope.subjects}
        initialForcedTables={forcedTables}
        subjectSuggestions={scope.subjectSuggestions}
        subjectKind={itemKindFor(nav.secType)}
      />
    </Box>
  );
}
