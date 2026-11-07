-- AI ask-history image bytes — the thumbnail/full-image source behind
-- GET /api/ai/ask-image.
-- args:
--   image_id: value bigint — the multi_media.src_images row to fetch
SELECT mime_type, data FROM multi_media.src_images WHERE image_id = {{image_id}}
