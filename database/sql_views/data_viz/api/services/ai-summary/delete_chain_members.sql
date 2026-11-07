-- Summary delete — resolve the run's WHOLE CHAIN: the three threads share
-- the root's super_thread_id; legacy runs resolve to themselves.
-- Serves DELETE /api/ai/summary-runs/:threadId (the per-member txn loop).
-- args:
--   thread_id: value text — ANY of the run's thread ids
SELECT DISTINCT thread_id
  FROM llm.deep_agent_states
 WHERE super_thread_id = {{thread_id}} OR thread_id = {{thread_id}}
