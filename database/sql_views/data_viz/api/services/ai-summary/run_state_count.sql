-- Analysis Summary run delete — existence probe inside the delete
-- transaction: distinguishes "unknown thread id" (404) from a thread whose
-- rows exist. Runs through the delete txn's own client.
-- Serves DELETE /api/ai/summary-runs/:threadId.
-- args:
--   thread_id: value text — the deep-agent thread being deleted
SELECT COUNT(*)::int AS n FROM llm.deep_agent_states WHERE thread_id = {{thread_id}}
