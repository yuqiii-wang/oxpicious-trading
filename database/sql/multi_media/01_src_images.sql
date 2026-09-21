-- ============================================================================
--  Source images
--  Table: multi_media.src_images — one row per distinct stored image binary.
--
--  Written by llm_agents.llm_ask.storage (AI-ask screenshots, PNG bytes
--  decoded from the payload's data URLs); further producers can join later.
--  Deduplication is by content_hash (sha256 of the bytes): the same
--  screenshot re-attached by a later ask reuses the existing row, so the
--  BYTEA is stored once. Consumers reference images by image_id through
--  junction tables in their own schemas (text.llm_qa_ask_context_images) —
--  never add a consumer FK column here.
-- ============================================================================

CREATE TABLE IF NOT EXISTS multi_media.src_images (
    image_id     BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mime_type    TEXT        NOT NULL DEFAULT 'image/png',
    byte_size    INTEGER     NOT NULL,
    width        INTEGER,                    -- parsed from the PNG IHDR when the bytes are PNG; NULL otherwise
    height       INTEGER,
    content_hash TEXT        NOT NULL,       -- sha256 hex of data; identical bytes stored once
    label        TEXT,                       -- producer hint, e.g. 'ai-ask screenshot 2/4'
    data         BYTEA       NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_src_images_content_hash UNIQUE (content_hash),
    CONSTRAINT ck_src_images_mime CHECK (mime_type LIKE 'image/%'),
    CONSTRAINT ck_src_images_byte_size CHECK (byte_size > 0)
);

COMMENT ON COLUMN multi_media.src_images.content_hash IS
  'sha256 hex of data. The write path upserts ON CONFLICT (content_hash), so '
  'an identical screenshot attached by a later ask reuses this row instead of '
  'storing the bytes again.';

COMMENT ON COLUMN multi_media.src_images.width IS
  'Pixel dimensions parsed from the PNG IHDR header when data is PNG; NULL '
  'for other formats until a parser exists.';

COMMENT ON COLUMN multi_media.src_images.label IS
  'Free-form producer hint (which screenshot slot of which ask). Display '
  'only — never a lookup key.';

COMMENT ON TABLE multi_media.src_images IS
  'Source image binaries, deduplicated by content hash. One row per '
  'distinct image; consumer features map to image_id through junction '
  'tables in their own schemas (see text.llm_qa_ask_context_images).';
