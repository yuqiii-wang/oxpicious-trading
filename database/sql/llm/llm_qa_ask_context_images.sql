
-- ----------------------------------------------------------------------------
--  Ask-context -> image junction: one context entry, many images
--  (the modal attaches up to 4 stacked-chart screenshots per ask).
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS llm.llm_qa_ask_context_images (
    ask_id   BIGINT   NOT NULL,
    image_id BIGINT   NOT NULL,
    position SMALLINT NOT NULL DEFAULT 0,     -- screenshot slot order in the payload

    CONSTRAINT pk_llm_qa_ask_context_images PRIMARY KEY (ask_id, image_id),
    CONSTRAINT fk_llm_qa_ask_context_images_ctx FOREIGN KEY (ask_id)
        REFERENCES llm.llm_qa_ask_context (ask_id) ON DELETE CASCADE,
    CONSTRAINT fk_llm_qa_ask_context_images_img FOREIGN KEY (image_id)
        REFERENCES multi_media.src_images (image_id) ON DELETE CASCADE
);

-- Reverse lookup: where (which asks) one stored image is referenced.
CREATE INDEX IF NOT EXISTS ix_llm_qa_ask_context_images_image
    ON llm.llm_qa_ask_context_images (image_id);

COMMENT ON COLUMN llm.llm_qa_ask_context_images.position IS
  'Screenshot slot order within the ask (0-based, the payload array order) '
  '— the detail view re-attaches thumbnails in this order.';

COMMENT ON TABLE llm.llm_qa_ask_context_images IS
  'Junction: which multi_media.src_images belong to which ask context. One '
  'context entry maps to N images (the modal''s stacked-chart screenshots, '
  'capped at 4); identical screenshots across asks share one src_images row '
  '(deduplicated by content_hash).';
