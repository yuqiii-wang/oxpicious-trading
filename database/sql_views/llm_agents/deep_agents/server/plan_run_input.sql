-- llm_agents.deep_agents.server — the UI node actions' RUN PASS-DOWN
-- source (2026-11-15): the chain ROOT thread's kind-0 (agent_context_kind
-- 'plan') context payload ({args, goal, steps, plan_prompts,
-- prompts_loaded, prompts_catalog}) — the same face a delegated spawn's
-- pass-down carries, re-packed host-side for the Thread Graph's
-- UI-spawned child (SpawnPassDown.pack over goal / args / the loaded +
-- cataloged prompts). Read by (thread, node, kind) — the plan state
-- row's own input_context_id reference is NULL by design (the planner's
-- context is graph-level). Fail-soft host-side: no row (degraded /
-- legacy thread) → the bare user goal.
-- args:
--   thread_id: value text — the chain ROOT thread (super_thread_id)
SELECT payload
  FROM llm.deep_agent_contexts
 WHERE thread_id = {{thread_id}}
   AND node = 'plan'
   AND kind = 0
 ORDER BY context_id
 LIMIT 1
