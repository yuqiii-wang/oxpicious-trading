-- ============================================================================
--  Signal rules audit — pairwise family overlap
--  Table: analysis_signals.signal_overlap_audit — one row per YEAR
--  period per ORDERED pair of standalone signal families (A = base,
--  B = other): to what extent family A's history signals are also
--  observed in family B's history.
--
--  Overlap semantics (population = history_signals STANDALONE TRADING
--  tier, is_in_portfolio = FALSE, action IN (buy, sell),
--  confidence >= 0; events DISTINCT (code, date, signal_type)):
--    n_overlap     = |A days ∩ B days|            (same code, same date)
--    overlap_pct   = n_overlap / n_base           ("of A's signal days,
--                    the fraction where B also fired" — ASYMMETRIC:
--                    row = base family, column = other family)
--    expected_pct  = n_other / n_universe         (chance co-occurrence
--                    if A's days were random signal-days of the period)
--    lift          = overlap_pct / expected_pct   (> 1: A's days are
--                    genuinely enriched in B — structural correlation;
--                    <= 1: the co-firing is what chance gives)
--    jaccard       = n_overlap / (n_base + n_other - n_overlap)
--    n_same_action     = overlap days where A and B share at least one
--                        matching side (buy↔buy / sell↔sell)
--    n_opposite_action = overlap days where A and B fire opposite
--                        sides (buy↔sell — the conflict audit; a day
--                        can carry both matches)
--  Rows exist only for pairs with n_base > 0 AND n_other > 0, so the
--  ratio columns are NOT NULL by the writer's contract.
--
--  Period grid: CALENDAR YEARS keyed by `period` (e.g. 2020); counts
--  additive across years — the "All history" view is a SUM
--  re-aggregation in the API SQL. Incremental contract mirrors
--  signal_type_audit (analysis.signal_rules_audit: complete years
--  compute once, current + grace years recompute, --force truncates).
-- ============================================================================
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Pairwise signal-family overlap audit per year — of family A's trigger days, the share family B also fired on, judged against the chance expectation (lift) with Jaccard and same/opposite-action splits; locates which signal families genuinely co-fire and which conflict.
-- tags: signal overlap, 信号重叠, overlap, 重叠, co-occurrence, 共现, jaccard, lift, 提升度, signal rules audit, 信号规则审计, audit, 审计, conflict, 冲突, 同向, same action, opposite action, 反向, signal family, 信号家族


CREATE TABLE IF NOT EXISTS analysis_signals.signal_overlap_audit (
    period             SMALLINT      NOT NULL,  -- calendar year of the period (e.g. 2020); counts additive across years
    period_start       DATE          NOT NULL,  -- period start (Jan 1 of the year)
    period_end         DATE          NOT NULL,  -- period end (Dec 31 of the year)
    sec_type           SMALLINT      NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock
    signal_type        SMALLINT      NOT NULL,  -- BASE family A of the ordered pair (static.label_dim 'signal_type') — the matrix ROW
    other_signal_type  SMALLINT      NOT NULL,  -- co-occurring family B of the ordered pair — the matrix COLUMN (A→B overlap)
    n_base             INTEGER       NOT NULL,  -- A's DISTINCT (code, date) days in the period
    n_other            INTEGER       NOT NULL,  -- B's DISTINCT (code, date) days in the period
    n_universe         INTEGER       NOT NULL,  -- the period's DISTINCT (code, date) days with ANY family signal (chance denominator)
    n_overlap          INTEGER       NOT NULL,  -- days where BOTH A and B fired (same code, same date)
    n_same_action      INTEGER       NOT NULL,  -- overlap days with at least one matching side (buy↔buy / sell↔sell)
    n_opposite_action  INTEGER       NOT NULL,  -- overlap days with at least one opposite side (buy↔sell — conflicts; both matches can share a day)
    overlap_pct        NUMERIC(6,4)  NOT NULL,  -- n_overlap / n_base — of A's days, the fraction B also fired on (asymmetric A→B)
    expected_pct       NUMERIC(6,4)  NOT NULL,  -- n_other / n_universe — chance co-occurrence on a random signal-day
    jaccard            NUMERIC(6,4)  NOT NULL,  -- n_overlap / (n_base + n_other - n_overlap) — symmetric set similarity
    lift               NUMERIC(8,4)  NOT NULL,  -- overlap_pct / expected_pct — > 1 enrichment beyond chance, <= 1 chance-level
    computed_at        TIMESTAMPTZ   NOT NULL DEFAULT now(),

    CONSTRAINT pk_signal_overlap_audit PRIMARY KEY (sec_type, period, signal_type, other_signal_type),
    CONSTRAINT chk_signal_overlap_audit_sec_type CHECK (sec_type BETWEEN 0 AND 2),
    CONSTRAINT chk_signal_overlap_audit_counts CHECK (
        n_base > 0 AND n_other > 0 AND n_universe > 0
        AND n_overlap >= 0
        AND n_overlap <= n_base
        AND n_overlap <= n_other
        AND n_same_action <= n_overlap
        AND n_opposite_action <= n_overlap
        AND overlap_pct BETWEEN 0 AND 1
        AND expected_pct BETWEEN 0 AND 1
        AND jaccard BETWEEN 0 AND 1
    )
);

-- Matrix fetches (the audit page's heatmap + pair table, per period).
CREATE INDEX IF NOT EXISTS ix_signal_overlap_audit_period
    ON analysis_signals.signal_overlap_audit (sec_type, period DESC, signal_type, other_signal_type);

COMMENT ON TABLE analysis_signals.signal_overlap_audit IS
  'Signal rules audit, pairwise family overlap — one row per year period per ORDERED pair (A = base row, B = other column): the share of A''s standalone trigger days where B also fired (overlap_pct), the chance expectation (expected_pct = n_other / n_universe), the enrichment (lift), Jaccard similarity and the same/opposite-action splits of the co-firing days. Population: history_signals standalone trading tier (is_in_portfolio = FALSE, action buy/sell, confidence >= 0), events DISTINCT (code, date, signal_type). Populated by python -m analysis.signal_rules_audit.';

COMMENT ON COLUMN analysis_signals.signal_overlap_audit.other_signal_type IS
  'The co-occurring family (matrix column). The pair is ORDERED: the (A, B) row reads "of A''s signal days, the fraction B also fired on"; the (B, A) row is the mirror and carries its own counts.';
COMMENT ON COLUMN analysis_signals.signal_overlap_audit.lift IS
  'overlap_pct / expected_pct = n_overlap * n_universe / (n_base * n_other). Above 1: A''s days are enriched in B beyond chance (structural co-firing, e.g. capitulation families on crash days); at or below 1: chance-level.';
COMMENT ON COLUMN analysis_signals.signal_overlap_audit.n_opposite_action IS
  'Overlap days where A and B fired opposite sides (buy vs sell) on at least one pairing — the conflict audit: how often one family''s buy lands on another family''s sell the same day.';
