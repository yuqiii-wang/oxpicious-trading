-- Thread Graph task-row expansion — WHICH chain member holds the state.
-- The UI opens a run by ANY member's id (usually the super chain root,
-- the plan graph's own thread id), so a task of the custom/result
-- segment arrives tagged with the ROOT's thread id — resolving the
-- request rows needs the OWNING member's thread id (the root CTE is the
-- chain_threads resolution: any member's rows carry the root's id in
-- super_thread_id; a legacy single-thread run COALESCEs to itself).
-- Serves GET /api/ai/threads/:threadId/states/:stateId/llm-requests.
-- args:
--   thread_id: value text — ANY chain member's id (uuid hex)
--   state_id: value bigint — the deep_agent_states row (task)
WITH root AS (
    SELECT COALESCE(MIN(s.super_thread_id), {{thread_id}}) AS chain_id
      FROM llm.deep_agent_states s
     WHERE s.super_thread_id = {{thread_id}} OR s.thread_id = {{thread_id}}
)
SELECT s.thread_id
  FROM llm.deep_agent_states s, root
 WHERE s.state_id = {{state_id}}
   AND (s.super_thread_id = root.chain_id OR s.thread_id = root.chain_id)
 LIMIT 1
