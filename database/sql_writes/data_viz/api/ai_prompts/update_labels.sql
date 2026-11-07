-- SQL write action — llm.ai_prompts labels UPDATE (UI: Prompts picker,
-- Labels editor save). Executed by
-- data_viz/api/services/ai-prompts.service.ts through the pg primary
-- pool; convention of this tree:
--   database/sql_writes/<app>/<layer>/<table>/<action>.sql
--
-- Params (node-postgres $n, in order):
--   $1 prompt_key       TEXT — the stable join key
--   $2 bound_labels     TEXT[] — the FULL effective bound set (force-load
--                       keywords; wholesale replace, no merge)
--   $3 optional_labels  TEXT[] — the FULL effective optional set (search
--                       vocabulary; wholesale replace, no merge)
--   $4 prompt_sub_type  TEXT | NULL — narrows the match to the one
--                       (prompt_key, prompt_sub_type) PK row; NULL = the
--                       key-only match
--
-- No active row matches → empty RETURNING (the caller maps it to 404).
-- prompt_type is a static.label_dim SMALLINT code decoded here at the SQL
-- boundary (domain 'prompt_type').
UPDATE llm.ai_prompts
   SET bound_labels = $2::text[],
       optional_labels = $3::text[],
       updated_at = now()
 WHERE prompt_key = $1
   AND ($4::text IS NULL OR prompt_sub_type = $4)
   AND is_active
RETURNING prompt_key,
          prompt_sub_type,
          prompt_type,
          (SELECT d.label FROM static.label_dim d
            WHERE d.domain = 'prompt_type' AND d.code = prompt_type)
            AS prompt_type_label,
          title, version, instruction, preview, note,
          bound_labels, optional_labels, ordinal
