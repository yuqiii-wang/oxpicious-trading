-- ============================================================================
--  Multi-media schema bootstrap
--  Schema: multi_media
--    Holds source media binaries shared across features: chart screenshots
--    attached to AI asks, downloaded article images, and (later) generated
--    figures. Consumer tables live in their OWN schemas and map to images
--    through junction tables there (e.g. text.llm_qa_ask_context_images) —
--    this schema stays consumer-agnostic so one stored image can back
--    several references.
--
--  Apply order: BEFORE database/sql/text/05_llm_qa_by_ask.sql — that file's
--  llm_qa_ask_context_images junction has a foreign key into
--  multi_media.src_images.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS multi_media;

COMMENT ON SCHEMA multi_media IS
  'Source media binaries shared across features. Consumer tables in other '
  'schemas map to multi_media.src_images through their own junction tables; '
  'this schema itself stays consumer-agnostic.';
