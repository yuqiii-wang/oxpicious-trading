-- llm_agents.deep_agents.server — the chain members of one deep-agent
-- run in CHAIN ORDER (plan → custom → result; legacy single-thread runs
-- resolve to themselves) — the service's canvas-node-id → owning-segment
-- thread mapping (the s<k>:node prefixes the merged detail carries).
-- Same ordering contract PlanStore.fetch_chain_threads uses (earliest
-- row creation; the root's own rows carry its id).
-- args:
--   thread_id: value text — any chain member id (the UI passes the root)
WITH root AS (
       SELECT COALESCE(super_thread_id, thread_id) AS super
         FROM llm.deep_agent_states
        WHERE thread_id = {{thread_id}}
        LIMIT 1
     )
SELECT thread_id, MIN(created_at) AS first_at
  FROM llm.deep_agent_states
 WHERE super_thread_id = COALESCE((SELECT super FROM root), {{thread_id}})
    OR thread_id = {{thread_id}}
 GROUP BY thread_id
 ORDER BY first_at, thread_id
