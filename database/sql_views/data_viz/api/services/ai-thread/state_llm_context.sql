-- Thread Graph task-row expansion — "unknown task vs nothing recorded"
-- probe: the state row's bound LLM context id (NULL for runs that predate
-- the chain binding).
-- Serves GET /api/ai/threads/:threadId/states/:stateId/llm-requests
-- (empty-requests fallback).
-- args:
--   thread_id: value text — the deep-agent thread (uuid hex)
--   state_id: value bigint — the deep_agent_states row (task)
SELECT llm_context_id FROM llm.deep_agent_states
        WHERE thread_id = {{thread_id}} AND state_id = {{state_id}}
