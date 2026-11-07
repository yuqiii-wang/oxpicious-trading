-- Saved plan templates — save side: the source run's identity row
-- (llm.deep_agent_identities), the code / name / sec_type placeholder
-- fallback for runs older than the templates feature.
-- sec_type decodes from its static.label_dim code at the SQL boundary.
-- Serves POST /api/ai/plan-templates.
-- args:
--   thread_id: value text — the source deep-agent thread (uuid hex)
--   sec_type_decode: fragment — labelDecodeSql("sec_type", "sec_type")
SELECT {{sec_type_decode}} AS sec_type,
            code, name
       FROM llm.deep_agent_identities
      WHERE thread_id = {{thread_id}} LIMIT 1
