-- AI ask-history detail's image metadata list (ids + dimensions + labels
-- only — the bytes come from the ask-image read on demand).
-- Serves GET /api/ai/ask-qa (the images block).
-- args:
--   ask_id: value bigint — the ask whose attached images to list
SELECT i.image_id, ci.position, i.mime_type, i.byte_size,
           i.width, i.height, i.label
      FROM llm.llm_qa_ask_context_images ci
      JOIN multi_media.src_images i ON i.image_id = ci.image_id
     WHERE ci.ask_id = {{ask_id}}
     ORDER BY ci.position
