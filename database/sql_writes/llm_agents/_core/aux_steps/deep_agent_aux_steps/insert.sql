-- SQL write action — llm.deep_agent_aux_steps INSERT (the table's ONE
-- write place). Executed by llm_agents/_core/aux_steps.py
-- (AuxStepRecorder's writer thread, executemany batches) for every
-- auxiliary step: step_type 2 decision = one System One (jev) decision
-- read; step_type 3 tokenization = one StructBERT segmentation op.
-- Convention of this tree:
--   database/sql_writes/<app>/<layer>/<table>/<action>.sql
--
-- Params (psycopg executemany %s / dense $n, in order):
--   $1  thread_id       TEXT | NULL — the deep-agent thread the op ran
--                       under (NULL when the caller only knows the chain)
--   $2  super_thread_id TEXT | NULL — the run's chain id when known
--   $3  state_id        BIGINT | NULL — the task state the op served
--   $4  llm_context_id  TEXT | NULL — the ambient request chain (uuid
--                       hex) — the UI steps view's JOIN KEY
--   $5  step_type       SMALLINT — the agent_step_type code
--                       (2 decision | 3 tokenization; resolved host-side
--                       via _common.labels AGENT_STEP_TYPE_CODE)
--   $6  lane            TEXT — the op's lane (the decision template key /
--                       search_tsv | context_tags | query_segment)
--   $7  title           TEXT — one human headline line
--   $8  input_text      TEXT | NULL — the compact input face
--   $9  output_text     TEXT | NULL — the compact output face
--   $10 input_payload   JSONB | NULL — the typed facts (size-capped)
--   $11 output_payload  JSONB | NULL — the typed result (size-capped)
--   $12 duration_ms     INT | NULL — wall time (NULL when batch-shared)
--   $13 is_failed       BOOLEAN — the op errored (a below-floor read is
--                       NOT a failure)
--   $14 error           TEXT | NULL — the failure reason
--
-- Placeholders stay the tree's dense $n contract; the recorder's
-- psycopg writer translates $k → %s positionally at load time
-- (AuxStepRecorder._psycopg_sql — the tree's two standard executors
-- are node-postgres/asyncpg; this is the file's third, sync face).
INSERT INTO llm.deep_agent_aux_steps
    (thread_id, super_thread_id, state_id, llm_context_id, step_type,
     lane, title, input_text, output_text, input_payload,
     output_payload, duration_ms, is_failed, error)
VALUES ($1, $2, $3, $4, $5::smallint, $6, $7, $8, $9,
        $10::jsonb, $11::jsonb, $12, $13, $14)
