-- ============================================================================
--  Signal rules audit — per-family signal history census
--  Table: analysis_signals.signal_type_audit — one row per YEAR period
--  per standalone signal FAMILY: how many (code, date) days the family
--  fired on, the buy/sell split, its code/day spread, and its share of
--  the period's signal universe. The audit's family overview beside the
--  pairwise overlap table (signal_overlap_audit).
--
--  Population: analysis_signals.history_signals STANDALONE TRADING tier
--  (is_in_portfolio = FALSE, action IN (buy, sell), confidence >= 0 —
--  the wait twins and the REJECT tier are not trading signals). Events
--  collapse to DISTINCT (code, date, signal_type): a family's sub types
--  firing the same (code, date) count once.
--
--  Period grid: CALENDAR YEARS keyed by `period` (e.g. 2020). Counts
--  are ADDITIVE across years (year buckets are disjoint), so no
--  all-history rows are stored — the "All history" view is a SUM
--  re-aggregation in the API SQL. signal_universe (denormalized per
--  row) = the period's DISTINCT (code, date) days with ANY family's
--  signal — the chance denominator the overlap audit's expected_pct
--  divides by.
--
--  Incremental contract (analysis.signal_rules_audit): a COMPLETE year
--  computes once; the CURRENT (trailing) year and the most recent
--  --grace-years complete years recompute every run; --force truncates
--  both audit tables. A year with no events materializes no rows and
--  counts as covered.
-- ============================================================================
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Per-year census of each signal family's standalone history — trigger-day counts, buy/sell split, code/day spread and the family's share of the period's signal universe; the overview beside the pairwise signal overlap audit.
-- tags: signal rules audit, 信号规则审计, audit, 审计, signal family, 信号家族, trigger days, 触发日, history signals, 历史信号, family counts, 家族统计, overlap, 重叠, co-occurrence, 共现, signal universe, 信号覆盖


CREATE TABLE IF NOT EXISTS analysis_signals.signal_type_audit (
    period          SMALLINT      NOT NULL,  -- calendar year of the period (e.g. 2020); counts additive across years
    period_start    DATE          NOT NULL,  -- period start (Jan 1 of the year)
    period_end      DATE          NOT NULL,  -- period end (Dec 31 of the year)
    sec_type        SMALLINT      NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock
    signal_type     SMALLINT      NOT NULL,  -- label code (static.label_dim 'signal_type'): the family (0=mov_rsi 1=bollinger 2=ma_cross 3=options_wall 4=options_vix 5=momentum 6=rf_break 9=rebound 10=macd 11=crowdedness 12=volatility)
    n_events        INTEGER       NOT NULL,  -- DISTINCT (code, date) days the family fired in the period
    n_buy           INTEGER       NOT NULL,  -- of them, days with a buy action (a day can carry both sides: n_buy + n_sell >= n_events)
    n_sell          INTEGER       NOT NULL,  -- of them, days with a sell action
    n_codes         INTEGER       NOT NULL,  -- distinct codes with at least one event of the family
    n_days          INTEGER       NOT NULL,  -- distinct calendar dates among the events
    signal_universe INTEGER       NOT NULL,  -- the period's DISTINCT (code, date) days with ANY family signal — the chance denominator for expected_pct
    first_date      DATE          NOT NULL,  -- earliest event date of the family in the period
    last_date       DATE          NOT NULL,  -- latest event date of the family in the period
    computed_at     TIMESTAMPTZ   NOT NULL DEFAULT now(),

    CONSTRAINT pk_signal_type_audit PRIMARY KEY (sec_type, period, signal_type),
    CONSTRAINT chk_signal_type_audit_sec_type CHECK (sec_type BETWEEN 0 AND 2),
    CONSTRAINT chk_signal_type_audit_counts CHECK (
        n_buy >= 0 AND n_sell >= 0 AND n_events >= 0
        AND n_events <= signal_universe
    )
);

-- Period walks (the audit page's per-year selector).
CREATE INDEX IF NOT EXISTS ix_signal_type_audit_period
    ON analysis_signals.signal_type_audit (sec_type, period DESC, signal_type);

COMMENT ON TABLE analysis_signals.signal_type_audit IS
  'Signal rules audit, per-family census — one row per year period per standalone signal family (signal_type label code): DISTINCT (code, date) trigger-day counts, buy/sell split, code/day spread and the share of the period''s signal universe. Population: history_signals standalone trading tier (is_in_portfolio = FALSE, action buy/sell, confidence >= 0). Counts additive across years; populated by python -m analysis.signal_rules_audit.';

COMMENT ON COLUMN analysis_signals.signal_type_audit.period IS
  'Calendar year of the period (SMALLINT, e.g. 2020). No all-history rows: year buckets are disjoint so counts are additive — the All-history view SUM-re-aggregates in the API.';
COMMENT ON COLUMN analysis_signals.signal_type_audit.signal_universe IS
  'The period''s DISTINCT (code, date) days with ANY family''s signal — the chance denominator the overlap audit''s expected_pct = n_other / signal_universe divides by.';
COMMENT ON COLUMN analysis_signals.signal_type_audit.n_events IS
  'DISTINCT (code, date) days the family fired (sub types collapsed); n_buy + n_sell >= n_events because a day can carry both sides.';
