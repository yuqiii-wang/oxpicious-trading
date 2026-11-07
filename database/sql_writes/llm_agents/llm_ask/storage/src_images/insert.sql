-- SQL write action — multi_media.src_images INSERT (one deduplicated
-- screenshot blob). Executed by llm_agents/llm_ask/storage.py
-- (_persist_ask) per decoded screenshot inside the per-ask transaction.
--
-- Params (asyncpg $n, in order):
--   $1 mime_type     TEXT — the decoded data URL's mime
--   $2 byte_size     INTEGER — the blob's size
--   $3 width         INTEGER | NULL — PNG IHDR width (NULL non-PNG)
--   $4 height        INTEGER | NULL — PNG IHDR height
--   $5 content_hash  TEXT — sha256 hex (the dedup key)
--   $6 label         TEXT — the human label ('ai-ask screenshot i/n')
--   $7 data          BYTEA — the image bytes
--
-- DO UPDATE (not DO NOTHING) so RETURNING always yields the id, also
-- for an already-stored identical screenshot; the label refreshes.
INSERT INTO multi_media.src_images
    (mime_type, byte_size, width, height, content_hash, label, data)
VALUES ($1, $2, $3, $4, $5, $6, $7)
ON CONFLICT (content_hash) DO UPDATE
SET label = EXCLUDED.label RETURNING image_id
