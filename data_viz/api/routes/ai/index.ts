// The route catalog below was extracted verbatim from routes/ai.ts when
// the ai routes were split into this directory of subfiles.
/**
 * AI API routes — read access to the LLM Q&A knowledge base in the text
 * schema (llm.llm_qa / llm.llm_qa_refs / text.news_digestions, written by
 * llm_agents) and to the persisted interactive ask history
 * (llm.llm_qa_by_ask family + multi_media.src_images, written by
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
 * base llm.llm_qa_by_research, written by the five-request generation
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
 *     The active llm.ai_prompts rows (the Prompts picker's catalog; DDL
 *     database/sql/text/llm/06_ai_prompts.sql, seeds database/sql/text/
 *     static/ai_prompts/) — prompt_key/prompt_sub_type/prompt_type
 *     (+ decoded prompt_type_label)/title/version/instruction/preview/
 *     note/ordinal. The picker nests the rows into sub_type foldables
 *     inside each prompt_type section and sends the picked skills- and
 *     plan-type prompt_key values back in the summary request (the deep
 *     agent fetches their instruction texts itself over its DB tools);
 *     the tool-type rows (the tools' schema descriptions, applied
 *     automatically by the runner every run) are listed for view/edit
 *     only — never selectable.
 *
 *   PUT /api/ai/prompts/:prompt_key  { instruction, prompt_sub_type? }
 *     Save an edited prompt (the prompt view's Edit → Save): the active
 *     row's instruction is replaced in place and its version bumps by one
 *     (prompt_key stays the stable join key; prompt_sub_type narrows the
 *     match to the one PK row — update_plan's two lanes edit
 *     independently). Returns the refreshed row; 404 when no active row
 *     matches.
 *
 *   PUT /api/ai/prompts/:prompt_key/labels
 *     { bound_labels: string[], optional_labels: string[], prompt_sub_type? }
 *     Save the prompt view's Labels editor: the row's two label arrays are
 *     replaced wholesale with the posted effective sets (trimmed, the
 *     optional group lowercased, deduped). Returns the refreshed row; 404
 *     when no active row matches.
 *
 *   POST /api/ai/summary
 *     { analysisItems: [{ key, label, description?, insight? }], prompts?,
 *     scopes?, scope?, question? } → enriches the items with their
 *     insight-registry page docs + metric db-search recipes and POSTs the
 *     whole spec as JSON to the deep-agents FastAPI daemon
 *     (llm_agents.deep_agents.server — see services/ai-summary.service.ts),
 *     returning
 *     { success, answer, model?, steps?, n_tool_calls?, thread_id?, plan? }.
 *     thread_id + plan (the llm.deep_agent_states snapshot: ordinal /
 *     node / title / status per planned step) are present when the run
 *     picked plan prompts and the plan phase persisted. scopes is the
 *     MULTI-PICK scope list that focuses the run — one entry per picked
 *     subject, each { secType: industry|index|etf|stock, code? |
 *     industry?, name? }; the legacy singular scope folds in as a
 *     one-entry list; absent/empty = market-wide.
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
 *   GET /api/ai/summary-runs/dates?limit=&sec_type=&code=
 *     The Summary History strip's FIRST-phase read: one aggregate row per
 *     GMT operational day that has runs — the day's run count + per-status
 *     step totals (the DateEventStrip's dot content), with NO per-thread
 *     rows, plan-goal payload or identity join unless the nav pick is
 *     present. The picked day's rows come from summary-runs?date= below.
 *     Same filter semantics + limit clamp as summary-runs.
 *
 *   GET /api/ai/summary-runs?limit=&sec_type=&code=&date=&thread_id=
 *     The strip's SECOND-phase read: one row per planned deep-agent run
 *     (a llm.deep_agent_states thread — runs without a plan phase create
 *     no thread), with the plan goal, the per-status step counts, the
 *     first/last Shanghai-local times and the run's scope pick when scoped
 *     (scope_sec_type decoded / scope_code / scope_name —
 *     llm.deep_agent_identities, the code <-> thread_id link the runner's
 *     plan node writes). With date= (YYYY-MM-DD, the GMT operational day)
 *     the read narrows to that ONE day — the picked dot's per-thread rows,
 *     an indexed gmt-day range (ix_deep_agent_states_gmt_day) instead of an
 *     aggregate over the whole table. With thread_id= the read becomes the
 *     search-by-id lookup — a case-insensitive PREFIX match (a pasted full
 *     id is the exact case); the UI's search box sends it alone, but if
 *     nav/date filters ride too they AND-combine as usual. sec_type is
 *     a label NAME
 *     (index|etf|stock|industry, encoded via label-codes) and code the
 *     subject id (a security code or industry id) — the classification-nav
 *     filter; code may REPEAT (the summary page's multi-pick subject set —
 *     a run scoped to ANY of them matches); either axis alone filters on
 *     that axis, both = the exact pick; absent = every run. The pick is INCLUSIVE of market-wide runs: a
 *     thread with no identity row never carried a scope pick and a
 *     market-wide summary covers every subject, so the filtered read
 *     returns the pick's runs plus the unscoped ones (scope_* fields null
 *     on those rows — the UI badges them). The filtered read is an indexed
 *     identity lookup, not a states scan. run_date is the GMT operational
 *     day of the run's first state (a new day starts at Beijing 08:00, the
 *     same boundary as llm.llm_requests.date). Most recent first; limit
 *     clamps to 1..1000 (default 200).
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

import { Router } from "express";
import askRouter from "./ask.js";
import knowledgeBaseRouter from "./knowledge-base.js";
import planTemplatesRouter from "./plan-templates.js";
import promptsRouter from "./prompts.js";
import researchRouter from "./research.js";
import summaryRouter from "./summary.js";

const router = Router();

router.use(knowledgeBaseRouter);
router.use(askRouter);
router.use(researchRouter);
router.use(promptsRouter);
router.use(summaryRouter);
router.use(planTemplatesRouter);

export default router;
