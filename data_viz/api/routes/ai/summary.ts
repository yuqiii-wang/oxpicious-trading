/**
 * Analysis-summary routes — POST /summary (the deep-agent run), POST
 * /summary/stop and GET /summary/active (the in-flight controls) and the
 * run-history reads (GET /summary-runs/dates, GET /summary-runs, DELETE
 * /summary-runs/:threadId). Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import { HttpError, sendError, wrapAsync } from "../../lib/http.js";
import { publishAiEvent } from "../../services/ai/ai-events.service.js";
import type {
  AiSummaryAnalysisItem,
  AiSummaryScope,
  PromptLabelSets,
} from "../../services/ai/ai-summary.service.js";
import {
  deleteSummaryRun,
  generateAnalysisSummary,
  listActiveSummaryRuns,
  listSummaryRunDates,
  listSummaryRuns,
  stopAnalysisSummary,
} from "../../services/ai/ai-summary.service.js";
import { encodeLabelParam } from "../../services/infra/label-codes.js";
import { str } from "./_shared.js";

const router = Router();

// ---------------------------------------------------------------------------
// Analysis Summary — the AI section's summary tab. The request IS the JSON
// spec of what to summarize: the selected tables (the Scopes panel's
// ticked KG tables — key + label, one summary section each; the same
// list rides forcedTables as the run's KG scope) and the picked
// llm.ai_prompts prompt_key values; the service hands everything to the
// deep-agents daemon as its structured data.
// ---------------------------------------------------------------------------

// GET /api/ai/summary-runs[/dates] — the run-history strip's two reads
// (see the header doc). Shared parsing: the limit clamp + the
// classification-nav pick (sec_type is a label NAME encoded here — an
// unknown name is a 400, the researchLogicCode precedent; code the
// subject id — REPEATED params are the multi-pick subject set, the
// summary page's tag list).
function parseSummaryRunFilters(req: Request): {
  limit: number;
  secTypeCode: number | null;
  codes: string[] | null;
} {
  const parsed = Number(req.query.limit);
  const limit = Math.min(1000, Math.max(1, Number.isFinite(parsed) ? Math.floor(parsed) : 200));
  const secTypeName = str(req.query.sec_type);
  let secTypeCode: number | null = null;
  if (secTypeName !== undefined) {
    secTypeCode = encodeLabelParam("sec_type", secTypeName);
    if (secTypeCode === null) {
      throw new HttpError(400, "INVALID_PARAM", `Unknown 'sec_type': ${secTypeName}`);
    }
  }
  // code may repeat (the multi-pick) — collect every param, trimmed.
  const rawCode = req.query.code;
  const codes = (Array.isArray(rawCode) ? rawCode : rawCode === undefined ? [] : [rawCode])
    .map((c) => String(c).trim())
    .filter((c) => c !== "");
  return { limit, secTypeCode, codes: codes.length > 0 ? codes : null };
}

// GET /api/ai/summary-runs/dates — phase 1: the strip's per-day aggregates
// (one row per GMT operational day that has runs; no per-thread rows).
router.get("/summary-runs/dates", wrapAsync(async (req: Request, res: Response) => {
  const f = parseSummaryRunFilters(req);
  res.json(await listSummaryRunDates(f.limit, { secTypeCode: f.secTypeCode, codes: f.codes }));
}));

// GET /api/ai/summary-runs — phase 2: the per-thread rows, narrowed to the
// picked day with date= (YYYY-MM-DD) when the strip's dot click loads one,
// or to the search-by-id lookup with thread_id= (case-insensitive PREFIX —
// a pasted full uuid4().hex id is the exact case; it ignores the nav pick,
// the Summary History's search box).
router.get("/summary-runs", wrapAsync(async (req: Request, res: Response) => {
  const f = parseSummaryRunFilters(req);
  const date = str(req.query.date);
  if (date !== undefined && !/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    throw new HttpError(400, "INVALID_PARAM", `Bad 'date' (YYYY-MM-DD expected): ${date}`);
  }
  res.json(
    await listSummaryRuns(f.limit, {
      secTypeCode: f.secTypeCode,
      codes: f.codes,
      date: date ?? null,
      threadId: str(req.query.thread_id)?.trim() ?? null,
    }),
  );
}));

// DELETE /api/ai/summary-runs/:thread_id — delete one run entry (the
// Summary History dropdown's hover-delete cross): the thread's full
// purge in one transaction, the runs-prune order (llm_requests chains
// → states → contexts → identities → store items → langgraph
// checkpoints). 404 unknown thread; 409 while the daemon still reports
// the thread active (the in-flight run must settle or be stopped
// first). On success a bridge-side "thread_deleted" event rides the
// /api/ai/events channel so every open page refreshes its strip and
// collapses an open graph of the thread (the reads are no-store, but a
// settled page's poll loop has stopped — the push IS the refresh).
router.delete("/summary-runs/:threadId", wrapAsync(async (req: Request, res: Response) => {
  const threadId = str(req.params.threadId);
  if (!threadId) throw HttpError.missingParam("thread_id");
  const out = await deleteSummaryRun(threadId);
  if (out === "missing") {
    throw HttpError.notFound(`thread ${threadId} not found`);
  }
  if (out === "running") {
    throw HttpError.conflict("run is still in flight — stop it or wait for it to settle");
  }
  publishAiEvent({ type: "thread_deleted", thread_id: threadId });
  res.json({ deleted: true });
}));

// POST /api/ai/summary/stop — cancel in-flight summary run(s): forwards
// to the deep-agents daemon's POST /stop (the langgraph thread winds
// down at its current await; the daemon resolves the response only
// AFTER the unwind + the whole-chain settle — the confirmed stop, so
// the UI's one post-stop refresh draws the settled chain). An
// optional { threadId } cancels ONE run of the several the parallel-run
// cap now admits; the thread-less form (the UI's Stop button) cancels
// every active run as before. The still-pending POST /api/ai/summary of
// a cancelled run then resolves with stopped: true. stopped === 0 means
// nothing was running.
router.post("/summary/stop", wrapAsync(async (req: Request, res: Response) => {
  const threadId =
    typeof req.body?.threadId === "string" && req.body.threadId.trim() !== ""
      ? req.body.threadId.trim()
      : undefined;
  res.json(await stopAnalysisSummary(threadId));
}));

// GET /api/ai/summary/active — the in-flight run(s)' thread ids (empty
// when idle): forwards to the daemon's GET /active. The UI's live-view
// poll reads this from the Generate click onward and opens the thread
// graph on the first id — ~20-80s before the plan phase persists any
// rows the strip could show.
router.get("/summary/active", wrapAsync(async (_req: Request, res: Response) => {
  res.json({ active: await listActiveSummaryRuns() });
}));

router.post("/summary", wrapAsync(async (req: Request, res: Response) => {
  const rawItems = Array.isArray(req.body?.analysisItems)
    ? req.body.analysisItems
    : [];
  const analysisItems: AiSummaryAnalysisItem[] = [];
  for (const raw of rawItems) {
    const key = typeof raw?.key === "string" ? raw.key.trim() : "";
    const label = typeof raw?.label === "string" ? raw.label.trim() : "";
    if (!key || !label) {
      throw new HttpError(
        400,
        "INVALID_PARAM",
        "each analysisItems entry needs a 'key' and a 'label'",
      );
    }
    analysisItems.push({
      key,
      label,
      description:
        typeof raw?.description === "string" && raw.description.trim() !== ""
          ? raw.description
          : undefined,
    });
  }
  if (analysisItems.length === 0) throw HttpError.missingParam("analysisItems");
  const prompts =
    Array.isArray(req.body?.prompts) &&
    req.body.prompts.every((s: unknown) => typeof s === "string" && s.trim() !== "")
      ? (req.body.prompts as string[])
      : undefined;
  // Scope picks — the classification-nav subject focus. `scopes` is the
  // MULTI-PICK form (the summary page's tag list, one entry per
  // subject). sec_type must be one of the four; each entry's subject id
  // must match its kind (code vs industry).
  const SUMMARY_SEC_TYPES = new Set(["industry", "index", "etf", "stock"]);
  const parseScopeEntry = (
    raw: unknown,
    label: string,
  ): { scope?: AiSummaryScope; error?: string } => {
    if (typeof raw !== "object" || raw === null) {
      return { error: `${label} must be an object` };
    }
    const secType =
      typeof (raw as { secType?: unknown }).secType === "string"
        ? (raw as { secType: string }).secType
        : "";
    if (!SUMMARY_SEC_TYPES.has(secType)) {
      return {
        error: `${label}.secType must be one of industry|index|etf|stock, got '${secType}'`,
      };
    }
    const code =
      typeof (raw as { code?: unknown }).code === "string"
        ? (raw as { code: string }).code.trim()
        : "";
    const industry =
      typeof (raw as { industry?: unknown }).industry === "string"
        ? (raw as { industry: string }).industry.trim()
        : "";
    if (secType === "industry" ? !industry : !code) {
      return {
        error:
          secType === "industry"
            ? `${label}.industry is required for secType 'industry'`
            : `${label}.code is required for secType ${secType}`,
      };
    }
    const name =
      typeof (raw as { name?: unknown }).name === "string" &&
      (raw as { name: string }).name.trim() !== ""
        ? (raw as { name: string }).name
        : undefined;
    return {
      scope: {
        secType: secType as AiSummaryScope["secType"],
        ...(secType === "industry" ? { industry } : { code }),
        ...(name !== undefined ? { name } : {}),
      },
    };
  };
  let scopes: AiSummaryScope[] | undefined;
  const rawScopes = req.body?.scopes;
  if (rawScopes !== undefined && rawScopes !== null) {
    if (!Array.isArray(rawScopes)) {
      throw new HttpError(
        400,
        "INVALID_PARAM",
        "scopes must be an array of {secType, code | industry, name?}",
      );
    }
    const parsed: AiSummaryScope[] = [];
    for (let i = 0; i < rawScopes.length; i++) {
      const entry = parseScopeEntry(rawScopes[i], `scopes[${i}]`);
      if (entry.error !== undefined || entry.scope === undefined) {
        throw new HttpError(400, "INVALID_PARAM", entry.error ?? `scopes[${i}] is invalid`);
      }
      parsed.push(entry.scope);
    }
    scopes = parsed.length > 0 ? parsed : undefined;
  }
  // The FORCED-TABLE-SCOPE — the ticked KG tables (the Scopes panel's
  // tree / the Generate dialog's knob): the run's KG surface serves
  // ONLY these (the serving layer filters structurally) and the same
  // list mirrors analysis_items. Strings only, trimmed; an empty or
  // malformed shape is a 400 (never silently dropped — the session
  // believes the filter rides).
  let forcedTables: string[] | undefined;
  const rawTables = req.body?.forcedTables;
  if (rawTables !== undefined && rawTables !== null) {
    if (
      !Array.isArray(rawTables) ||
      !rawTables.every((s: unknown) => typeof s === "string" && s.trim() !== "")
    ) {
      throw new HttpError(
        400,
        "INVALID_PARAM",
        "forcedTables must be an array of non-empty table names",
      );
    }
    forcedTables = (rawTables as string[]).map((t) => t.trim());
  }
  const question =
    typeof req.body?.question === "string" && req.body.question.trim() !== ""
      ? req.body.question
      : undefined;
  // A SAVED PLAN TEMPLATE to plan from instead of the LLM
  // (llm.deep_agent_plan_templates — the Thread Graph Save button's
  // placeholder-templated plan): rides the daemon /run as
  // plan_template_id and the plan node materializes it with this
  // run's input args, zero planning LLM calls.
  const planTemplateId =
    typeof req.body?.planTemplateId === "number" &&
    Number.isInteger(req.body.planTemplateId) && req.body.planTemplateId >= 1
      ? req.body.planTemplateId
      : undefined;

  // The Labels editor's run-scoped overrides ("just this run" saves) —
  // prompt identity → { bound, optional }, strings only; empty or
  // malformed shapes are a 400 (never silently dropped — the session
  // believes they ride).
  let promptLabels: Record<string, PromptLabelSets> | undefined;
  const rawLabels = req.body?.promptLabels;
  if (rawLabels !== undefined && rawLabels !== null) {
    if (typeof rawLabels !== "object" || Array.isArray(rawLabels)) {
      throw new HttpError(
        400,
        "INVALID_PARAM",
        "promptLabels must be an object of identity → { bound, optional }",
      );
    }
    promptLabels = {};
    for (const [identity, sets] of Object.entries(
      rawLabels as Record<string, unknown>,
    )) {
      const bound = (sets as { bound?: unknown } | null)?.bound;
      const optional = (sets as { optional?: unknown } | null)?.optional;
      if (
        identity.trim() === "" ||
        !Array.isArray(bound) ||
        !bound.every((s) => typeof s === "string") ||
        !Array.isArray(optional) ||
        !optional.every((s) => typeof s === "string")
      ) {
        throw new HttpError(
          400,
          "INVALID_PARAM",
          "promptLabels values must be { bound: string[], optional: string[] }",
        );
      }
      promptLabels[identity] = {
        bound: bound as string[],
        optional: optional as string[],
      };
    }
    if (Object.keys(promptLabels).length === 0) promptLabels = undefined;
  }

  const out = await generateAnalysisSummary({
    analysisItems,
    prompts,
    promptLabels,
    scopes,
    forcedTables,
    question,
    planTemplateId,
  });
  // Stopped run (POST /api/ai/summary/stop while this was in flight):
  // a success-shaped payload with stopped — the UI clears its
  // generating state and may show the settled plan, not an error.
  if (out.stopped) {
    res.json({
      success: true,
      stopped: true,
      answer: "",
      thread_id: out.threadId,
      plan: out.plan,
    });
    return;
  }
  if (!out.success) {
    sendError(res, 500, out.stderrTail ?? "deep agent failed", "UPSTREAM_FAILED");
    return;
  }
  res.json({
    success: true,
    answer: out.answer,
    model: out.model,
    steps: out.steps,
    n_tool_calls: out.nToolCalls,
    thread_id: out.threadId,
    plan: out.plan,
  });
}));

export default router;
