-- Thread Graph — the thread's JSONB context payloads
-- (llm.deep_agent_contexts: kind plan / step-input / step-result /
-- subagent-result / final-result). `kind` is a CONVERTED label column
-- (agent_context_kind code) decoded back to the label string in SQL so the
-- response contract keeps the pre-conversion vocabulary.
-- Serves GET /api/ai/threads/:threadId (the contexts block).
-- args:
--   thread_id: value text — the deep-agent thread (uuid hex)
--   kind_decode: fragment — labelDecodeSql("agent_context_kind", "kind")
SELECT context_id::int AS context_id,
            {{kind_decode}} AS kind,
            node,
            state_id::int AS state_id,
            llm_context_id,
            payload,
            (created_at AT TIME ZONE 'Asia/Shanghai')::text AS created_at
       FROM llm.deep_agent_contexts
      WHERE thread_id = {{thread_id}}
      ORDER BY context_id
