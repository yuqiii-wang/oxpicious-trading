/**
 * AI API routes — read access to the LLM Q&A knowledge base in the text
 * schema (text.llm_qa / text.llm_qa_refs / text.news_digestions, written by
 * llm_agents) and to the persisted interactive ask history
 * (text.llm_qa_by_ask family + multi_media.src_images, written by
 * llm_agents.llm_ask.storage). Mirrors the news routes' design:
 *
 *   GET /api/ai/calendar?sector_id=&industry_id=&search=
 *     Per-day Q&A counts for the date-event strip's dots; co-filtered by
 *     the classification scope + keyword search so the bar reacts to the
 *     nav, exactly like /api/news/calendar. Inactive rows never surface.
 *
 *   GET /api/ai/items?sector_id=&industry_id=&search=&date=&date_from=&date_to=&limit=&offset=
 *     One page of Q&A rows for the same filters (+ optional single-day pick
 *     from the date strip, or an inclusive date_from/date_to range window
 *     around the pick). answer is truncated to a 240-char snippet; the
 *     full row comes from /qa below.
 *
 *   GET /api/ai/qa?qa_id=
 *     Full Q&A row (untruncated answer + context) with its per-ref
 *     resolutions joined back to the text.news articles and the average
 *     digestion sentiment across the refs — the feed card's click-to-expand
 *     fetch (mirrors /api/news/item).
 *
 *   POST /api/ai/ask
 *     Interactive chart question (the AI Ask "?" beside chart titles):
 *     { question, plotInfo, screenshots?, themeMode, onlineSearch?,
 *     searchQuery? } → spawns llm_agents.llm_ask (chart-adviser mode) via
 *     the WSL py-runner (payload by temp file — see
 *     services/ai-ask.service.ts) and returns { success, answer, model }.
 *     Screenshots ride along as images when the model has image input
 *     (vision config on the python side); onlineSearch=true routes through
 *     the online-search agent instead (cited [来源：ref_N] answer,
 *     text-only). Every ask is persisted server-side (python side,
 *     fail-soft) into the ask-history tables below.
 *
 * Ask history (the AiPage "QA by Ask" toggle — same filter params, plus a
 * picked code filters llm_qa_by_ask.code directly; search terms hit
 * question/answer OR the derived keyword rows):
 *
 *   GET /api/ai/ask-calendar?sector_id=&industry_id=&code=&search=
 *   GET /api/ai/ask-items?...&date=&date_from=&date_to=&limit=&offset=
 *   GET /api/ai/ask-qa?ask_id=           — full row + keywords + image list
 *   GET /api/ai/ask-image?image_id=      — the screenshot bytes themselves
 *
 * Research Q&A (the AiPage "Research" toggle — the research-logic knowledge
 * base text.llm_qa_by_research, written by the five-request generation
 * flow; same filter params as the knowledge-base routes, plus a
 * logic_label pick encoded to its research_logic code):
 *
 *   GET /api/ai/research-calendar?sector_id=&industry_id=&search=&logic_label=
 *   GET /api/ai/research-items?...&date=&date_from=&date_to=&limit=&offset=
 *   GET /api/ai/research-qa?qa_id=        — summary + four dimensions + sources
 *
 * Analysis Summary (the AI section's "Analysis Summary" tab — the deep
 * agent, not the chart adviser):
 *
 *   GET /api/ai/prompts
 *     The active text.ai_prompts rows (the Skills picker's catalog; DDL +
 *     seed database/sql/text/08_ai_prompts.sql) — prompt_key/prompt_type
 *     (+ decoded prompt_type_label)/title/version/instruction/note/ordinal.
 *     The picker sends the picked skills- and plan-type prompt_key values
 *     back in the summary request; the deep agent fetches their
 *     instruction texts itself over its DB tools.
 *
 *   PUT /api/ai/prompts/:prompt_key  { instruction }
 *     Save an edited prompt (the prompt view's Edit → Save): the active
 *     row's instruction is replaced in place and its version bumps by one
 *     (prompt_key stays the stable join key). Returns the refreshed row;
 *     404 when no active row carries the key.
 *
 *   POST /api/ai/summary
 *     { analysisItems: [{ key, label, description?, insight? }], prompts?,
 *     scope?, question? } → enriches the items with their insight-registry
 *     page docs + metric db-search recipes and POSTs the whole spec as
 *     JSON to the deep-agents FastAPI daemon (llm_agents.deep_agents.server
 *     — see services/ai-summary.service.ts), returning
 *     { success, answer, model?, steps?, n_tool_calls?, thread_id?, plan? }.
 *     thread_id + plan (the text.deep_agent_states snapshot: ordinal /
 *     node / title / status per planned step) are present when the run
 *     picked plan prompts and the plan phase persisted. scope is the
 *     classification-nav pick that focuses the run:
 *     { secType: industry|index|etf|stock, code? | industry?, name? } —
 *     absent = market-wide.
 *
 *   POST /api/ai/summary/stop
 *     Cancel the in-flight summary run (forwards to the daemon's
 *     POST /stop) → { stopped, thread_ids }; stopped is how many runs
 *     were actually cancelled (0 = the run had already finished). The
 *     still-pending POST /api/ai/summary then resolves
 *     { success: true, stopped: true, answer: "", thread_id?, plan? } —
 *     the plan snapshot settled with the stop.
 *
 *   GET /api/ai/summary/active
 *     The in-flight run(s)' thread ids (forwards to the daemon's
 *     GET /active) → { active: [{ thread_id, started_at }] }, empty
 *     when idle or the daemon is down. The UI's live-view poll reads
 *     this from the Generate click onward and opens the thread graph
 *     on the first id — before the plan phase persists any rows.
 *
 *   GET /api/ai/summary-runs?limit=&sec_type=&code=
 *     The Analysis Summary page's date-event strip source: one row per
 *     planned deep-agent run (a text.deep_agent_states thread — runs
 *     without a plan phase create no thread), with the plan goal, the
 *     per-status step counts, the first/last Shanghai-local times and
 *     the run's scope pick when scoped (scope_sec_type decoded /
 *     scope_code / scope_name — text.deep_agent_identities, the code
 *     <-> thread_id link the runner's plan node writes). sec_type is a
 *     label NAME (index|etf|stock|industry, encoded via label-codes)
 *     and code the subject id (a security code or industry id) — the
 *     classification-nav filter; either alone filters on that axis,
 *     both = the exact pick; absent = every run. The filtered read is
 *     an indexed identity lookup, not a states scan. run_date is the
 *     GMT operational day of the run's first state (a new day starts
 *     at Beijing 08:00, the same boundary as text.llm_requests.date).
 *     Most recent first; limit clamps to 1..1000 (default 200).
 *
 *   DELETE /api/ai/summary-runs/:thread_id
 *     Delete one run entry (the Summary History dropdown's hover-delete
 *     cross): the thread's full purge in one transaction, the runs-prune
 *     order (llm_requests chains → states → contexts → identities →
 *     store items → langgraph checkpoints). 404 unknown thread; 409
 *     while the daemon still reports the thread active (the in-flight
 *     run must settle or be stopped first). Success broadcasts a
 *     bridge-side "thread_deleted" frame on /api/ai/events — every open
 *     page refreshes its strip and collapses an open graph of the
 *     thread.
 */
import { Router, type Request, type Response } from "express";
import { askLlm } from "../services/ai-ask.service.js";
import { publishAiEvent } from "../services/ai-events.service.js";
import { listAiPrompts, updateAiPromptInstruction } from "../services/ai-prompts.service.js";
import type {
  AiSummaryAnalysisItem,
  AiSummaryScope,
} from "../services/ai-summary.service.js";
import {
  deleteSummaryRun,
  generateAnalysisSummary,
  listActiveSummaryRuns,
  listSummaryRuns,
  stopAnalysisSummary,
} from "../services/ai-summary.service.js";
import { getAiQa, listAiCalendar, listAiItems } from "../services/ai.service.js";
import {
  getAiResearchQa,
  listAiResearchCalendar,
  listAiResearchItems,
} from "../services/ai-research.service.js";
import {
  getAskHistoryDetail,
  getAskHistoryImage,
  listAskHistoryCalendar,
  listAskHistoryItems,
} from "../services/ai-ask-history.service.js";
import { encodeLabelParam } from "../services/label-codes.js";

const router = Router();

function str(v: unknown): string | undefined {
  return typeof v === "string" && v.trim() !== "" ? v.trim() : undefined;
}

/** industry_ids list — comma-joined. PRESENT (even empty) scopes to exactly
 *  that set (empty → no rows); ABSENT leaves the scope to the other params. */
function industryIds(v: unknown): string[] | null {
  if (v === undefined) return null;
  return String(v).split(",").map((s) => s.trim()).filter(Boolean);
}

/** Boolean flag query param — "1" (URLSearchParams-friendly) or "true"
 *  (String(boolean) from the api-client's qs). Anything else = off. */
function bool(v: unknown): boolean {
  return v === "1" || v === "true";
}

router.get("/calendar", async (req: Request, res: Response) => {
  try {
    res.json(
      await listAiCalendar({
        sectorId: str(req.query.sector_id) ?? null,
        industryId: str(req.query.industry_id) ?? null,
        industryIds: industryIds(req.query.industry_ids),
        search: str(req.query.search) ?? null,
        bigFluctuationOnly: bool(req.query.is_big_fluctuation),
      }),
    );
  } catch (err) {
    console.error("[ai/calendar] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/items", async (req: Request, res: Response) => {
  try {
    const limit = Number(req.query.limit);
    const offset = Number(req.query.offset);
    res.json(
      await listAiItems({
        sectorId: str(req.query.sector_id) ?? null,
        industryId: str(req.query.industry_id) ?? null,
        industryIds: industryIds(req.query.industry_ids),
        search: str(req.query.search) ?? null,
        bigFluctuationOnly: bool(req.query.is_big_fluctuation),
        date: str(req.query.date) ?? null,
        dateFrom: str(req.query.date_from) ?? null,
        dateTo: str(req.query.date_to) ?? null,
        limit: Number.isFinite(limit) ? limit : null,
        offset: Number.isFinite(offset) ? offset : null,
      }),
    );
  } catch (err) {
    console.error("[ai/items] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// GET /api/ai/qa?qa_id= — full Q&A row + refs (the expand-card fetch).
router.get("/qa", async (req: Request, res: Response) => {
  try {
    const qaId = Number(req.query.qa_id);
    if (!Number.isFinite(qaId) || qaId <= 0) {
      res.status(400).json({ error: "Missing 'qa_id'" });
      return;
    }
    const row = await getAiQa(qaId);
    if (row === null) {
      res.status(404).json({ error: `qa_id ${qaId} not found` });
      return;
    }
    res.json(row);
  } catch (err) {
    console.error("[ai/qa] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// ---------------------------------------------------------------------------
// Ask history — the persisted interactive asks (AiPage "QA by Ask" toggle).
// Same filter params as the knowledge-base routes above, plus `code` (a
// picked security filters the ask's primary code directly).
// ---------------------------------------------------------------------------

router.get("/ask-calendar", async (req: Request, res: Response) => {
  try {
    res.json(
      await listAskHistoryCalendar({
        sectorId: str(req.query.sector_id) ?? null,
        industryId: str(req.query.industry_id) ?? null,
        industryIds: industryIds(req.query.industry_ids),
        code: str(req.query.code) ?? null,
        search: str(req.query.search) ?? null,
      }),
    );
  } catch (err) {
    console.error("[ai/ask-calendar] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/ask-items", async (req: Request, res: Response) => {
  try {
    const limit = Number(req.query.limit);
    const offset = Number(req.query.offset);
    res.json(
      await listAskHistoryItems({
        sectorId: str(req.query.sector_id) ?? null,
        industryId: str(req.query.industry_id) ?? null,
        industryIds: industryIds(req.query.industry_ids),
        code: str(req.query.code) ?? null,
        search: str(req.query.search) ?? null,
        date: str(req.query.date) ?? null,
        dateFrom: str(req.query.date_from) ?? null,
        dateTo: str(req.query.date_to) ?? null,
        limit: Number.isFinite(limit) ? limit : null,
        offset: Number.isFinite(offset) ? offset : null,
      }),
    );
  } catch (err) {
    console.error("[ai/ask-items] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// GET /api/ai/ask-qa?ask_id= — full ask row + keywords + image list.
router.get("/ask-qa", async (req: Request, res: Response) => {
  try {
    const askId = Number(req.query.ask_id);
    if (!Number.isFinite(askId) || askId <= 0) {
      res.status(400).json({ error: "Missing 'ask_id'" });
      return;
    }
    const row = await getAskHistoryDetail(askId);
    if (row === null) {
      res.status(404).json({ error: `ask_id ${askId} not found` });
      return;
    }
    res.json(row);
  } catch (err) {
    console.error("[ai/ask-qa] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// GET /api/ai/ask-image?image_id= — one screenshot's bytes (the feed card's
// thumbnail / zoom source). Cached client-side by the browser via the
// immutable-by-id URL.
router.get("/ask-image", async (req: Request, res: Response) => {
  try {
    const imageId = Number(req.query.image_id);
    if (!Number.isFinite(imageId) || imageId <= 0) {
      res.status(400).json({ error: "Missing 'image_id'" });
      return;
    }
    const image = await getAskHistoryImage(imageId);
    if (image === null) {
      res.status(404).json({ error: `image_id ${imageId} not found` });
      return;
    }
    res.set("Content-Type", image.mime_type);
    res.set("Cache-Control", "private, max-age=86400");
    res.send(image.data);
  } catch (err) {
    console.error("[ai/ask-image] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// ---------------------------------------------------------------------------
// Research Q&A — the research-logic knowledge base (AiPage "Research"
// toggle). Same filter params as the knowledge-base routes; logic_label is
// a label NAME (growth/…) encoded to its research_logic code here — an
// unknown name is a 400 (the monitor.ts encode precedent).
// ---------------------------------------------------------------------------

/** logic_label query param -> research_logic code, or null (absent). */
function researchLogicCode(
  res: Response,
  raw: unknown,
): number | null | undefined {
  const name = str(raw);
  if (name === undefined) return null; // absent — all labels
  const code = encodeLabelParam("research_logic", name);
  if (code === null) {
    res.status(400).json({ error: `Unknown 'logic_label': ${name}` });
    return undefined; // signals the handler to stop
  }
  return code;
}

router.get("/research-calendar", async (req: Request, res: Response) => {
  try {
    const logicLabel = researchLogicCode(res, req.query.logic_label);
    if (logicLabel === undefined) return;
    res.json(
      await listAiResearchCalendar({
        sectorId: str(req.query.sector_id) ?? null,
        industryId: str(req.query.industry_id) ?? null,
        industryIds: industryIds(req.query.industry_ids),
        search: str(req.query.search) ?? null,
        logicLabel,
      }),
    );
  } catch (err) {
    console.error("[ai/research-calendar] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

router.get("/research-items", async (req: Request, res: Response) => {
  try {
    const limit = Number(req.query.limit);
    const offset = Number(req.query.offset);
    const logicLabel = researchLogicCode(res, req.query.logic_label);
    if (logicLabel === undefined) return;
    res.json(
      await listAiResearchItems({
        sectorId: str(req.query.sector_id) ?? null,
        industryId: str(req.query.industry_id) ?? null,
        industryIds: industryIds(req.query.industry_ids),
        search: str(req.query.search) ?? null,
        logicLabel,
        date: str(req.query.date) ?? null,
        dateFrom: str(req.query.date_from) ?? null,
        dateTo: str(req.query.date_to) ?? null,
        limit: Number.isFinite(limit) ? limit : null,
        offset: Number.isFinite(offset) ? offset : null,
      }),
    );
  } catch (err) {
    console.error("[ai/research-items] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// GET /api/ai/research-qa?qa_id= — summary + four dimensions + sources.
router.get("/research-qa", async (req: Request, res: Response) => {
  try {
    const qaId = Number(req.query.qa_id);
    if (!Number.isFinite(qaId) || qaId <= 0) {
      res.status(400).json({ error: "Missing 'qa_id'" });
      return;
    }
    const row = await getAiResearchQa(qaId);
    if (row === null) {
      res.status(404).json({ error: `qa_id ${qaId} not found` });
      return;
    }
    res.json(row);
  } catch (err) {
    console.error("[ai/research-qa] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// POST /api/ai/ask — chart question → llm_agents.llm_ask (via WSL).
router.post("/ask", async (req: Request, res: Response) => {
  try {
    const question =
      typeof req.body?.question === "string" ? req.body.question.trim() : "";
    const plotInfo =
      typeof req.body?.plotInfo === "object" && req.body.plotInfo !== null
        ? (req.body.plotInfo as Record<string, unknown>)
        : null;
    if (!question) {
      res.status(400).json({ success: false, error: "Missing 'question'" });
      return;
    }
    if (!plotInfo) {
      res.status(400).json({ success: false, error: "Missing 'plotInfo'" });
      return;
    }
    const screenshots =
      Array.isArray(req.body?.screenshots) && req.body.screenshots.every((s: unknown) => typeof s === "string")
        ? (req.body.screenshots as string[])
        : undefined;
    const themeMode =
      typeof req.body?.themeMode === "string" ? req.body.themeMode : undefined;
    const onlineSearch = req.body?.onlineSearch === true;
    // The modal's online-search line — meaningful only on the online-search
    // path (the plain adviser ignores it; the service passes it through to
    // python, which falls back to the question when absent/blank).
    const searchQuery =
      typeof req.body?.searchQuery === "string" && req.body.searchQuery.trim() !== ""
        ? req.body.searchQuery
        : undefined;

    const out = await askLlm({ question, plotInfo, screenshots, themeMode, onlineSearch, searchQuery });
    if (!out.success) {
      res.status(500).json({ success: false, error: out.stderrTail ?? "llm_ask failed" });
      return;
    }
    res.json({ success: true, answer: out.answer, model: out.model });
  } catch (err) {
    console.error("[ai/ask] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

// ---------------------------------------------------------------------------
// Analysis Summary — the AI section's summary tab. The request IS the JSON
// spec of what to summarize: the selected analysis items (keys + labels +
// optional insight identity) and the picked text.ai_prompts prompt_key
// values; the service enriches the items from the insight registry and
// hands everything to the deep-agents daemon as its structured data.
// ---------------------------------------------------------------------------

// GET /api/ai/prompts — the Skills picker's catalog (text.ai_prompts).
router.get("/prompts", async (_req: Request, res: Response) => {
  try {
    res.json(await listAiPrompts());
  } catch (err) {
    console.error("[ai/prompts] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// PUT /api/ai/prompts/:prompt_key — save an edited instruction (the prompt
// view's Edit → Save): the row's text is replaced in place and its version
// bumps by one; responds with the refreshed row, 404 when no active row
// carries the key.
router.put("/prompts/:promptKey", async (req: Request, res: Response) => {
  try {
    const promptKey = str(req.params.promptKey);
    const instruction =
      typeof req.body?.instruction === "string" ? req.body.instruction.trim() : "";
    if (!promptKey) {
      res.status(400).json({ error: "Missing 'prompt_key'" });
      return;
    }
    if (!instruction) {
      res.status(400).json({ error: "Missing 'instruction'" });
      return;
    }
    const row = await updateAiPromptInstruction(promptKey, instruction);
    if (row === null) {
      res.status(404).json({ error: `prompt_key ${promptKey} not found or inactive` });
      return;
    }
    res.json(row);
  } catch (err) {
    console.error("[ai/prompts put] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// GET /api/ai/summary-runs — the run-history strip's rows (one per
// planned deep-agent run thread), optionally filtered by the
// classification-nav pick: sec_type is a label NAME encoded here (an
// unknown name is a 400 — the researchLogicCode precedent), code the
// subject id. See the header doc + services/ai-summary.service.ts.
router.get("/summary-runs", async (req: Request, res: Response) => {
  try {
    const parsed = Number(req.query.limit);
    const limit = Math.min(1000, Math.max(1, Number.isFinite(parsed) ? Math.floor(parsed) : 200));
    const secTypeName = str(req.query.sec_type);
    let secTypeCode: number | null = null;
    if (secTypeName !== undefined) {
      secTypeCode = encodeLabelParam("sec_type", secTypeName);
      if (secTypeCode === null) {
        res.status(400).json({ error: `Unknown 'sec_type': ${secTypeName}` });
        return;
      }
    }
    res.json(
      await listSummaryRuns(limit, {
        secTypeCode,
        code: str(req.query.code) ?? null,
      }),
    );
  } catch (err) {
    console.error("[ai/summary-runs] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

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
router.delete("/summary-runs/:threadId", async (req: Request, res: Response) => {
  try {
    const threadId = str(req.params.threadId);
    if (!threadId) {
      res.status(400).json({ error: "Missing 'thread_id'" });
      return;
    }
    const out = await deleteSummaryRun(threadId);
    if (out === "missing") {
      res.status(404).json({ error: `thread ${threadId} not found` });
      return;
    }
    if (out === "running") {
      res.status(409).json({
        error: "run is still in flight — stop it or wait for it to settle",
      });
      return;
    }
    publishAiEvent({ type: "thread_deleted", thread_id: threadId });
    res.json({ deleted: true });
  } catch (err) {
    console.error("[ai/summary-runs delete] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// POST /api/ai/summary/stop — cancel in-flight summary run(s): forwards
// to the deep-agents daemon's POST /stop (the langgraph thread winds
// down at its current await; the thread rows settle with the stop). An
// optional { threadId } cancels ONE run of the several the parallel-run
// cap now admits; the thread-less form (the UI's Stop button) cancels
// every active run as before. The still-pending POST /api/ai/summary of
// a cancelled run then resolves with stopped: true. stopped === 0 means
// nothing was running.
router.post("/summary/stop", async (req: Request, res: Response) => {
  try {
    const threadId =
      typeof req.body?.threadId === "string" && req.body.threadId.trim() !== ""
        ? req.body.threadId.trim()
        : undefined;
    res.json(await stopAnalysisSummary(threadId));
  } catch (err) {
    console.error("[ai/summary/stop] error:", err);
    res.status(500).json({ stopped: 0, thread_ids: [], error: String(err) });
  }
});

// GET /api/ai/summary/active — the in-flight run(s)' thread ids (empty
// when idle): forwards to the daemon's GET /active. The UI's live-view
// poll reads this from the Generate click onward and opens the thread
// graph on the first id — ~20-80s before the plan phase persists any
// rows the strip could show.
router.get("/summary/active", async (_req: Request, res: Response) => {
  try {
    res.json({ active: await listActiveSummaryRuns() });
  } catch (err) {
    console.error("[ai/summary/active] error:", err);
    res.status(500).json({ active: [], error: String(err) });
  }
});

router.post("/summary", async (req: Request, res: Response) => {
  try {
    const rawItems = Array.isArray(req.body?.analysisItems)
      ? req.body.analysisItems
      : [];
    const analysisItems: AiSummaryAnalysisItem[] = [];
    for (const raw of rawItems) {
      const key = typeof raw?.key === "string" ? raw.key.trim() : "";
      const label = typeof raw?.label === "string" ? raw.label.trim() : "";
      if (!key || !label) {
        res.status(400).json({
          success: false,
          error: "each analysisItems entry needs a 'key' and a 'label'",
        });
        return;
      }
      const insight = raw.insight as
        | { tab?: unknown; page?: unknown }
        | null
        | undefined;
      analysisItems.push({
        key,
        label,
        description:
          typeof raw?.description === "string" && raw.description.trim() !== ""
            ? raw.description
            : undefined,
        insight:
          insight && typeof insight.tab === "string" && typeof insight.page === "string"
            ? { tab: insight.tab, page: insight.page }
            : undefined,
      });
    }
    if (analysisItems.length === 0) {
      res
        .status(400)
        .json({ success: false, error: "Missing 'analysisItems'" });
      return;
    }
    const prompts =
      Array.isArray(req.body?.prompts) &&
      req.body.prompts.every((s: unknown) => typeof s === "string" && s.trim() !== "")
        ? (req.body.prompts as string[])
        : undefined;
    // Scope pick — the classification-nav focus. sec_type must be one of
    // the four; the subject id must match the kind (code vs industry).
    const SUMMARY_SEC_TYPES = new Set(["industry", "index", "etf", "stock"]);
    let scope: AiSummaryScope | undefined;
    const rawScope = req.body?.scope;
    if (rawScope !== undefined && rawScope !== null) {
      const secType =
        typeof rawScope.secType === "string" ? rawScope.secType : "";
      if (!SUMMARY_SEC_TYPES.has(secType)) {
        res.status(400).json({
          success: false,
          error: `scope.secType must be one of industry|index|etf|stock, got '${secType}'`,
        });
        return;
      }
      const code = typeof rawScope.code === "string" ? rawScope.code.trim() : "";
      const industry =
        typeof rawScope.industry === "string" ? rawScope.industry.trim() : "";
      if (secType === "industry" ? !industry : !code) {
        res.status(400).json({
          success: false,
          error:
            secType === "industry"
              ? "scope.industry is required for secType 'industry'"
              : "scope.code is required for secType " + secType,
        });
        return;
      }
      scope = {
        secType: secType as AiSummaryScope["secType"],
        ...(secType === "industry" ? { industry } : { code }),
        name:
          typeof rawScope.name === "string" && rawScope.name.trim() !== ""
            ? rawScope.name
            : undefined,
      };
    }
    const question =
      typeof req.body?.question === "string" && req.body.question.trim() !== ""
        ? req.body.question
        : undefined;

    const out = await generateAnalysisSummary({
      analysisItems,
      prompts,
      scope,
      question,
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
      res.status(500).json({ success: false, error: out.stderrTail ?? "deep agent failed" });
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
  } catch (err) {
    console.error("[ai/summary] error:", err);
    res.status(500).json({ success: false, error: String(err) });
  }
});

export default router;
