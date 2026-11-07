-- The super chain's thread ids in chain order (plan → custom → result).
-- Resolve the CHAIN ROOT first: any member's own rows carry the root's id
-- in super_thread_id (the root's own rows carry its id itself; a legacy
-- single-thread run carries NULL — COALESCE keeps the requested thread as
-- its own root), then every thread sharing that root id (plus the root)
-- is a member. The root is the earliest row's thread (the root's run-start
-- plan row), so MIN(created_at) orders plan → custom → result.
-- Serves GET /api/ai/threads/:threadId (the chain resolution read).
-- args:
--   thread_id: value text — ANY chain member's id (uuid hex)
WITH root AS (
    SELECT COALESCE(MIN(s.super_thread_id), $1) AS chain_id
      FROM llm.deep_agent_states s
     WHERE s.super_thread_id = {{thread_id}} OR s.thread_id = {{thread_id}}
)
SELECT s.thread_id
  FROM llm.deep_agent_states s, root
 WHERE s.super_thread_id = root.chain_id OR s.thread_id = root.chain_id
 GROUP BY s.thread_id
 ORDER BY MIN(s.created_at), s.thread_id
