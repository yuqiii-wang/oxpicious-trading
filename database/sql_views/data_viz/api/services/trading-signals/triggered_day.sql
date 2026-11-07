-- One day's triggered signals for a sec_type — the Trading Signals page's
-- main list. Two source roles, joined per (config, day) and deduped to one
-- row per (config, day) with the live tick winning:
--   • analysis_signals.history_signals — the day's close-trigger anchors
--     (the analysis run's 15:00 records; SMALLINT label codes decoded back
--     to the labels the live side speaks; the crossed cell bar arrives as
--     signal_reference and the legacy excess/threshold contract is derived),
--   • live.live_signals — the day's monitor breaches + day-close
--     observations (TEXT label columns, bound by the sec_type param).
-- Each row joins the sec_type's identity table (latest row per code) for
-- the display name and carries count_20d — the code's SIGNAL-DAY count
-- over the last 20 trading days ending on the anchor date.
-- Serves GET /api/live-data/trading-signals.
-- args:
--   sec_type: value text — the sec_type label ('index'|'etf'|'stock'); bound
--     as pg $1 — pinned by the {{sec_type}} reference in this line (slot
--     numbering is first-use order and the body below meets the date slot
--     first, so without the pin the date would render as $1 and renumber
--     the statement away from its pre-migration form)
--   date: value text — the anchor day, 'YYYY-MM-DD'
--   basic_stats_table: fragment — BASIC_STATS_TABLE[secType]
--   hist_sec_type_decode: fragment — labelDecodeSql("sec_type", "s.sec_type")
--   hist_signal_type_decode: fragment — labelDecodeSql("signal_type", "s.signal_type")
--   hist_signal_sub_type_decode: fragment — labelDecodeSql("signal_sub_type", "s.signal_sub_type")
--   wait_code: fragment — LABEL_CODE.action.wait
--   hist_sec_type_code: fragment — LABEL_CODE.sec_type[secType]
--   hist_options_wall_code: fragment — LABEL_CODE.signal_type.options_wall
--   hist_options_vix_code: fragment — LABEL_CODE.signal_type.options_vix
--   action_decode: fragment — labelDecodeSql("action", "r.action")
--   regime_decode: fragment — labelDecodeSql("regime", "r.regime_state")
--   identity_table: fragment — IDENTITY_TABLE[secType]
WITH win AS (
        -- 20th-most-recent DISTINCT close date <= the anchor:
        -- (win_start, anchor] spans exactly the last 20 trading days.
        -- DISTINCT: the baseline is per-(code, date) — one date has many
        -- rows, and OFFSET over raw rows would land less than a day back.
        SELECT DISTINCT date AS win_start
        FROM {{basic_stats_table}}
        WHERE date <= {{date}}::date AND close IS NOT NULL
        ORDER BY win_start DESC
        OFFSET 19 LIMIT 1
     ),
     recent AS (
        -- THE MONITOR SLICE — live rows carry no population variants
        -- (no boost twins, no pass stamp).
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
               FALSE         AS is_rejection
        FROM live.live_signals s
        WHERE s.sec_type = {{sec_type}} AND s.date = {{date}}::date
          -- the options families live on their own page (see
          -- fetchTradingSignalConfigs) — the shared day view excludes
          -- them
          AND s.signal_type <> 'options_wall'
          AND s.signal_type <> 'options_vix'
     ),
     history AS (
        -- THE ANALYSIS ANCHORS — the day's close-trigger records (one
        -- population since the boost twins' 2026-10-16 retirement) + the
        -- day's episode-pass attribute (is_typical, NULL pre-stamp). The
        -- rules-era table stores the crossed cell bar as signal_reference
        -- — the legacy response contract (excess / threshold) is DERIVED
        -- here so the live UNION and every consumer keep their shape.
        SELECT s.code,
               {{hist_sec_type_decode}} AS sec_type,
               {{hist_signal_type_decode}} AS signal_type,
               {{hist_signal_sub_type_decode}} AS signal_sub_type,
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
               (s.action = {{wait_code}}) AS is_rejection
        FROM analysis_signals.history_signals s
        WHERE s.sec_type = {{hist_sec_type_code}}
          AND s.date = {{date}}::date
          AND s.signal_type <> {{hist_options_wall_code}}
          AND s.signal_type <> {{hist_options_vix_code}}
     ),
     combined AS (
        SELECT h.* FROM history h
        UNION ALL
        SELECT r.* FROM recent r
     ),
     deduped AS (
        -- One record per (config, day): a live tick wins over the
        -- history anchor (earliest first — state families re-flag on
        -- every qualifying tick while the breach persists); a stronger
        -- confidence breaks remaining ties. The EVENT's pass stamp
        -- rides the partition (bool_or — one event carries exactly one
        -- stamp): a live tick winning a day inherits its history
        -- anchor's pass. is_rejection rides the partition too — a
        -- rejected NO-ACTION record never collapses into (or masks)
        -- its actionable twin.
        SELECT d.*,
               bool_or(d.is_typical) OVER (
                   PARTITION BY d.signal_type, d.signal_sub_type, d.date,
                                d.is_rejection
               ) AS event_is_typical,
               row_number() OVER (
                   PARTITION BY d.signal_type, d.signal_sub_type, d.date,
                                d.is_rejection
                   ORDER BY d.is_day_close_trigger, d.time,
                            d.confidence DESC
               ) AS rn
        FROM combined d
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
            (SELECT count(DISTINCT ls.date)::int
             FROM live.live_signals ls
             WHERE ls.sec_type = r.sec_type
               AND ls.code = r.code
               AND ls.date <= {{date}}::date
               AND ls.date > COALESCE(
                     (SELECT win_start FROM win), '1900-01-01'::date)
            ) AS count_20d
     FROM deduped r
     LEFT JOIN LATERAL (
       SELECT i.name FROM {{identity_table}} i
       WHERE i.code = r.code
       ORDER BY i.date DESC LIMIT 1
     ) n ON TRUE
     WHERE r.rn = 1
     ORDER BY r.confidence DESC, r.time DESC, r.code, r.signal_type,
              r.signal_sub_type
