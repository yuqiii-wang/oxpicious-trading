-- Trading signals history — the unioned multi-year record for ONE code:
--   • analysis_signals.history_signals — the multi-year record replayed
--     from the rules tier (15:00 day-close anchors; label-code columns
--     decoded back to the labels the union speaks via the caller's
--     labelDecodeSql fragments),
--   • analysis_signals.no_action_signals — the NO-ACTION OVERLAY marks
--     (2026-11-18): one row per (fired positive day x declined action x
--     reason) — negative-confidence advice AGAINST the fired day's own
--     action, OVERLAYING (never suppressing) its positive twin (the
--     dedup partition carries is_no_action so both rows survive),
--   • live.live_signals — the RECENT 20-TRADING-DAY SLICE ONLY: the
--     recorded breaches inside (win_start, anchor]. Older days belong to
--     the history record — the union never re-scans them.
-- The 20d window anchor ('viewed day or infinity') and the per-tier
-- predicates ride caller-built fragments — the live tier keeps TEXT
-- sec_type/signal_type columns (its params carry the labels) while the
-- history tier stores the SMALLINT label codes — so the $n params stay
-- caller-assembled. Serves GET /api/live-data/trading-signals/history.
-- args:
--   basic_stats_table: fragment — BASIC_STATS_TABLE[st] (the win CTE's
--     distinct-close source)
--   identity_table: fragment — IDENTITY_TABLE[st] (the name CTE's source)
--   where_clause: fragment — the live-tier predicates joined " AND "
--     (aliased s.; carries the caller's $n params)
--   hist_where_clause: fragment — the history-tier predicates joined
--     " AND " (aliased s.; applies to the history AND no-action tiers —
--     both store the SMALLINT label codes; label-code literals + caller
--     $n params)
--   anchor_expr: fragment — "$n::date" (the viewed day) or
--     "'infinity'::date"
--   sec_type_decode: fragment — labelDecodeSql("sec_type", "s.sec_type")
--   signal_type_decode: fragment — labelDecodeSql("signal_type",
--     "s.signal_type")
--   signal_sub_type_decode: fragment — labelDecodeSql("signal_sub_type",
--     "s.signal_sub_type")
--   action_decode: fragment — labelDecodeSql("action", "r.action")
--   regime_decode: fragment — labelDecodeSql("regime", "r.regime_state")
--   no_action_reason_decode: fragment — labelDecodeSql(
--     "no_action_reason", "s.reason_code") — the no-action tier's
--     reason vocabulary decode
--   action_wait: fragment — LABEL_CODE.action.wait (the wait-action code
--     literal: the is_rejection derivation)
--   name_code: fragment — "$2" (the code param the predicates bind; the
--     name CTE reuses it)
WITH win AS (
    -- 20th-most-recent DISTINCT close date before the anchor:
    -- (win_start, anchor] spans exactly the last 20 trading days.
    SELECT win_start
    FROM (
        SELECT DISTINCT date AS win_start
        FROM {{basic_stats_table}}
        WHERE date <= {{anchor_expr}} AND close IS NOT NULL
        ORDER BY win_start DESC
        OFFSET 19 LIMIT 1
    ) w
 ),
 recent AS (
    -- THE 20d SLICE — live tier ONLY: recorded breaches inside the
    -- anchor's last 20 trading days (the count_20d window). The live
    -- table contributes nothing outside this window — older days
    -- belong to the history record below, so the union never
    -- re-scans live's back catalog. Live rows carry no population
    -- variants (no boost twins, no pass stamp).
    SELECT s.code,
           s.sec_type,
           s.signal_type,
           s.signal_sub_type,
           s.date,
           s.time,
           s.action,
           s.signal_excess::float8        AS signal_excess,
           s.signal_excess_pct::float8    AS signal_excess_pct,
           s.signal::float8               AS signal,
           s.signal_threshold::float8     AS signal_threshold,
           s.confidence,
           (s.confidence::float8 / 100.0) AS confidence_pct,
           s.is_day_close_trigger,
           s.regime_state,
           NULL::boolean AS is_typical,
           FALSE        AS is_rejection,
           FALSE        AS is_no_action,
           NULL::text   AS no_action_reason
    FROM live.live_signals s
    WHERE {{where_clause}}
      AND s.date > (SELECT win_start FROM win)
      AND s.date <= {{anchor_expr}}
 ),
 history AS (
    -- THE PLOT BULK — the multi-year record replayed from the rules
    -- tier (15:00 day-close anchors), one population per event (the
    -- is_ohlcv_boosted / is_pe_boosted variant axes left the schema
    -- 2026-11-01); is_typical TRUE = the typical pass, FALSE =
    -- the re-entry pass, NULL = the family does not classify passes
    -- (no toggle filters on it any more). The rules-era table stores
    -- the crossed cell bar as signal_reference — the legacy response
    -- contract (excess / threshold) is DERIVED here so the union
    -- keeps its shape.
    SELECT s.code,
           {{sec_type_decode}} AS sec_type,
           {{signal_type_decode}} AS signal_type,
           {{signal_sub_type_decode}} AS signal_sub_type,
           s.date,
           s.time,
           s.action,
           (s.signal - s.signal_reference)::float8 AS signal_excess,
           ((s.signal - s.signal_reference)
            / NULLIF(abs(s.signal_reference), 0) * 100)::float8
                                        AS signal_excess_pct,
           s.signal::float8               AS signal,
           s.signal_reference::float8     AS signal_threshold,
           s.confidence,
           (s.confidence::float8 / 100.0) AS confidence_pct,
           s.is_day_close_trigger,
           s.regime_state,
           s.is_typical,
           -- the is_rejection column was dropped 2026-10-14 — the
           -- grey-diamond wait event IS the action code now
           (s.action = {{action_wait}}) AS is_rejection,
           FALSE        AS is_no_action,
           NULL::text   AS no_action_reason
    FROM analysis_signals.history_signals s
    WHERE {{hist_where_clause}}
 ),
 noaction AS (
    -- THE NO-ACTION OVERLAY (2026-11-18) — analysis_signals.
    -- no_action_signals: day-level negative-confidence marks on FIRED
    -- POSITIVE days (engines/no_action's invalidation conditions).
    -- A row re-states its fired twin's own identity/values with
    -- NEGATIVE confidence = do NOT take this action at this price
    -- (never the inverse action); the reason names WHY (the squeeze
    -- breakdown, the fatigue chop, ...). The dedup partition carries
    -- is_no_action so a mark never collapses into its positive twin —
    -- the overlay's whole point is that BOTH rows stay visible.
    SELECT s.code,
           {{sec_type_decode}} AS sec_type,
           {{signal_type_decode}} AS signal_type,
           {{signal_sub_type_decode}} AS signal_sub_type,
           s.date,
           s.time,
           s.action,
           (s.signal - s.signal_reference)::float8 AS signal_excess,
           ((s.signal - s.signal_reference)
            / NULLIF(abs(s.signal_reference), 0) * 100)::float8
                                        AS signal_excess_pct,
           s.signal::float8               AS signal,
           s.signal_reference::float8     AS signal_threshold,
           s.confidence,
           (s.confidence::float8 / 100.0) AS confidence_pct,
           TRUE                           AS is_day_close_trigger,
           s.regime_state,
           NULL::boolean AS is_typical,
           FALSE        AS is_rejection,
           TRUE         AS is_no_action,
           {{no_action_reason_decode}} AS no_action_reason
    FROM analysis_signals.no_action_signals s
    WHERE {{hist_where_clause}}
 ),
 combined AS (
    SELECT h.* FROM history h
    UNION ALL
    SELECT n.* FROM noaction n
    UNION ALL
    SELECT r.* FROM recent r
 ),
 deduped AS (
    -- One record per (config, day): a live tick wins over the
    -- history anchor (earliest first — state families re-flag on
    -- every qualifying tick while the breach persists); a stronger
    -- confidence breaks remaining ties (the retired day-close
    -- mirror rows). The EVENT's pass stamp rides the partition
    -- (max — one event carries exactly one stamp): a live tick
    -- winning a day inherits its history anchor's pass, so the
    -- pass toggle still sees the day. is_rejection AND is_no_action
    -- ride the partition too — a rejected / no-action-marked record
    -- never collapses into its actionable twin.
    SELECT d.*,
           bool_or(d.is_typical) OVER (
               PARTITION BY d.signal_type, d.signal_sub_type, d.date,
                            d.is_rejection, d.is_no_action
           ) AS event_is_typical,
           row_number() OVER (
               PARTITION BY d.signal_type, d.signal_sub_type, d.date,
                            d.is_rejection, d.is_no_action
               ORDER BY d.is_day_close_trigger, d.time,
                        d.confidence DESC
           ) AS rn
    FROM combined d
 ),
 name AS (
    SELECT i.name FROM {{identity_table}} i
    WHERE i.code = {{name_code}}
    ORDER BY i.date DESC LIMIT 1
 )
 SELECT r.code,
        n.name                       AS code_name,
        r.sec_type,
        r.signal_type,
        r.signal_sub_type,
        to_char(r.date, 'YYYY-MM-DD') AS date,
        to_char(r.time, 'HH24:MI')    AS time,
        {{action_decode}} AS action,
        r.signal_excess,
        r.signal_excess_pct,
        r.signal,
        r.signal_threshold,
        r.confidence,
        r.confidence_pct,
        r.is_day_close_trigger,
        {{regime_decode}} AS regime_state,
        r.event_is_typical AS is_typical,
        r.is_rejection,
        r.is_no_action,
        r.no_action_reason,
        (SELECT to_char(win_start, 'YYYY-MM-DD') FROM win)
            AS recent_window_start
 FROM deduped r
 LEFT JOIN name n ON TRUE
 WHERE r.rn = 1
 ORDER BY r.date DESC, r.time DESC, r.confidence DESC
 LIMIT 2000
