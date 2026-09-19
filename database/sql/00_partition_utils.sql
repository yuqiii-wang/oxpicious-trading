-- ============================================================================
--  Partition Utilities (shared across all schemas)
--  One helper that wraps the repetitive native hash-partition DDL:
--
--    CREATE TABLE <parent>_p00 PARTITION OF <parent>
--        FOR VALUES WITH (MODULUS n, REMAINDER 0);
--    ... x n
--
--  The result is still 100% native declarative partitioning — the function
--  only issues CREATE TABLE ... PARTITION OF statements, one per remainder.
--  Partition children are named <table>_p00 .. <table>_p{modulus-1}
--  (zero-padded to 2 digits), matching the naming used across the DB.
--
--  Usage in DDL files (idempotent, safe to re-run):
--    SELECT public.create_hash_partitions('analysis', 'mov_ave_spreads_detail', 16);
--
--  Guards:
--    - parent must exist and be PARTITION BY HASH (relkind = 'p')
--    - an existing child with a DIFFERENT bound (e.g. MODULUS 8 when 16 is
--      requested) raises instead of silently leaving mixed-modulus children
--    - a stray same-name table that is NOT attached to the parent raises
--      (loud failure instead of IF NOT EXISTS silently skipping)
-- ============================================================================

CREATE OR REPLACE FUNCTION public.create_hash_partitions(
    p_schema  text,
    p_table   text,
    p_modulus int
) RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
    parent_oid  oid;
    child_name  text;
    bound       text;
    expected    text;
    created     int := 0;
    r           int;
BEGIN
    IF p_modulus < 1 OR p_modulus > 1024 THEN
        RAISE EXCEPTION 'modulus must be within 1..1024, got %', p_modulus;
    END IF;

    SELECT c.oid INTO parent_oid
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = p_schema
      AND c.relname = p_table
      AND c.relkind = 'p';

    IF parent_oid IS NULL THEN
        RAISE EXCEPTION '%.%. is not a partitioned table', p_schema, p_table;
    END IF;

    FOR r IN 0 .. p_modulus - 1 LOOP
        child_name := p_table || '_p' || lpad(r::text, 2, '0');
        expected := format('FOR VALUES WITH (modulus %s, remainder %s)',
                           p_modulus, r);

        SELECT pg_get_expr(c.relpartbound, c.oid) INTO bound
        FROM pg_inherits i
        JOIN pg_class c ON c.oid = i.inhrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE i.inhparent = parent_oid
          AND n.nspname = p_schema
          AND c.relname = child_name;

        IF bound IS NOT NULL AND bound <> expected THEN
            RAISE EXCEPTION '%.%: existing bound "%" does not match requested "%" — refusing to mix moduli',
                p_schema, child_name, bound, expected;
        END IF;

        IF bound IS NULL THEN
            EXECUTE format(
                'CREATE TABLE %I.%I PARTITION OF %I.%I '
                'FOR VALUES WITH (MODULUS %s, REMAINDER %s)',
                p_schema, child_name, p_schema, p_table, p_modulus, r
            );
            created := created + 1;
        END IF;
    END LOOP;

    RETURN created;
END;
$$;

COMMENT ON FUNCTION public.create_hash_partitions(text, text, int) IS
    'Ensure <schema>.<table> has exactly <modulus> native hash partitions named _p00.._p{mod-1}. Idempotent; returns the number of partitions actually created (0 when all already exist).';

-- ============================================================================
--  CHECK-constraint helpers — the garbage-in gates for the trading tables
--  (side / state / period vocabularies, probability and confidence domains,
--  sign conventions). The two-function split keeps DDL applies non-blocking:
--
--    ensure_check_constraint        — idempotent ADD ... NOT VALID (a brief
--                                     metadata-only lock; concurrent COPY /
--                                     INSERT is never blocked).
--    validate_pending_checks        — VALIDATEs only the schema's still-
--                                     unvalidated chk_% constraints (the
--                                     full-scan validation runs ONCE; on an
--                                     already-clean store re-applies are
--                                     no-ops). A validation failure surfaces
--                                     REAL data bugs — fix the data, never
--                                     the gate.
--
--  Usage in DDL files (idempotent, safe to re-run):
--    SELECT public.ensure_check_constraint(
--        'analysis_forecasts.mov_rsi', 'chk_mov_rsi_side',
--        $chk$side IN ('top', 'bottom')$chk$);
--    SELECT public.validate_pending_checks('analysis_forecasts');
--
--  Naming convention: the constraint name MUST start with chk_ (the
--  validator only picks chk_% CHECK constraints in the schema).
-- ============================================================================

CREATE OR REPLACE FUNCTION public.ensure_check_constraint(
    p_target  regclass,
    p_name    name,
    p_check   text
) RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    IF NOT EXISTS (SELECT 1
                   FROM pg_constraint c
                   WHERE c.conname = ensure_check_constraint.p_name
                     AND c.conrelid = ensure_check_constraint.p_target
                     AND c.contype = 'c') THEN
        EXECUTE format(
            'ALTER TABLE %s ADD CONSTRAINT %I CHECK (%s) NOT VALID',
            ensure_check_constraint.p_target,
            ensure_check_constraint.p_name,
            ensure_check_constraint.p_check
        );
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.validate_pending_checks(
    p_schema text
) RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
    r        record;
    n        int := 0;
BEGIN
    FOR r IN
        SELECT c.conname, c.conrelid::regclass AS tbl
        FROM pg_constraint c
        JOIN pg_class t  ON t.oid = c.conrelid
        JOIN pg_namespace ns ON ns.oid = t.relnamespace
        WHERE ns.nspname = p_schema
          AND c.contype = 'c'
          AND c.conname LIKE 'chk_%'
          AND NOT c.convalidated
        ORDER BY c.conrelid, c.conname
    LOOP
        RAISE NOTICE 'validating %.%', r.tbl, r.conname;
        EXECUTE format('ALTER TABLE %s VALIDATE CONSTRAINT %I', r.tbl, r.conname);
        n := n + 1;
    END LOOP;
    RETURN n;
END;
$$;

COMMENT ON FUNCTION public.ensure_check_constraint(regclass, name, text) IS
    'Idempotently add a CHECK constraint (NOT VALID) to a table. Pairs with validate_pending_checks; the constraint name should start with chk_.';
COMMENT ON FUNCTION public.validate_pending_checks(text) IS
    'VALIDATE every still-unvalidated chk_% CHECK constraint of the schema (once — re-applies skip validated ones). Returns the number validated.';
