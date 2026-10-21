-- ============================================================================
--  Table: analysis_signals.portfolio_signal_rules  (REFACTORED 2026-10-18:
--  per-sub-type GROUPS with an ids ARRAY; convention-aligned to
--  signal_rules 2026-10-20; SIMPLIFIED the same day's cell-level redesign:
--  the signal_sub_type axis DROPPED — ONE ROW PER (code, portfolio_type),
--  the whole portfolio's member cells in the single ids ARRAY, the
--  per-sub-type coordination specs nested under rule_params.subs).
--
--  PORTFOLIO SIGNAL RULES — the PORTFOLIO tier over signal_rules: a
--  portfolio is a code whose CELL-LEVEL RULE CELLS act as ONE managed
--  group (the negative-corr hedge pair precedent — the composite 'A~B'
--  long–long pair portfolio). ONE ROW PER (code, portfolio_type):
--
--    - code               the portfolio's identity + SEARCH KEY (the
--      pick-a-portfolio lookup starts here); 'A~B' lexicographic for
--      the pair types.
--    - portfolio_type     WHAT THE CODE IS as a portfolio — its own
--      static.label_dim domain (NOT the signal family; 0 =
--      neg_corr_hedge_pair, the two-leg long–long negatively-
--      correlated hedge pair).
--    - signal_rule_ids    BIGINT[] — THE ARRAY. ALL the portfolio's
--      MEMBER rule-row surrogate ids ACROSS SUB TYPES (entry /
--      harvest / catch-up / stop / regime-flip cells together):
--      SELECT ... FROM signal_rules r WHERE r.signal_rule_id =
--      ANY(p.signal_rule_ids) finds this portfolio's member cells —
--      exactly the cells whose signal_rules.is_in_portfolio is TRUE
--      through this group. Array length = the portfolio's total
--      member-cell count (the members differ by sub type, trend,
--      regime, action).
--    - is_valid           the PORTFOLIO group's GATE VERDICT
--      (2026-10-20): TRUE iff the src data TOGETHER passed the
--      PORTFOLIO GATE (the audited benefit clearing its bar — the
--      hedge type: the max drawdown reduction vs the MEAN leg >= 0pp)
--      AND at least one ORCHESTRATING composite cell's own is_valid
--      (the dir_ave verdict on the coordination itself). THE GATING
--      conjunction for the confidence cascade: only a group with
--      is_valid TRUE lets its member cells see the cell-level
--      confidence increase (the cascade scale on the
--      history_portfolio_signal_performance snapshots' gate digest,
--      applied by the live tier beside the starvation sample scale).
--      FALSE = the group audits below its benefit bar or every
--      orchestration measured out / is observational — members keep
--      their OWN cell verdicts (never discounted), just no cascade.
--    - rule_entry         the engine entries that wrote the group (the
--      signal_rules column's portfolio counterpart) — the
--      ORCHESTRATING RuleEntry subclass names under
--      analysis/analysis_signals/engines/signal_families/, '|'-
--      joined (a portfolio's subs are written by up to two entries:
--      the hp_rsi orchestrator + the pair-native sibling).
--    - rule_params        JSONB — the CONSTANT PORTFOLIO rule
--      definition in the signal_rules rule_params GRAMMAR (portfolio-
--      level since the 2026-10-20 simplification): the shared
--      top-level keys carry the SAME values the member cells' params
--      carry (universe / pair_weighting / episode_merge_days / the
--      gate bars / the confidence CASCADE scale), the portfolio tier's
--      own CONSTRUCTION block, and the PER-SUB coordination specs
--      nested under ``subs`` keyed by sub-type label:
--        axis               the portfolio_type label (signal_rules
--                           carries the family label).
--        universe           top-level, verbatim the cells' string.
--        pair_weighting     top-level STRING, verbatim the cells'
--                           ("inverse-vol 60d, lagged one row").
--        episode_merge_days the K the coordinated episodes merge on
--                           (the cells' own K).
--        gate               the PORTFOLIO GATE bars {metric
--                           max_dd_reduction_vs_mean, min_pp} — the
--                           signal_rules gate convention (constants
--                           here; the VERDICT digest rides the dated
--                           history_portfolio_signal_performance
--                           snapshots).
--        confidence         the CASCADE scale {factor, unit_pp, cap}
--                           — the signal_rules confidence convention
--                           applied to the benefit depth: the member
--                           cells' increase is 1 + factor x
--                           min(benefit_pp / unit_pp, cap), applied
--                           ONLY on groups that passed (is_valid).
--        portfolio          {legs, market, aim} — the CONSTRUCTION
--                           block: the pair's legs, the offset-corr
--                           market leg, and the AIM the portfolio is
--                           run for (max_drawdown_reduction — the
--                           audited claim history_portfolio_signal_
--                           performance records).
--        subs               {<sub_type label>: {axis, response,
--                           coordination, observational?}} — the
--                           per-sub-type coordination specs (the
--                           2026-10-20 pre-simplification top-level
--                           blocks, one per sub type): axis
--                           "<portfolio_type>:<sub>", response (the
--                           sub's response axis — inverse_vol_pair_
--                           portfolio / laggard_leg / strong_leg),
--                           coordination (kind-first the bar_def
--                           convention; kind reuses the member cells'
--                           bar_def.kind, the param keys the family
--                           doc's sub_signals params vocabulary),
--                           observational TRUE on the ungated subs.
--
--  Deliberately NO trend / regime / action columns (the tier's PK has
--  no cell axes — a group holds its cells across them), NO sub-type
--  column (the members' own signal_rules.signal_sub_type carries it;
--  the per-sub specs live in rule_params.subs), and no cell-level
--  gate bars (the members' own gates govern their emission; the
--  portfolio tier's verdict axis is the BENEFIT audit in
--  history_portfolio_signal_performance, denormalized here as
--  is_valid).
--
--  No DB FK to signal_rules (hash-partitioned: no UNIQUE over the id
--  alone, hence no FK target) — the WRITER owns referential
--  integrity: run.py stamps fresh ids on the rule rows and rewrites
--  these groups in the same run, both scoped (sec_type,
--  portfolio_type) and wiped together. The ids identify rule ROW
--  EMISSIONS (the rules tier is current-state — a family's scope is
--  wiped and re-inserted every pipeline run), so both tables churn
--  together and are always read as a pair.
--
--  The PORTFOLIO knowledge document (the type's construction / aim /
--  benefit vocabulary) lives ONE row per portfolio_type in
--  static.portfolio_signal_docs (seeded from database/sql/static/
--  portfolio_signal_docs/) — the static.analysis_signal_docs
--  convention at the portfolio tier; the portfolio API joins it per
--  portfolio_type at the read boundary.
--
--  Which families register: engines/signal_families.
--  FAMILY_PORTFOLIO_TYPE (hedge_pairs → neg_corr_hedge_pair today).
--
--  Read path: the portfolio signal APIs match rows by code (the legs
--  of 'A~B' must sit in the requested codes) and expand
--  signal_rule_ids to the member rule rows; the Hedge Pairs UI
--  renders the group's rules through the shared portfolio-signal
--  component.
-- ============================================================================

DROP TABLE IF EXISTS analysis_signals.portfolio_signal_rules CASCADE;

CREATE TABLE analysis_signals.portfolio_signal_rules (
    code            TEXT         NOT NULL,  -- the PORTFOLIO code — the identity + search key (the pair types' composite 'A~B', lexicographic)
    sec_type        SMALLINT     NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock — the owning rule rows' stamp (the pipeline run's sec_type)
    portfolio_type  SMALLINT     NOT NULL,  -- label code (static.label_dim 'portfolio_type'): 0 neg_corr_hedge_pair — WHAT the code is as a portfolio (replaces the per-rule shape's signal_type discriminator)
    signal_rule_ids BIGINT[]     NOT NULL,  -- THE ARRAY: ALL the portfolio's MEMBER rule-row surrogate ids ACROSS SUB TYPES (signal_rules.signal_rule_id; those cells' is_in_portfolio = TRUE through this group) — = ANY(this) finds the portfolio's member cells
    is_valid        BOOLEAN      NOT NULL DEFAULT false,  -- the PORTFOLIO group's GATE VERDICT (2026-10-20): TRUE iff the audited benefit cleared the portfolio gate AND at least one orchestrating composite cell's own verdict holds — the GATING conjunction for the member confidence cascade (members see the increase only on is_valid groups; their own verdicts are never discounted)
    rule_entry      TEXT         NOT NULL,  -- the engine entries that wrote the group (the orchestrating RuleEntry subclass names under analysis/analysis_signals/engines/signal_families/, '|'-joined) — the signal_rules column's portfolio counterpart
    rule_params     JSONB        NOT NULL DEFAULT '{}',  -- the CONSTANT PORTFOLIO rule definition in the signal_rules rule_params GRAMMAR (portfolio-level: top-level axis / universe / pair_weighting / episode_merge_days + the gate bars + the confidence CASCADE scale + the portfolio {legs, market, aim} construction + the PER-SUB coordination specs under subs) — engine-generated; the MEASURED benefit audit lives in history_portfolio_signal_performance (05)

    created_at      TIMESTAMP    NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_portfolio_signal_rules PRIMARY KEY (code, portfolio_type),
    CONSTRAINT chk_portfolio_signal_rules_sec_type CHECK (sec_type BETWEEN 0 AND 2),
    CONSTRAINT chk_portfolio_signal_rules_ids_not_empty CHECK (cardinality(signal_rule_ids) >= 1)
);

-- Type-first lookup (a portfolio type's registered groups).
CREATE INDEX IF NOT EXISTS idx_portfolio_signal_rules_type
    ON analysis_signals.portfolio_signal_rules (sec_type, portfolio_type);

COMMENT ON TABLE analysis_signals.portfolio_signal_rules IS 'Portfolio signal rules — the PORTFOLIO tier over signal_rules (refactored 2026-10-18; convention-aligned to signal_rules'' column order + rule_params grammar 2026-10-20; the sub-type axis DROPPED the same day''s cell-level redesign): ONE ROW PER (code, portfolio_type) whose signal_rule_ids BIGINT[] holds ALL the portfolio''s member rule-row surrogate ids ACROSS SUB TYPES — = ANY(signal_rule_ids) finds the portfolio''s member cells, exactly the cells whose signal_rules.is_in_portfolio is TRUE through this group — and whose rule_params JSONB carries the CONSTANT portfolio construction in the signal_rules grammar (top-level axis / universe / pair_weighting / episode_merge_days + the gate bars + the confidence CASCADE scale + the portfolio {legs, market, aim} block + the per-sub-type coordination specs nested under subs keyed by sub-type label; the MEASURED benefit audit lives in history_portfolio_signal_performance). is_valid = the audited benefit cleared the portfolio gate AND at least one orchestrating cell''s own verdict — the GATING conjunction: only is_valid groups let their member cells see the cell-level confidence increase (the cascade scale on the history_portfolio_signal_performance snapshots'' gate digest). code is the identity + search key; portfolio_type is the portfolio tier''s own vocabulary (0 neg_corr_hedge_pair — NOT the signal family). Populated by python -m analysis.analysis_signals in the same run that stamps the rule ids (both scopes wiped + rewritten together — no DB FK by partition-design, the writer owns integrity). The portfolio knowledge document lives in static.portfolio_signal_docs (one row per portfolio_type).';
COMMENT ON COLUMN analysis_signals.portfolio_signal_rules.code IS 'The PORTFOLIO code — the identity + SEARCH KEY. The pair types: the composite ''A~B'' id (lexicographic, mirroring the offsets table''s dedup) — the long–long pair portfolio the family''s rule group trades.';
COMMENT ON COLUMN analysis_signals.portfolio_signal_rules.sec_type IS 'The owning rule rows'' sec_type stamp as its static.label_dim (''sec_type'') SMALLINT code (0=etf 1=index 2=stock) — the pipeline run''s sec_type scope (hedge_pairs runs the default index scope).';
COMMENT ON COLUMN analysis_signals.portfolio_signal_rules.portfolio_type IS 'WHAT the code IS as a portfolio, as its static.label_dim (''portfolio_type'') SMALLINT code — the portfolio tier''s OWN vocabulary, deliberately NOT the signal family (a portfolio type can outlive family renames; one type may group rules from several families). 0 = neg_corr_hedge_pair, the two-leg long–long negatively-correlated hedge pair. Replaces the earlier per-rule shape''s signal_type discriminator.';
COMMENT ON COLUMN analysis_signals.portfolio_signal_rules.signal_rule_ids IS 'THE ARRAY — ALL the portfolio''s MEMBER rule-row surrogate ids ACROSS SUB TYPES (signal_rules.signal_rule_id, drawn from signal_rule_id_seq): SELECT ... FROM signal_rules r WHERE r.signal_rule_id = ANY(signal_rule_ids) finds this portfolio''s member cells — exactly the cells whose signal_rules.is_in_portfolio is TRUE through this group. Array length = the portfolio''s total member-cell count (the members differ by sub type / trend / regime / action; the orchestrating composites carry FALSE — the group IS the composite). The ids identify ROW EMISSIONS (the rules tier is current-state) and are rewritten with the rules every pipeline run.';
COMMENT ON COLUMN analysis_signals.portfolio_signal_rules.is_valid IS 'The PORTFOLIO group''s GATE VERDICT (2026-10-20, the signal_rules column''s portfolio counterpart): TRUE iff the src data TOGETHER passed the PORTFOLIO GATE (the audited benefit clearing its bar — the hedge type: the max drawdown reduction vs the MEAN leg >= 0pp, judged on the group''s history_portfolio_signal_performance snapshot''s gate digest) AND at least one ORCHESTRATING composite cell''s own is_valid (the dir_ave verdict on the coordination itself). The GATING conjunction for the CONFIDENCE CASCADE: only is_valid groups let their member cells see the cell-level confidence increase (the cascade scale on the portfolio snapshots'' gate digest; the live tier applies it beside the starvation sample scale). FALSE = the group audits below its benefit bar, every orchestration measured out, or it is observational — members keep their OWN cell verdicts (never discounted), just no cascade.';
COMMENT ON COLUMN analysis_signals.portfolio_signal_rules.rule_entry IS 'The engine entries that wrote the group — the ORCHESTRATING RuleEntry subclass names under analysis/analysis_signals/engines/signal_families/, ''|''-joined when a portfolio''s subs were written by more than one entry (the hp_rsi orchestrator + the pair-native sibling); the signal_rules column''s portfolio counterpart (how a group finds its writers).';
COMMENT ON COLUMN analysis_signals.portfolio_signal_rules.rule_params IS 'The CONSTANT PORTFOLIO rule definition (JSONB, the signal_rules rule_params grammar, portfolio-level since the 2026-10-20 cell-level redesign): top-level axis (the portfolio_type label), universe + pair_weighting (verbatim the member cells'' strings), episode_merge_days (the cells'' K), the gate BARS {metric max_dd_reduction_vs_mean, min_pp} and the confidence CASCADE scale {factor, unit_pp, cap} (the signal_rules conventions — the member cells'' increase is 1 + factor x min(benefit_pp / unit_pp, cap), applied ONLY on groups that passed is_valid), the portfolio CONSTRUCTION block {legs [A, B], market (the offset-corr market leg), aim = max_drawdown_reduction (WHAT BENEFIT the portfolio is run for — the audited claim history_portfolio_signal_performance records)}, and the PER-SUB coordination specs under ``subs`` keyed by sub-type label ({axis, response, coordination, observational?} each — the pre-simplification top-level blocks). Engine-generated, never hand-typed. The MEASURED half (the benefit audit + the combined member stats) lives in analysis_signals.history_portfolio_signal_performance as dated daily snapshots.';
