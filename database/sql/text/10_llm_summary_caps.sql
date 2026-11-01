-- ============================================================================
--  LLM summary text caps — store-time truncation contract + one-time cleanup
--
--  The generated texts in the text.* AI tables used to be stored at
--  whatever length the model produced. Measured 2026-10-29 (read replica):
--    * text.llm_qa.answer               p95 1241, max  2733 chars (rendered)
--    * text.llm_qa.context              p50 2494, max 151771 chars — the
--      supporting passage is NOT rendered (data_viz AiQaCard skips it) yet
--      /api/ai/qa ships it whole on every detail fetch
--    * text.llm_qa_by_research.context  avg 32072 chars (never rendered)
--
--  Contract (mirrors llm_agents._core.text_caps, enforced at store time by
--  llm_agents.llm_qa.upsert_qa / llm_qa_by_research.storage /
--  llm_ask.storage): answer-like UI text caps at MAX_SUMMARY_CHARS = 2000
--  chars, supporting-passage context caps at MAX_CONTEXT_CHARS = 8000.
--  Truncation appends " …[截断]" — the news-body marker of
--  llm_agents.news_digestions.agent.truncate_content.
--
--  The UPDATEs below re-apply that cap to existing rows. Idempotent: the
--  WHERE clauses make re-runs no-ops, and any over-cap row a pre-cap path
--  writes is trimmed again on the next apply (self-healing).
--
--  Deliberately NOT capped here: text.llm_requests request/response
--  payloads (the source log's purpose is full-payload recovery — that is
--  a retention question, not truncation), llm_qa_ask_context.plot_info
--  (verbatim ask context, measured max 1.8K), question columns (bounded
--  by construction).
-- ============================================================================

-- text.llm_qa — the AI-page knowledge base.
UPDATE text.llm_qa
SET answer = LEFT(answer, 2000) || ' …[截断]'
WHERE LENGTH(answer) > 2000;

UPDATE text.llm_qa
SET context = LEFT(context, 8000) || ' …[截断]'
WHERE LENGTH(context) > 8000;

-- text.llm_qa_by_ask — interactive ask history.
UPDATE text.llm_qa_by_ask
SET answer = LEFT(answer, 2000) || ' …[截断]'
WHERE LENGTH(answer) > 2000;

UPDATE text.llm_qa_by_ask
SET error_tail = LEFT(error_tail, 2000) || ' …[截断]'
WHERE LENGTH(error_tail) > 2000;

-- text.llm_qa_by_research — answer + the five dimension texts at the
-- summary cap (one pass; NULL dimensions pass through untouched).
UPDATE text.llm_qa_by_research
SET answer             = CASE WHEN LENGTH(answer) > 2000
                              THEN LEFT(answer, 2000) || ' …[截断]'
                              ELSE answer END,
    intro              = CASE WHEN LENGTH(intro) > 2000
                              THEN LEFT(intro, 2000) || ' …[截断]'
                              ELSE intro END,
    optimistic_forecast = CASE WHEN LENGTH(optimistic_forecast) > 2000
                              THEN LEFT(optimistic_forecast, 2000)
                                   || ' …[截断]'
                              ELSE optimistic_forecast END,
    pessimistic_forecast = CASE WHEN LENGTH(pessimistic_forecast) > 2000
                              THEN LEFT(pessimistic_forecast, 2000)
                                   || ' …[截断]'
                              ELSE pessimistic_forecast END,
    risks              = CASE WHEN LENGTH(risks) > 2000
                              THEN LEFT(risks, 2000) || ' …[截断]'
                              ELSE risks END,
    capitalization     = CASE WHEN LENGTH(capitalization) > 2000
                              THEN LEFT(capitalization, 2000) || ' …[截断]'
                              ELSE capitalization END
WHERE LENGTH(answer) > 2000
   OR LENGTH(intro) > 2000
   OR LENGTH(optimistic_forecast) > 2000
   OR LENGTH(pessimistic_forecast) > 2000
   OR LENGTH(risks) > 2000
   OR LENGTH(capitalization) > 2000;

UPDATE text.llm_qa_by_research
SET context = LEFT(context, 8000) || ' …[截断]'
WHERE LENGTH(context) > 8000;

-- ----------------------------------------------------------------------------
--  Column contracts (catalog-visible; the DDL files 02/05/06 keep the
--  inline pointers).
-- ----------------------------------------------------------------------------

COMMENT ON COLUMN text.llm_qa.answer IS
  'The LLM answer (the AI-page card body / detail text). Capped at store '
  'time to 2000 chars, overruns trimmed to the cap plus a " …[截断]" '
  'marker (llm_agents._core.text_caps.MAX_SUMMARY_CHARS; existing rows '
  'trimmed by 10_llm_summary_caps.sql).';

COMMENT ON COLUMN text.llm_qa.context IS
  'Optional supporting passage the answer was grounded in. NEVER rendered '
  'by the UI (AiQaCard) — provenance only. Capped at store time to 8000 '
  'chars + " …[截断]" marker (text_caps.MAX_CONTEXT_CHARS) — pre-cap rows '
  'held up to 152K chars and were shipped whole by /api/ai/qa.';

COMMENT ON COLUMN text.llm_qa_by_ask.answer IS
  'The adviser answer text. NULL when status = ''failed'' (the ask errored '
  'before an answer existed); failure detail lives in error_tail. Capped '
  'at store time to 2000 chars + " …[截断]" marker '
  '(text_caps.MAX_SUMMARY_CHARS).';

COMMENT ON COLUMN text.llm_qa_by_ask.error_tail IS
  'Failure stderr/exception tail when status = ''failed''. Capped at '
  'store time to 2000 chars + " …[截断]" marker '
  '(text_caps.MAX_SUMMARY_CHARS).';

COMMENT ON COLUMN text.llm_qa_by_research.answer IS
  'The 6th request''s SUMMARY of the five dimension answers — the row''s '
  'display content (the UI shows this as the card body; the dimensions are '
  'the expandable detail). Replaces any earlier whole-answer semantics. '
  'Capped at store time to 2000 chars + " …[截断]" marker '
  '(text_caps.MAX_SUMMARY_CHARS), same as the five dimension columns.';

COMMENT ON COLUMN text.llm_qa_by_research.context IS
  'The merged per-dimension reference blocks the generation was grounded '
  'in. NEVER rendered by the UI — provenance only. Capped at store time '
  'to 8000 chars + " …[截断]" marker (text_caps.MAX_CONTEXT_CHARS).';
