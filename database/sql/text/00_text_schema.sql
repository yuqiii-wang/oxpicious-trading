-- ============================================================================
--  Text schema bootstrap
--  Schema: text
--    Holds all NLP / text-derived data: raw news, extracted keywords, and
--    LLM question-answering corpora. Semantic-search (similarity) columns use
--    the pgvector `vector` type.
--
--    Vector columns are stored as `vector(N)` where N is the embedding
--    dimensionality. 1536 = OpenAI text-embedding-3-small (current default
--    in this project); 1024 = BAAI/bge-m3 if the local model is adopted.
--    ANN search uses hnsw with cosine distance — query with the `<=>`
--    operator (NOT `<->`, which is L2 distance and cannot use the cosine
--    index).
--
--  STATUS: the embedding tables exist but are NOT IN USE — no embedding
--    pipeline is implemented yet (see news_embeddings / qa_embeddings).
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS text;

-- pgvector bootstrap. This cluster runs the Nix-built Supabase postgres
-- image whose utility-hook layer intercepts CREATE EXTENSION and requires a
-- 'supabase_admin' role with superuser to exist first (verified empirically
-- on this cluster: a NOLOGIN role without SUPERUSER still fails with
-- 'permission denied for function pg_read_file'). NOLOGIN means the role
-- cannot authenticate; it only satisfies the hook.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'supabase_admin') THEN
        CREATE ROLE supabase_admin NOLOGIN SUPERUSER;
    END IF;
END $$;

-- WITH SCHEMA public is required on this cluster: the connecting role's
-- search_path starts with `stats`, so an unqualified install would land the
-- extension's objects in the stats schema instead of public.
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
