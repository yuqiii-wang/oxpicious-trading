-- ============================================================================
--  mov_ave_spread suite — split of the former 03_mov_ave_spreads.sql.
--  Shared conventions (sec_type handling, the NO-FK derived-data design,
--  rebuild semantics): see 01_mov_ave_spreads_detail.sql.
-- ============================================================================

-- Re-runnable DDL: the table is derived data, fully recomputed by the
-- next analysis.mov_ave_spread run after a DROP (the incremental
-- missing-date check sees every date missing).
DROP TABLE IF EXISTS analysis.mov_ave_spreads_roof_and_floor_bands CASCADE;
DELETE FROM analysis.analysis_identity WHERE name = 'mov_ave_spread_bands';

-- ----------------------------------------------------------------------------
--  Table: analysis.mov_ave_spreads_roof_and_floor_bands  (2026-10)
--    The UI's roof/floor SHADE BANDS, precomputed per (sec_type, code,
--    date, period) from the same anchors as
--    mov_ave_spreads_roof_and_floor_lines — plus per-band BREACH and
--    BOUNCE analytics (count + dates, ±1σ event semantics below) over
--    the row's own window.
--
--  Columns:
--    sec_type, code, date, period     — PK; period ∈ {20, 60, 120, 255,
--                                        500, 750, 1275} trading days
--    upper_band_low / upper_band_high — the ROOF band: min/max of the
--                                        window's two high anchors
--                                        (high_over_period,
--                                        high_2nd_over_period — the 1st
--                                        is an anchor-date CLOSE, the
--                                        2nd an anchor-date INTRADAY
--                                        HIGH; the pair is stored in
--                                        DATE order, hence the min/max)
--    lower_band_low / lower_band_high — the FLOOR band: min/max of the
--                                        window's two low anchors
--    upper_band_breach_count          — # UPPER breach DAYS in the
--                                        trailing `period` window ending
--                                        at `date` (see breach definition
--                                        below)
--    upper_band_breach_dates          — those breach days' dates
--    lower_band_breach_count          — # LOWER breach days, same window
--    lower_band_breach_dates          — those breach days' dates
--    upper_band_bounce_count          — # UPPER bounce DAYS, same window
--    upper_band_bounce_dates          — those bounce days' dates
--    lower_band_bounce_count          — # LOWER bounce days, same window
--    lower_band_bounce_dates          — those bounce days' dates
--
--  Event σ (shared by BREACH and BOUNCE): sigma[i] = population std
--  (ddof=0, the Bollinger convention) of the daily CLOSE CHANGES
--  (close[r] - close[r-1], price units) over the trailing 20 trading
--  rows ENDING AT i-1 — shifted one row so day i's classification
--  never reads its own move. NULL until 20 consecutive valid diffs
--  exist. Events need sigma: no sigma → no breach AND no bounce.
--
--  BREACH + BOUNCE are ANCHOR-BAND events (redefined 2026-10-02): row
--  (T, p)'s event columns describe touches/crossings of the row's OWN
--  band AS THE UI DRAWS IT — the near edge AFTER the shade cut (the
--  trailing-20-trading-row CLOSE extremes at T raise a roof's bottom
--  edge / lower a floor's top edge; when the strict cut empties the
--  band it retries at the anchor close; still-sub-visible bands
--  (< 0.02% of the anchor close) and windows without both anchors
--  carry NO edge and NO events) — over the row's window [T-p+1, T].
--  The UI reads a row's bounce/breach_dates arrays directly as its
--  marker set when anchored at T (the API serves the arrays; no
--  client-side recomputation). Day i ∈ [T-p+1, T] is an
--    UPPER BOUNCE of row (T, p) iff
--        |close[i] - roof_edge(T)| <= sigma[i]  — a ±1σ TOUCH of the
--            drawn edge (an approach closing just under it counts)
--        AND close[i] >= close[i-1]             — local peak of the
--            touch
--        AND close[i+1] < close[i]              — turned back down
--    an UPPER BREACH iff
--        close[i] > roof_edge(T) + sigma[i] AND close[i-1] <=
--        roof_edge(T)                           — a DIRECTIONAL entry
--            into the band from OUTSIDE: the prior close sat at/below
--            the drawn near edge and the day cleared the +1σ breach
--            line upward (an up move by construction). A close
--            already INSIDE the band that later clears +1σ never
--            marks — the price got there from the wrong side (e.g. a
--            rebound day after a breakdown fell into the zone);
--            staying beyond is not a new breach, and falling back
--            never marks.
--  Mirrors on the floor band's drawn top edge (touch zone ±1σ with a
--  local trough and close[i+1] > close[i]; breach = close[i-1] >=
--  edge with close[i] < edge - sigma[i] — a DOWNWARD entry from above
--  the floor). Bounce and breach are mutually exclusive
--  by construction (the touch zone caps at edge + sigma; breach is
--  strictly beyond). The close[i+1] confirmation means a row's LAST
--  day cannot (yet) bounce — incremental runs purge + recompute the
--  last existing date so the pending touch lands once its
--  confirmation day exists.
--
--  count == cardinality(dates) by construction for both families
--  (dates ascending, inside the row's own trailing window).
--
--  Populated by the internal bands step of `analysis.mov_ave_spread`
--  (roof_floor_bands.py — reuses the parent pipeline's source DataFrame,
--  no second DB round-trip). Sibling of run_ohlc (same windows, same
--  anchors); the UI's "shade" toggle reads this table instead of
--  deriving band edges client-side, and draws event markers from the
--  breach/bounce_dates arrays (solid triangle = bounce, light
--  transparent triangle = breach).
-- ----------------------------------------------------------------------------
CREATE TABLE analysis.mov_ave_spreads_roof_and_floor_bands (
    sec_type          SMALLINT     NOT NULL,  -- label code (stats.label_dim 'sec_type'): 0=etf 1=index 2=stock
    code              TEXT         NOT NULL,
    date              DATE         NOT NULL,
    period            INTEGER      NOT NULL,  -- window size in trading days

    upper_band_low    NUMERIC(18,6),
    upper_band_high   NUMERIC(18,6),
    lower_band_low    NUMERIC(18,6),
    lower_band_high   NUMERIC(18,6),

    upper_band_breach_count  INTEGER NOT NULL,
    upper_band_breach_dates  DATE[]  NOT NULL,
    lower_band_breach_count  INTEGER NOT NULL,
    lower_band_breach_dates  DATE[]  NOT NULL,

    upper_band_bounce_count  INTEGER NOT NULL,
    upper_band_bounce_dates  DATE[]  NOT NULL,
    lower_band_bounce_count  INTEGER NOT NULL,
    lower_band_bounce_dates  DATE[]  NOT NULL,

    CONSTRAINT pk_mov_ave_spreads_roof_and_floor_bands
        PRIMARY KEY (code, sec_type, date, period)
) PARTITION BY HASH (code);

-- Native hash partitions (32) keyed on code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p31
SELECT public.create_hash_partitions('analysis', 'mov_ave_spreads_roof_and_floor_bands', 32);

COMMENT ON TABLE  analysis.mov_ave_spreads_roof_and_floor_bands IS 'Roof/floor SHADE BANDS + per-band anchor-band breach/bounce analytics (LONG format): one row per (code, sec_type, date, period ∈ {20,60,120,255,500,750,1275}). Band edges: upper_band_low/high = min/max of the window''s two high anchors (high_over_period = 1st-half max CLOSE, high_2nd_over_period = 2nd anchor''s INTRADAY HIGH — the pair is stored in DATE order, hence min/max); lower_band_low/high = the mirror on the two low anchors. Event σ: sigma[i] = population std (ddof=0) of the daily close changes over the trailing 20 trading rows ending at i-1 (shifted 1 row; NULL until 20 valid diffs). BREACH/BOUNCE are ANCHOR-BAND events — measured against the ROW''S OWN band AS THE UI DRAWS IT: the near edge after the shade cut (the trailing-20-row close extremes at the anchor raise a roof''s bottom / lower a floor''s top, with the anchor-close fallback and the sub-visible < 0.02% skip), over the row''s window [date-period+1, date]. Day i is an upper bounce iff |close[i] - roof_edge| <= sigma[i] AND close[i] >= close[i-1] AND close[i+1] < close[i] (±1σ touch of the drawn edge, local peak, turned back); an upper breach iff close[i] > roof_edge + sigma[i] with close[i-1] <= roof_edge (a DIRECTIONAL entry into the band from OUTSIDE: the prior close sat at/below the drawn near edge and the day cleared the +1σ breach line upward — an up move by construction; a close already inside the band that later clears +1σ is not a breach). Mirror on the floor''s drawn top edge: close[i-1] >= floor_edge with close[i] < floor_edge - sigma[i] — a downward entry from above the floor. Mutually exclusive by construction. The UI reads the anchor row''s *_dates arrays directly as its marker set. Source: same DataFrame as the mov_ave_spread parent pipeline (roof_floor_bands.py internal step).';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_bands.sec_type IS 'Security type as its stats.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf (ETF), 1 = index (CSI-style index), 2 = stock (individual equity).';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_bands.code IS 'Ticker. ETFs bare 6-digit (e.g. "510050"); indices bare code (e.g. "000300"); stocks keep their exchange suffix (e.g. "600008.SS").';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_bands.date IS 'Business date (trading day) — the anchor date of the shade band (the window ends here).';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_bands.period IS 'Rolling-window size in trading days: one of {20, 60, 120, 255, 500, 750, 1275}. Part of the PK — the long format stores one row per (code, sec_type, date, period).';

COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_bands.upper_band_low  IS 'ROOF band lower edge: LEAST(high_over_period, high_2nd_over_period) of the trailing period window (the two anchors the roof trendline connects; the 1st is an anchor-date CLOSE, the 2nd an anchor-date INTRADAY HIGH). NULL when either anchor is absent for the window.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_bands.upper_band_high IS 'ROOF band upper edge: GREATEST(high_over_period, high_2nd_over_period). NULL when either anchor is absent for the window.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_bands.lower_band_low  IS 'FLOOR band lower edge: LEAST(low_over_period, low_2nd_over_period) of the trailing period window. NULL when either anchor is absent for the window.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_bands.lower_band_high IS 'FLOOR band upper edge: GREATEST(low_over_period, low_2nd_over_period). NULL when either anchor is absent for the window.';

COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_bands.upper_band_breach_count IS 'Number of UPPER breach days inside the row''s own trailing period window [date-period+1, date], measured against THIS row''s CUT-ADJUSTED roof bottom edge (the band the UI draws for this anchor date: upper_band_low raised to the trailing-20-row max close, anchor-close fallback, sub-visible skip). Breach day i: close[i] > roof_edge + sigma[i] with close[i-1] <= roof_edge — a DIRECTIONAL entry into the band from OUTSIDE (the prior close at/below the drawn near edge; sigma = population std of the daily close changes over the trailing 20 trading rows ending at i-1, so clearing the +1σ line from outside is an up move by construction; a close already inside the band that later clears +1σ is not a breach); staying beyond is not a new breach. Always == cardinality(upper_band_breach_dates); 0 for an empty window. NOT NULL.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_bands.upper_band_breach_dates IS 'Dates of the upper breach days of THIS row''s cut-adjusted band in [date-period+1, date] (ascending). Empty array ''{}'' when none. NOT NULL. The UI reads this array of the anchor row directly as its breach marker set (no client-side recomputation).';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_bands.lower_band_breach_count IS 'Number of LOWER breach days inside the row''s own trailing period window [date-period+1, date], measured against THIS row''s CUT-ADJUSTED floor top edge. Breach day i: close[i] < floor_edge - sigma[i] with close[i-1] >= floor_edge — a DIRECTIONAL entry into the band from OUTSIDE (the prior close at/above the drawn near edge, so clearing the −1σ line is a down move by construction; a close already inside the band that later clears −1σ is not a breach). Always == cardinality(lower_band_breach_dates); 0 for an empty window. NOT NULL.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_bands.lower_band_breach_dates IS 'Dates of the lower breach days of THIS row''s cut-adjusted band in [date-period+1, date] (ascending). Empty array ''{}'' when none. NOT NULL.';

COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_bands.upper_band_bounce_count IS 'Number of UPPER bounce days inside the row''s own trailing period window [date-period+1, date], measured against THIS row''s CUT-ADJUSTED roof bottom edge. Bounce day i: |close[i] - roof_edge| <= sigma[i] (a ±1σ TOUCH of the drawn edge — an approach closing just under it counts) AND close[i] >= close[i-1] (local peak of the touch) AND close[i+1] < close[i] (turned back down). Mutually exclusive with a breach by construction. The close[i+1] confirmation means a row''s last day cannot yet be a bounce (incremental runs recompute it). Always == cardinality(upper_band_bounce_dates); 0 for an empty window. NOT NULL.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_bands.upper_band_bounce_dates IS 'Dates of the upper bounce days of THIS row''s cut-adjusted band in [date-period+1, date] (ascending). Empty array ''{}'' when none. NOT NULL. The UI reads this array of the anchor row directly as its bounce marker set (no client-side recomputation).';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_bands.lower_band_bounce_count IS 'Number of LOWER bounce days inside the row''s own trailing period window [date-period+1, date], measured against THIS row''s CUT-ADJUSTED floor top edge. Bounce day i: |close[i] - floor_edge| <= sigma[i] AND close[i] <= close[i-1] (local trough) AND close[i+1] > close[i] (turned back up). Mutually exclusive with a breach by construction. Always == cardinality(lower_band_bounce_dates); 0 for an empty window. NOT NULL.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_bands.lower_band_bounce_dates IS 'Dates of the lower bounce days of THIS row''s cut-adjusted band in [date-period+1, date] (ascending). Empty array ''{}'' when none. NOT NULL.';
