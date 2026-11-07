-- SQL write action — llm.llm_qa UPDATE (soft-delete one Q&A row).
-- Executed by llm_agents/llm_qa.py (deactivate_qa — the CLI's
-- deactivate command): the row stays, is_active drops.
--
-- Params (asyncpg $n, in order):
--   $1 qa_id  BIGINT — the row to deactivate
--
-- The caller reads the command tag ("UPDATE 1") for whether a row
-- matched.
UPDATE llm.llm_qa SET is_active = false, updated_at = now()
WHERE qa_id = $1
