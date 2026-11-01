-- ============================================================================
--  Deep-agent run history prune — keep the BEST run per operational day
--
--  The Analysis Summary page's "Summary History" strip lists one event per
--  planned deep-agent run (a text.deep_agent_states thread), and clicking a
--  multi-run day opens the "Runs on <date>" dropdown listing EVERY run that
--  day. Dev/verification cycles accumulate junk there: 3-step smoke probes,
--  failed/skipped-only runs, superseded generations — 14 dropdown options
--  on a single day (2026-10-29 profile).
--
--  POLICY (idempotent, self-healing — re-apply any time):
--    Per GMT operational day (the SAME bucket the run strip uses —
--    listSummaryRuns' run_date), KEEP the closed run with the most
--    completed steps (ties: fewest failed, then the newest). PURGE every
--    other closed run. Threads with open states (a generation in flight)
--    are never purged; once they settle, the next apply prunes them when
--    they are not the day's best.
--
--  Purge order per 09_deep_agent_states.sql + 12_deep_agent_store.sql +
--  07_llm_requests.sql: the purged runs' text.llm_requests chains first
--  (a request's chain_id is the node's llm_context_id — resolved from the
--  states, the store's node items and the context registry while the
--  linking rows still exist), then states (the self-FK cascades child
--  states, and contexts.state_id CASCADE removes their task-linked
--  context rows), then the thread's remaining contexts (kind
--  final-result and unlinked legacy rows), then the deep-agent store items
--  (namespace[1:2] carries the thread id), then the framework-managed
--  langgraph checkpoint tables (all keyed by thread_id).  The kept run's
--  thread graph stays fully browsable (its llm_requests chains stay too);
--  purged thread_ids simply disappear from the strip/dropdown.
--
--  Applied as ONE statement batch (psycopg multi-statement execute → one
--  transaction): the temp tables scope the purge list so every delete
--  targets exactly the threads that had states at prune time.
-- ============================================================================

CREATE TEMP TABLE _runs_all AS
    SELECT s.thread_id,
           ((MIN(s.created_at) AT TIME ZONE 'GMT')::date)      AS run_date,
           COUNT(*) FILTER (WHERE s.status = 'completed')::int AS done,
           COUNT(*) FILTER (WHERE s.status = 'failed')::int    AS failed,
           COUNT(*) FILTER (WHERE s.status IN ('pending', 'in_progress'))::int AS open,
           MIN(s.created_at)                                   AS first_at
    FROM text.deep_agent_states s
    GROUP BY s.thread_id;

CREATE TEMP TABLE _runs_purge AS
    SELECT thread_id
    FROM _runs_all
    WHERE open = 0
      AND thread_id NOT IN (
          SELECT DISTINCT ON (run_date) thread_id
          FROM _runs_all
          WHERE open = 0
          ORDER BY run_date, done DESC, failed ASC, first_at DESC);

DELETE FROM text.llm_requests
WHERE chain_id IN (
    SELECT s.llm_context_id
    FROM text.deep_agent_states s
    JOIN _runs_purge p USING (thread_id)
    WHERE s.llm_context_id IS NOT NULL
    UNION
    SELECT i.value ->> 'llm_context_id'
    FROM text.deep_agent_store_items i
    JOIN _runs_purge p
      ON i.namespace[1:2] = ARRAY['deep_agent', p.thread_id]::text[]
    WHERE i.value ->> 'llm_context_id' IS NOT NULL
    UNION
    SELECT i.key
    FROM text.deep_agent_store_items i
    JOIN _runs_purge p
      ON i.namespace[1:3] = ARRAY['deep_agent', p.thread_id, 'contexts']::text[]);

DELETE FROM text.deep_agent_states
WHERE thread_id IN (SELECT thread_id FROM _runs_purge);

DELETE FROM text.deep_agent_contexts
WHERE thread_id IN (SELECT thread_id FROM _runs_purge);

DELETE FROM text.deep_agent_store_items
WHERE namespace[1:2] IN (
    SELECT ARRAY['deep_agent', thread_id]::text[] FROM _runs_purge);

DELETE FROM langgraph.checkpoints
WHERE thread_id IN (SELECT thread_id FROM _runs_purge);

DELETE FROM langgraph.checkpoint_blobs
WHERE thread_id IN (SELECT thread_id FROM _runs_purge);

DELETE FROM langgraph.checkpoint_writes
WHERE thread_id IN (SELECT thread_id FROM _runs_purge);

DROP TABLE _runs_purge;
DROP TABLE _runs_all;
