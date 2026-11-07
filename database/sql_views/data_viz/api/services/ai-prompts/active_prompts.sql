-- Prompts picker — the llm.ai_prompts registry's ACTIVE rows (the UI's
-- Prompts picker + the deep agent's searchable prompt set), ordinal/title/
-- version order. prompt_type decodes from its static.label_dim code
-- (domain prompt_type) at the SQL boundary.
-- Serves GET /api/ai/prompts (10-min cache).
-- args:
--   prompt_type_decode: fragment — labelDecodeSql("prompt_type", "prompt_type")
SELECT prompt_key,
              prompt_sub_type,
              prompt_type,
              {{prompt_type_decode}} AS prompt_type_label,
              title, version, instruction, preview, note,
              bound_labels, optional_labels, ordinal
         FROM llm.ai_prompts
        WHERE is_active
        ORDER BY ordinal, title, version, prompt_key
