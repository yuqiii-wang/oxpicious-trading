-- Summary History phase 2 — the per-thread run rows, narrowed to the
-- picked day / id-prefix search by the caller's where_clause. The run's
-- plan context + identities live on the CHAIN ROOT (the plan thread):
-- every member row joins through the chain key, so the whole run
-- aggregates as ONE history row. scope_sec_type is a static.label_dim
-- SMALLINT code decoded via the caller's labelDecodeSql fragment.
-- Serves GET /api/ai/summary-runs.
-- args:
--   where_clause: fragment — the summaryRunFilterConds WHERE text / ""
--     (caller-built; carries the caller's $n params)
--   plan_kind: fragment — LABEL_CODE.agent_context_kind.plan (the
--     deep_agent_contexts kind code for the run's plan row)
--   scope_sec_type_decode: fragment — labelDecodeSql("sec_type",
--     "i.sec_type")
--   limit: fragment — clamped run-row count (caller-built literal)
SELECT COALESCE(s.super_thread_id, s.thread_id)     AS thread_id,
       ((s.created_at AT TIME ZONE 'GMT')::date)::text  AS run_date,
       MIN(p.payload ->> 'goal')                    AS goal,
       COUNT(*) FILTER (WHERE s.child_kind IS DISTINCT FROM 3)::int                          AS steps_total,
       COUNT(*) FILTER (WHERE s.status = 'completed'
       AND s.child_kind IS DISTINCT FROM 3)::int AS steps_completed,
       COUNT(*) FILTER (WHERE s.status = 'failed'
       AND s.child_kind IS DISTINCT FROM 3)::int    AS steps_failed,
       COUNT(*) FILTER (WHERE s.status = 'skipped'
       AND s.child_kind IS DISTINCT FROM 3)::int   AS steps_skipped,
       -- THE LIVE BADGE (2026-11-13): the run is STILL RUNNING while any
       -- member state is open OR the chain ROOT's store namespace holds a
       -- running node item — the root namespace is where the
       -- chain-orchestrator item (node:super) spans the whole P→C→R run,
       -- so this one EXISTS is the chain-wide process signal (the same
       -- union predicate the Thread Graph's poll loop reads; the strip
       -- can badge unfinished runs, a crashed daemon's settle lands at
       -- its next boot).
       BOOL_OR(s.status IN ('pending', 'in_progress')
               OR EXISTS (
                 SELECT 1
                   FROM llm.deep_agent_store_items it
                  WHERE it.namespace
                        = ARRAY['deep_agent',
                                COALESCE(s.super_thread_id, s.thread_id)
                               ]::text[]
                    AND it.key LIKE 'node:%'
                    AND it.value ->> 'status' = 'running'
               ))::boolean                              AS is_live,
       (MIN(s.created_at) AT TIME ZONE 'Asia/Shanghai')::text AS first_at,
       (MAX(s.finished_at) AT TIME ZONE 'Asia/Shanghai')::text AS last_at,
       i.code                                       AS scope_code,
       i.name                                       AS scope_name,
       {{scope_sec_type_decode}}                    AS scope_sec_type
  FROM llm.deep_agent_states s
  -- The run's plan context + identities live on the CHAIN ROOT (the plan
  -- thread): every member row joins through the chain key, so the whole
  -- run aggregates as ONE history row.
  LEFT JOIN llm.deep_agent_contexts p
         ON p.thread_id = COALESCE(s.super_thread_id, s.thread_id)
        AND p.kind = {{plan_kind}}
  -- One identity row per scope pick — a multi-subject run carries
  -- several; the row display (the dropdown label) reads ONE of them (the
  -- first by the index order), never a fan-out duplicate.
  LEFT JOIN LATERAL (
         SELECT sec_type, code, name
           FROM llm.deep_agent_identities
          WHERE thread_id = COALESCE(s.super_thread_id, s.thread_id)
          LIMIT 1
  ) i ON true
  {{where_clause}}
 GROUP BY 1, 2, i.sec_type, i.code, i.name
 ORDER BY 2 DESC, MIN(s.created_at) DESC
 LIMIT {{limit}}
