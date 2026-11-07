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
 * the summary card's height) — and the central result panel toggling
 * between the run's RESULT document and its LangGraph thread graph. The
 * Result view reads
 * the picked thread's kind final-result context (the runner's
 * HARDCODED result node's fixed output — persisted with the run, so a
 * history run opened from the strip displays its result document too);
 * the in-memory generate response is the fallback only when the run
 * left no thread (plan degraded / market-wide no-plan run).
 *
 * Generate posts the selection — the analysis items (key + label +
 * insight identity), the picked skills- and plan-type llm.ai_prompts
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
 * While the run is in flight the Generate button turns into Stop on
 * hover (a disabled button ignores mouse events, so the hover is
 * tracked on the wrapper box): clicking it POSTs /api/ai/summary/stop,
 * the daemon cancels the langgraph thread, and the still-pending
 * generate resolves stopped — the auto-opened thread graph then shows
 * the settled thread (the in-progress step failed "stopped by user",
 * the never-started ones skipped).
 *
 * LIVE GENERATION VIEW (event channel): the Generate click flips the
 * result panel to the graph view IN THE CLICK'S FRAME —
 * ThreadGraphView's optimistic planning canvas (a running plan node)
 * draws immediately, before the thread even registers — and the daemon
 * announces every run
 * and state mutation on its WebSocket stream (llm_agents/
 * deep_agents/events.py → daemon /ws → the bridge's /api/ai/events →
 * aiEvents.ts — one shared browser socket with reconnect). The click's
 * thread_started event is the PRIMARY open path: the daemon registers
 * the run and persist its RUNNING PLANNING NODE (an ordinal-0 'plan'
 * state row, in_progress) in the same instants, so the handler creates
 * the click's Summary History record (refreshDates finds the row —
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
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  IconButton,
  InputAdornment,
  MenuItem,
  Paper,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import {
  Cancel,
  Close,
  Edit,
  PlayArrow,
  Save,
  Search,
  Stop,
} from "@mui/icons-material";
import CodeSearchBar, {
  findCodeInStrategyThemes,
  findCodeInThemes,
} from "@/components/CodeSearchBar";
import RefreshButton from "@/components/RefreshButton";
import DateEventStrip, {
  type DateEvent,
} from "@/shared/components/date-events/DateEventStrip";
import MarkdownView from "@/shared/markdown/MarkdownView";
import { MUTED_PALETTE } from "@/theme/chart-palette";
import SecClassificationNav from "@/shared/components/sec-classification/SecClassificationNav";
import { DEFAULTS_BY_KIND } from "@/shared/components/sec-classification/constants";
import { SecTypeToggle, useSecNav } from "@/shared/components/sec-nav";
import type { SecNavSecType } from "@/shared/components/sec-nav";
import {
  deleteSummaryRun,
  fetchAiPrompts,
  fetchSummaryRunDates,
  fetchSummaryRuns,
  generateAnalysisSummary,
  promptIdentity,
  stopAnalysisSummary,
  updateAiPrompt,
  updateAiPromptLabels,
  type AiPrompt,
  type AiSummaryRun,
  type AiSummaryRunDate,
  type AiSummaryRunFilters,
  type AiSummaryScope,
  type PromptLabelSets,
} from "@/lib/api-client/aiSummary";
import { subscribeAiEvents } from "@/lib/api-client/aiEvents";
import {
  deletePlanTemplate,
  fetchPlanTemplates,
  type AiPlanTemplate,
} from "@/lib/api-client/aiPlanTemplates";
import AnalysisItemsTree, {
  type ScopeSubjectEntry,
} from "./AnalysisItemsTree";
import PromptContent from "./PromptContent";
import PromptLabels from "./PromptLabels";
import RunLimitsPanel from "./RunLimitsPanel";
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
  goalAppliesToScope,
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

/** The tool-type rows — the model-callable tools' schema descriptions:
 *  the runner resolves the active rows automatically at every run start
 *  (tools.base's override contract), so they are picker VIEW/EDIT rows,
 *  never selected into the request. */
function isToolPrompt(prompt: AiPrompt): boolean {
  return prompt.prompt_type_label === "tool";
}

/** Normalize one typed label the way the server saves it (trimmed, the
 *  optional group lowercased) so a run-only label matches its later
 *  permanent form. */
function normalizeLabel(label: string, group: keyof PromptLabelSets): string {
  const trimmed = label.trim();
  return group === "optional" ? trimmed.toLowerCase() : trimmed;
}

/** A run row with steps that are neither completed/failed/skipped —
 *  the in-flight signature (the runner inserts the plan rows pending
 *  at run start; finalize settles the leftovers either way). */
function hasUnsettledSteps(r: AiSummaryRun): boolean {
  return r.steps_total > r.steps_completed + r.steps_failed + r.steps_skipped;
}

/** The same signature at the DATE-BUCKET level (the phase-1 aggregates):
 *  a bucket whose steps are not all settled has an in-flight (or
 *  crashed-unsettled) run on that day — the fallback open's candidate
 *  detector, over rows the page never fetches wholesale anymore. */
function dateUnsettled(d: AiSummaryRunDate): boolean {
  return d.steps_total > d.steps_completed + d.steps_failed + d.steps_skipped;
}

export default function AnalysisSummaryPage() {
  // ---- Scope nav (classification-nav kit; the pick focuses the run) ----
  const nav = useSecNav({
    themesSources: SUMMARY_THEMES_SOURCES,
    defaultSecType: "index",
    dataLabel: "summary scope",
  });

  // ---- Scope subjects (the classification nav's MULTI-pick + the
  // Scopes panel's tag bar) ----
  // The run's scope subject list: the nav's L3 row is MULTI-SELECT (every
  // tick lands here), the Scopes panel's tag + input bar reads/writes the
  // SAME list (tags default-linked to the nav picks, typed codes join
  // them, a tag's cross removes the pick), and the top CodeSearchBar adds
  // its resolved code too. A sec_type switch wipes the list — a code of
  // one universe is meaningless under another — and re-seeds the
  // DEFAULTS_BY_KIND subject so the out-of-box run keeps its canonical
  // focus (the multi-select nav skips its own single-select defaults).
  const [scopeCodes, setScopeCodes] = useState<string[]>([]);
  const seededSecTypeRef = useRef<SecNavSecType | null>(null);
  const handleSecTypeChange = (t: SecNavSecType) => {
    seededSecTypeRef.current = null;
    setScopeCodes([]);
    nav.setSecType(t);
  };

  /** Suffix-insensitive code key (000001.SZ ≙ 000001) — matches the nav's
   *  own normCode so a typed suffixed code joins its bare twin's tag. */
  const subjectKey = (code: string): string =>
    code.toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "");

  // code → display name across BOTH trees (the tags' labels and the
  // generate request's name field; a typed code outside the trees shows
  // and rides bare).
  const subjectNameMap = useMemo(() => {
    const m = new Map<string, string>();
    const add = (code: string, name: string) => {
      const key = subjectKey(code);
      if (!m.has(key)) m.set(key, name);
    };
    for (const s of nav.sectors) {
      for (const ind of s.industries) {
        for (const it of ind.items) add(it.code, it.name);
      }
    }
    for (const s of nav.strategies) {
      for (const th of s.industries) {
        for (const it of th.items) add(it.code, it.name);
      }
    }
    return m;
  }, [nav.sectors, nav.strategies]);

  // The input bar's suggestion list — the loaded trees' items, deduped by
  // normalized code (the Autocomplete's filter narrows them on typing).
  const subjectSuggestions = useMemo(() => {
    const out: Array<{ code: string; name: string }> = [];
    const seen = new Set<string>();
    for (const tree of [nav.sectors, nav.strategies]) {
      for (const s of tree) {
        for (const ind of s.industries) {
          for (const it of ind.items) {
            const key = subjectKey(it.code);
            if (!seen.has(key)) {
              seen.add(key);
              out.push({ code: it.code, name: it.name });
            }
          }
        }
      }
    }
    return out;
  }, [nav.sectors, nav.strategies]);

  // The tags the Scopes panel renders (code + resolved name).
  const subjects: ScopeSubjectEntry[] = useMemo(
    () =>
      scopeCodes.map((code) => {
        const name = subjectNameMap.get(subjectKey(code));
        return name === undefined ? { code } : { code, name };
      }),
    [scopeCodes, subjectNameMap],
  );

  const addScopeCode = useCallback((code: string) => {
    const key = subjectKey(code);
    setScopeCodes((prev) =>
      prev.some((c) => subjectKey(c) === key) ? prev : [...prev, code],
    );
  }, []);

  const removeScopeCode = useCallback((code: string) => {
    const key = subjectKey(code);
    setScopeCodes((prev) => prev.filter((c) => subjectKey(c) !== key));
  }, []);

  // Seed the subjects once per sec_type: the SAME default subject the
  // single-select nav drilled into (DEFAULTS_BY_KIND — the canonical
  // code, its industry's first item when classification drift moved it),
  // so the out-of-box run keeps the canonical focus instead of going
  // market-wide by accident. useNavDefaults no-ops in multi-select mode,
  // so the page owns the seed.
  useEffect(() => {
    if (seededSecTypeRef.current === nav.secType) return;
    const kind = itemKindFor(nav.secType);
    if (kind === undefined) return; // "all" — nothing to seed
    const def = DEFAULTS_BY_KIND[kind];
    if (def === undefined) return;
    const asStrategy = nav.strategies.find(
      (s) => s.sector_id === def.sectorId,
    );
    const asSector = nav.sectors.find((s) => s.sector_id === def.sectorId);
    const node = asStrategy ?? asSector;
    if (node === undefined) return; // trees not loaded yet — retry when they land
    seededSecTypeRef.current = nav.secType;
    const industry =
      node.industries.find((i) => i.industry_slug === def.industrySlug) ??
      node.industries[0];
    const item =
      industry?.items.find((it) => it.code === def.itemCode) ??
      industry?.items[0];
    if (industry === undefined || item === undefined) return;
    // Browse the nav to the default's location so the L3 chips show
    // where the seed came from.
    if (asStrategy !== undefined) {
      nav.handleStrategyChange(def.sectorId);
      nav.handleThemeChange(industry.industry_slug);
    } else {
      nav.handleSectorChange(def.sectorId);
      nav.handleIndustryChange(industry.industry_slug);
    }
    setScopeCodes([item.code]);
    // The nav handlers are stable in behavior but recreated per render;
    // the seeded ref makes re-runs no-ops.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nav.secType, nav.sectors, nav.strategies]);

  // The top search bar keeps its nav-jump job (resolve + highlight where
  // a code lives) AND adds the resolved code as a subject tag — the
  // pre-multi behavior had the search pick BE the scope, so a searched
  // code landing in the tags is the least surprising continuation.
  const handleScopeSearch = (code: string) => {
    const found =
      findCodeInThemes(nav.sectors, code) !== null ||
      findCodeInStrategyThemes(nav.strategies, code) !== null;
    nav.handleSearch(code);
    if (found) addScopeCode(code);
  };

  // The multi-pick summary label (the top caption + the history header):
  // the kind + count + the first tags, then "+n more"; empty (or the
  // "all" sec_type) reads market-wide.
  const scopeLabel =
    subjects.length === 0
      ? nav.secType === "all"
        ? "all (market-wide)"
        : "market-wide"
      : `${itemKindFor(nav.secType) ?? "Subject"} ×${subjects.length}: ` +
        subjects
          .slice(0, 3)
          .map((s) => (s.name === undefined ? s.code : `${s.code} ${s.name}`))
          .join(" + ") +
        (subjects.length > 3 ? ` +${subjects.length - 3} more` : "");

  // "Select all" default — the seed set is the full leaf universe.
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(
    () => new Set(ALL_ANALYSIS_ITEM_KEYS),
  );
  // Prompts come from llm.ai_prompts (GET /api/ai/prompts); the picker
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

  // ---- Saved plan templates (llm.deep_agent_plan_templates) ----
  // A template picked here starts the run's plan from the saved
  // placeholder-templated plan (zero planning LLM calls); null = the
  // normal LLM planning session.
  const [planTemplates, setPlanTemplates] = useState<AiPlanTemplate[]>([]);
  const [planTemplateId, setPlanTemplateId] = useState<number | null>(null);
  const refreshPlanTemplates = useCallback(() => {
    fetchPlanTemplates()
      // Only TYPE-PLAN rows are loadable as a plan (the renamed
      // langgraph-templates table also carries the seeded type-result
      // review-wave row — listing it in the picker would only send the
      // daemon a template it must fail-soft refuse).
      .then((rows) => setPlanTemplates(rows.filter((t) => t.type === 0)))
      .catch(() => setPlanTemplates([]));
  }, []);
  useEffect(() => {
    refreshPlanTemplates();
  }, [refreshPlanTemplates]);
  const removePlanTemplate = async (templateId: number) => {
    try {
      await deletePlanTemplate(templateId);
    } catch (err) {
      setErrorMsg(String(err instanceof Error ? err.message : err));
      return;
    }
    setPlanTemplateId((current) =>
      current === templateId ? null : current,
    );
    refreshPlanTemplates();
  };
  const [summaryMd, setSummaryMd] = useState<string | null>(null);
  // Hover over the generator button — while generating, hovering turns
  // it into Stop. Tracked on the wrapper box because a DISABLED button
  // (the generating state) swallows mouse events.
  const [generateHovered, setGenerateHovered] = useState(false);

  // ---- Prompt view (the main panel's prompt mode) ----
  // The opened prompt (clicking its picker row, not the checkbox) — the
  // main panel shows its content with Edit/Save in place of Generate.
  // The state carries the row's composite identity (promptIdentity —
  // key|sub_type): prompt_key alone is not unique (update_plan has one
  // row per lane among the tool rows).
  const [openPromptKey, setOpenPromptKey] = useState<string | null>(null);
  const [editingPrompt, setEditingPrompt] = useState(false);
  const [draftInstruction, setDraftInstruction] = useState("");
  const [savingPrompt, setSavingPrompt] = useState(false);

  // ---- Labels (the prompt view's PromptLabels section) ----
  // The "Just this run" saves: prompt identity → per-group effective
  // arrays (only the groups with a session delta — the other group keeps
  // reading its persisted arrays). The Generate request carries them as
  // promptLabels (→ the agent's prompt_labels), and the page unmount —
  // leaving the UI page — forgets them; nothing here is persisted.
  const [sessionLabels, setSessionLabels] = useState<
    Record<string, Partial<PromptLabelSets>>
  >({});

  // The prompts with the session label overrides applied — the picker
  // rows' label counts and the open prompt view read these; `prompts`
  // stays the persisted baseline the editor diffs against.
  const promptsEffective = useMemo(
    () =>
      prompts.map((p) => {
        const override = sessionLabels[promptIdentity(p)];
        if (!override) return p;
        return {
          ...p,
          bound_labels: override.bound ?? p.bound_labels,
          optional_labels: override.optional ?? p.optional_labels,
        };
      }),
    [prompts, sessionLabels],
  );

  // The run-scoped overrides in the request's full-set shape (identity →
  // both arrays) — promptsEffective already merged the partial deltas.
  const promptLabelOverrides = useMemo(() => {
    const out: Record<string, PromptLabelSets> = {};
    for (const p of promptsEffective) {
      if (sessionLabels[promptIdentity(p)] !== undefined) {
        out[promptIdentity(p)] = {
          bound: p.bound_labels,
          optional: p.optional_labels,
        };
      }
    }
    return out;
  }, [promptsEffective, sessionLabels]);

  // The registry's label vocabulary per group — the editor's suggestion
  // list (the scope-bound guides' seeded phrases; a permanently saved
  // custom phrase joins it for the next picker).
  const labelSuggestions = useMemo<PromptLabelSets>(
    () => ({
      bound: [...new Set(promptsEffective.flatMap((p) => p.bound_labels))].sort((a, b) =>
        a.localeCompare(b),
      ),
      optional: [...new Set(promptsEffective.flatMap((p) => p.optional_labels))].sort(
        (a, b) => a.localeCompare(b),
      ),
    }),
    [promptsEffective],
  );

  // One committed Labels-editor change. Permanent: PUT the row's full
  // effective arrays (the server replaces wholesale) and swap the
  // refreshed row into state — the session override entry for THAT GROUP
  // then dissolves (the DB now matches it; the other group's run-only
  // delta survives). Run-only: the arrays land in the session override
  // map (forgotten on page leave; rides the next Generate).
  const applyLabelChange = async (
    identity: string,
    group: keyof PromptLabelSets,
    label: string,
    add: boolean,
    permanent: boolean,
  ) => {
    const base = prompts.find((p) => promptIdentity(p) === identity);
    if (!base) return;
    const value = normalizeLabel(label, group);
    if (value === "") return;
    const override = sessionLabels[identity];
    const current: PromptLabelSets = {
      bound: override?.bound ?? base.bound_labels,
      optional: override?.optional ?? base.optional_labels,
    };
    const next: PromptLabelSets =
      group === "bound"
        ? {
            bound: add
              ? [...new Set([...current.bound, value])]
              : current.bound.filter((l) => l !== value),
            optional: current.optional,
          }
        : {
            bound: current.bound,
            optional: add
              ? [...new Set([...current.optional, value])]
              : current.optional.filter((l) => l !== value),
          };
    if (!permanent) {
      setSessionLabels((prev) => ({ ...prev, [identity]: { ...prev[identity], [group]: next[group] } }));
      return;
    }
    // The permanent save promotes ONLY its own group's effective arrays;
    // the other group rides its persisted baseline so a run-only delta
    // there stays run-only (the endpoint replaces both arrays wholesale).
    const payload: PromptLabelSets = {
      bound: group === "bound" ? next.bound : base.bound_labels,
      optional: group === "optional" ? next.optional : base.optional_labels,
    };
    try {
      const updated = await updateAiPromptLabels(
        base.prompt_key,
        payload,
        base.prompt_sub_type,
      );
      setPrompts((prev) =>
        prev.map((p) =>
          promptIdentity(p) === promptIdentity(updated) ? updated : p,
        ),
      );
      // The saved group's session delta dissolves; the other group's
      // run-only delta stays until its own change or the page leave.
      setSessionLabels((prev) => {
        const entry = { ...prev[identity] };
        delete entry[group];
        const rest = { ...prev };
        if (Object.keys(entry).length > 0) {
          rest[identity] = entry;
        } else {
          delete rest[identity];
        }
        return rest;
      });
      setNoticeMsg(`Saved labels for "${updated.title}"`);
    } catch (err) {
      setErrorMsg(
        `Failed to save the labels: ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
    }
  };

  const openPrompt =
    promptsEffective.find((p) => promptIdentity(p) === openPromptKey) ?? null;
  // The open prompt's persisted baseline — what the labels editor diffs
  // its session delta against (null for the summary view).
  const openBasePrompt =
    prompts.find((p) => promptIdentity(p) === openPromptKey) ?? null;

  // The picker row's "+" label button: open the prompt (read mode)
  // focused on its labels input — PromptLabels scrolls itself into view
  // and focuses the bound group's add control on the bump.
  const [labelsFocusSignal, setLabelsFocusSignal] = useState(0);
  const openPromptLabels = (identity: string) => {
    openPromptByKey(identity);
    setLabelsFocusSignal((s) => s + 1);
  };

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

  // The strip's nav filters — the scope pick scopes EVERY run-history
  // read (the dates, a day's rows, the live baseline) the same way: the
  // picked subjects narrow the runs to those scoped to ANY of them PLUS
  // the market-wide ones (the identity link's indexed EXISTS, OR no
  // identity row at all); an empty pick (or the "all" mode) shows every
  // run. secType rides only alongside codes — a bare sec_type filter has
  // no UI meaning and "all" is not a label_dim sec_type.
  const stripFilters = useCallback(
    (): AiSummaryRunFilters =>
      nav.secType !== "all" && scopeCodes.length > 0
        ? { secType: nav.secType, codes: scopeCodes }
        : {},
    [nav.secType, scopeCodes],
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

  // ---- Live generation view (see the header's LIVE GENERATION VIEW) ----
  // While a run is in flight, re-read the strip's date buckets every 3s
  // (same cadence as ThreadGraphView's own poll) — uncached reads, so the
  // in-flight run's dot appears the moment its plan persists at run
  // start. The read is the light per-day aggregate, not the per-thread
  // rows.
  useEffect(() => {
    if (!generating) return;
    const timer = setInterval(refreshDates, 3000);
    return () => clearInterval(timer);
  }, [generating, refreshDates]);

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
  }, [refreshDates, loadDayRuns, refreshHistory]);

  // A nav pick change invalidates the open run graph (the selected thread
  // may not be in the narrowed list) — collapse bar + graph. No-op on
  // mount (nothing open yet). Keyed on the flattened pick so the effect
  // does not re-run per render.
  const scopePickKey = scopeCodes.join("|");
  useEffect(() => {
    setSelectedRunDate(null);
    setSelectedThreadId(null);
  }, [nav.secType, scopePickKey]);

  useEffect(() => {
    let cancelled = false;
    fetchAiPrompts()
      .then((rows) => {
        if (cancelled) return;
        setPrompts(rows);
        if (!promptsLoadedRef.current) {
          promptsLoadedRef.current = true;
          // "Select all" first load — EXCEPT the plan-goal sub type:
          // those four study-scope rows are ONE radio choice in the
          // picker (its Auto entry = none picked, the runner defaults
          // by the run's scope), so the seed set starts at Auto
          // instead of checking all four at once.
          setSelectedPrompts(
            new Set(
              rows
                .filter(
                  (p) =>
                    isSelectablePrompt(p) &&
                    !(
                      p.prompt_type_label === "plan" &&
                      p.prompt_sub_type === "goal"
                    ),
                )
                .map((p) => p.prompt_key),
            ),
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

  // The prompt view's Save: PUT the edited instruction (the row's
  // sub_type rides along so duplicate-key rows — update_plan's lanes —
  // save independently) — the server replaces the row's text in place
  // and bumps its version by one; the refreshed row swaps into state
  // (the picker + the open view follow).
  const savePromptEdit = async () => {
    if (!openPrompt) return;
    setSavingPrompt(true);
    setErrorMsg(null);
    try {
      const updated = await updateAiPrompt(
        openPrompt.prompt_key,
        draftInstruction.trim(),
        openPrompt.prompt_sub_type,
      );
      setPrompts((prev) =>
        prev.map((p) => (promptIdentity(p) === promptIdentity(updated) ? updated : p)),
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
  const scopeHasCodes = scopeSecType !== null && scopeCodes.length > 0;

  // The study-scope goal pick follows the scope pick reactively: a goal
  // row the scope rules out (codes-family on an empty pick, market-family
  // with codes named) cannot stay selected — drop it so the set falls
  // back to Auto and the runner re-defaults by scope. Silent no-op when
  // nothing selected is out of scope.
  useEffect(() => {
    setSelectedPrompts((prev) => {
      const stale = prompts.filter(
        (p) => prev.has(p.prompt_key) && !goalAppliesToScope(p, scopeHasCodes),
      );
      if (stale.length === 0) return prev;
      const next = new Set(prev);
      for (const p of stale) next.delete(p.prompt_key);
      return next;
    });
  }, [prompts, scopeHasCodes]);

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
  }, [threadSearch, threadSearching]);

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
    [refreshHistory, selectedThreadId],
  );

  const generate = async () => {
    // The click's feedback is INSTANT: the live view (the thread graph's
    // optimistic planning canvas — the running planning node) flips in
    // this frame, before any fetch. The pre-click date-bucket snapshot
    // is synchronous state (the fallback open's "new bucket" test), and
    // the landing day's row set — the new run is "the first NEW
    // unsettled row" against it — follows one indexed day read later;
    // the event channel's thread_started opens the graph without
    // waiting for it.
    liveRunOpenedRef.current = false;
    preGenDatesRef.current = new Map(
      runDates.map((d) => [d.run_date, dateUnsettled(d)]),
    );
    setGenerating(true);
    // The event handler may see thread_started before the mirroring
    // effect below runs — the click itself claims the generation.
    generatingRef.current = true;
    // The run's thread graph is what to watch while it executes.
    setResultView("langgraph");
    setErrorMsg(null);
    setNoticeMsg(null);
    const today = new Date().toISOString().slice(0, 10);
    const baseline = await fetchSummaryRuns({
      ...stripFilters(),
      date: today,
    }).catch(() => [] as AiSummaryRun[]);
    knownThreadsRef.current = new Set(baseline.map((r) => r.thread_id));
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
      // The session's run-only label changes ride the request — the
      // agent applies them (and the force-load vocabulary they carry)
      // for THIS run only.
      const labelOverrides =
        Object.keys(promptLabelOverrides).length > 0 ? promptLabelOverrides : undefined;
      const res = await generateAnalysisSummary({
        analysisItems: items,
        prompts: promptKeys.length > 0 ? promptKeys : undefined,
        promptLabels: labelOverrides,
        // The subject tags ride as the scope list — every picked code one
        // AiSummaryScope entry (industry ids in industry mode); empty or
        // "all" sends none (market-wide).
        scopes:
          scopeSecType !== null && scopeCodes.length > 0
            ? scopeCodes.map((code) => ({
                secType: scopeSecType,
                ...(scopeSecType === "industry"
                  ? { industry: code }
                  : { code }),
                name: subjectNameMap.get(subjectKey(code)),
              }))
            : undefined,
        // A picked template bypasses the LLM planning node — its steps
        // materialize with THIS run's scope/question args.
        planTemplateId: planTemplateId ?? undefined,
      });
      if (res.stopped) {
        // The Stop click won: the daemon cancelled the thread and its
        // settled plan rides the auto-opened thread graph above.
        setNoticeMsg("Summary generation stopped");
        refreshHistory();
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
      refreshHistory();
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
          onChange={handleSecTypeChange}
          options={SUMMARY_SEC_TYPES}
        />
        {nav.secType !== "all" && (
          <CodeSearchBar
            activeCode={nav.searchCode}
            onSearch={handleScopeSearch}
            onClear={nav.handleClearSearch}
            placeholder={
              nav.secType === "industry"
                ? "Industry id (e.g. BANKS) — find & tag"
                : nav.secType === "etf"
                  ? "ETF code (e.g. 510050) — find & tag"
                  : nav.secType === "stock"
                    ? "Stock code (e.g. 600000) — find & tag"
                    : "Index code (e.g. 000300) — find & tag"
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
           otherwise the L3 row is MULTI-SELECT — every tick lands in the
           Scopes panel's subject tags) ---- */}
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
        multiSelectItems
        selectedItemCodes={scopeCodes}
        onMultiItemSelected={setScopeCodes}
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
        <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
          <Typography variant="caption" color="text.secondary" sx={{ minWidth: 0 }}>
            Summary History
            {subjects.length > 0
              ? ` — scoped to ${scopeLabel} · market-wide runs included`
              : " — all runs"}
          </Typography>
          <Tooltip title="Search a run by its thread id — paste the full id or a leading prefix; the lookup ignores the scope pick">
            <TextField
              size="small"
              placeholder="Search by thread id"
              value={threadSearch}
              onChange={(e) => setThreadSearch(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void searchRunByThreadId();
              }}
              sx={{ ml: "auto", width: 300 }}
              InputProps={{
                endAdornment: (
                  <InputAdornment position="end">
                    <IconButton
                      size="small"
                      edge="end"
                      aria-label="Search run by thread id"
                      disabled={threadSearch.trim() === "" || threadSearching}
                      onClick={() => void searchRunByThreadId()}
                    >
                      {threadSearching ? (
                        <CircularProgress size={16} color="inherit" />
                      ) : (
                        <Search fontSize="small" />
                      )}
                    </IconButton>
                  </InputAdornment>
                ),
              }}
            />
          </Tooltip>
        </Box>
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
            datesLoading
              ? "Loading run history…"
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
          subjects={subjects}
          onRemoveSubject={removeScopeCode}
          onAddSubject={addScopeCode}
          subjectSuggestions={subjectSuggestions}
          subjectKind={itemKindFor(nav.secType)}
        />
        <SkillsPromptsPanel
          prompts={promptsEffective.filter(isSelectablePrompt)}
          toolPrompts={promptsEffective.filter(isToolPrompt)}
          loading={promptsLoading}
          selectedPrompts={selectedPrompts}
          onTogglePrompt={togglePrompt}
          openPromptKey={openPromptKey}
          onOpenPrompt={openPromptByKey}
          onAddLabel={openPromptLabels}
          scopeHasCodes={scopeHasCodes}
        />
        <RunLimitsPanel />
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
                sx={{
                  display: "flex",
                  gap: 1,
                  alignItems: "center",
                  flexShrink: 0,
                  flexWrap: "wrap",
                }}
              >
                {/* Plan template pick — a SAVED plan (the Thread Graph's
                    Save button) to start from instead of the LLM planning
                    session: the plan materializes instantly with this
                    run's scope/question args filling the template's
                    placeholders. */}
                <Tooltip
                  title={
                    planTemplates.length === 0
                      ? "No saved plan templates yet — settle a run, then use its thread graph's “Save plan” button"
                      : "Start from a saved plan template — the plan finishes instantly (placeholders filled with this run's scope), skipping LLM planning"
                  }
                >
                  <TextField
                    select
                    size="small"
                    label="Plan template"
                    value={planTemplateId ?? ""}
                    disabled={generating || planTemplates.length === 0}
                    onChange={(e) =>
                      setPlanTemplateId(
                        e.target.value === "" ? null : Number(e.target.value),
                      )
                    }
                    sx={{ minWidth: 210 }}
                  >
                    <MenuItem value="">LLM planning (default)</MenuItem>
                    {planTemplates.map((t) => (
                      <MenuItem
                        key={t.template_id}
                        value={t.template_id}
                        sx={{
                          "& .tpl-delete": { visibility: "hidden" },
                          "&:hover .tpl-delete": { visibility: "visible" },
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
                            {t.name} · {t.n_steps} steps
                            {t.placeholders.length > 0
                              ? ` · ${t.placeholders
                                  .map((p) => `{{${p}}}`)
                                  .join(" ")}`
                              : ""}
                          </Typography>
                          <IconButton
                            className="tpl-delete"
                            size="small"
                            edge="end"
                            aria-label="Delete plan template"
                            disabled={generating}
                            onClick={(e) => {
                              e.stopPropagation();
                              void removePlanTemplate(t.template_id);
                            }}
                          >
                            <Close fontSize="small" />
                          </IconButton>
                        </Box>
                      </MenuItem>
                    ))}
                  </TextField>
                </Tooltip>
                <Box
                  onMouseEnter={() => setGenerateHovered(true)}
                  onMouseLeave={() => setGenerateHovered(false)}
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
              </Box>
            )}
          </Box>
          {openPrompt ? (
            <PromptContent
              prompt={openPrompt}
              editing={editingPrompt}
              draft={draftInstruction}
              onDraftChange={setDraftInstruction}
              labelsSlot={
                openBasePrompt ? (
                  <PromptLabels
                    baseline={{
                      bound: openBasePrompt.bound_labels,
                      optional: openBasePrompt.optional_labels,
                    }}
                    effective={{
                      bound: openPrompt.bound_labels,
                      optional: openPrompt.optional_labels,
                    }}
                    sessionModified={
                      sessionLabels[promptIdentity(openPrompt)] !== undefined
                    }
                    suggestions={labelSuggestions}
                    focusSignal={labelsFocusSignal}
                    onCommit={(group, label, add, permanent) =>
                      void applyLabelChange(
                        promptIdentity(openPrompt),
                        group,
                        label,
                        add,
                        permanent,
                      )
                    }
                  />
                ) : undefined
              }
            />
          ) : resultView === "langgraph" ? (
            <ThreadGraphView
              threadId={liveThreadId ?? selectedThreadId}
              expectLive={generating}
              onSettled={refreshHistory}
              refreshSignal={graphSignal}
              onPlanTemplateSaved={refreshPlanTemplates}
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
