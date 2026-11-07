-- SQL write action — llm.ai_prompts instruction UPDATE (UI: Prompts
-- picker, instruction edit). Executed by
-- data_viz/api/services/ai-prompts.service.ts through the pg primary
-- pool; convention of this tree:
--   database/sql_writes/<app>/<layer>/<table>/<action>.sql
--
-- Params (node-postgres $n, in order):
--   $1 prompt_key       TEXT — the stable join key (the deep agent's
--                       fetches and persisted plan payloads keep pointing
--                       at the same row)
--   $2 instruction      TEXT — the replacement instruction text (version
--                       bumps by one)
--   $3 prompt_sub_type  TEXT | NULL — narrows the match to the one
--                       (prompt_key, prompt_sub_type) PK row; NULL = the
--                       key-only match for callers without the sub_type
--
-- No active row matches → empty RETURNING (the caller maps it to 404).
-- prompt_type is a static.label_dim SMALLINT code decoded here at the SQL
-- boundary (domain 'prompt_type').
UPDATE llm.ai_prompts
   SET instruction = $2,
       version = version + 1,
       updated_at = now()
 WHERE prompt_key = $1
   AND ($3::text IS NULL OR prompt_sub_type = $3)
   AND is_active
RETURNING prompt_key,
          prompt_sub_type,
          prompt_type,
          (SELECT d.label FROM static.label_dim d
            WHERE d.domain = 'prompt_type' AND d.code = prompt_type)
            AS prompt_type_label,
          title, version, instruction, preview, note,
          bound_labels, optional_labels, ordinal
