-- Thread Graph — the LangGraph store's LLM-context REGISTRY
-- (llm.deep_agent_store_items under ("deep_agent", <tid>, "contexts")):
-- one reference item per context (node, checkpoint id, call counts) — the
-- context DATA itself is never persisted, by design.
-- Serves GET /api/ai/threads/:threadId (the llm_contexts block).
-- args:
--   thread_id: value text — the deep-agent thread (uuid hex)
SELECT key AS context_id,
            (value->>'kind') AS kind,
            (value->>'node') AS node,
            NULLIF(value->>'state_id', '')::int AS state_id,
            value->>'parent_context_id' AS parent_context_id,
            value->>'checkpoint_id'     AS checkpoint_id,
            value->>'model'             AS model,
            (value->>'n_llm_calls')::int  AS n_llm_calls,
            (value->>'n_tool_calls')::int AS n_tool_calls,
            (updated_at AT TIME ZONE 'Asia/Shanghai')::text AS updated_at
       FROM llm.deep_agent_store_items
      WHERE namespace = ARRAY['deep_agent', {{thread_id}}, 'contexts']::text[]
      ORDER BY key
