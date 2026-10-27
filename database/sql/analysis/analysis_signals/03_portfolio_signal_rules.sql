-- ============================================================================
--  Table: analysis_signals.portfolio_signal_rules  (REFACTORED 2026-10-18:
--  per-sub-type GROUPS with an ids ARRAY; convention-aligned to
--  signal_rules 2026-10-20; SIMPLIFIED the same day's cell-level redesign:
--  the signal_sub_type axis DROPPED — ONE ROW PER (code, portfolio_type),
--  the whole portfolio's member cells in the single ids ARRAY, the
--  per-sub-type coordination specs nested under rule_params.subs;
--  SPLIT 2026-10-25: the portfolio_sub_type axis — ONE ROW PER (code,
--  portfolio_type, portfolio_sub_type), the pair's TWO portfolio rules
--  (trio wash + spread) SEPARATE rows, the registered mov_rsi cells
--  mapped INTO them through signal_rule_ids only).
--
--  PORTFOLIO SIGNAL RULES — the PORTFOLIO tier over signal_rules: a
--  portfolio is a code whose CELL-LEVEL RULE CELLS act as ONE managed
--  group (the negative-corr hedge pair precedent — the composite 'A~B'
--  long–long pair portfolio). ONE ROW PER (code, portfolio_type,
--  portfolio_sub_type):
--
--    - code               the portfolio's identity + SEARCH KEY (the
--      pick-a-portfolio lookup starts here); 'A~B' lexicographic for
--      the pair types.
--    - portfolio_type     WHAT THE CODE IS as a portfolio — its own
--      static.label_dim domain (NOT the signal family; 0 =
--      neg_corr_hedge_pair, the two-leg long–long negatively-
--      correlated hedge pair).
--    - portfolio_sub_type WHICH PORTFOLIO RULE the row is within its
--      portfolio_type — its own static.label_dim domain (2026-10-25,
--      user-directed "migrate trio wash and spread to as
--      portfolio_sub_type"): 0 trio_wash (the hp_entry_trio
--      orchestration), 1 spread (the hp_entry_spread + hp_exit_spread
--      orchestrations). Selected by the row's own orchestrating
--      signal_sub_type; the identity axis that split the merged row —
--      a pair running BOTH rules carries TWO rows, each with ITS OWN
--      ids array, per-sub coordination spec and audit.
--    - signal_rule_ids    BIGINT[] — THE ARRAY. THIS portfolio rule's
--      member rule-row surrogate ids: the sub's OWN orchestrating
--      cells PLUS the MAPPED REGISTERED CELLS — real standalone-family
--      rule rows (mov_rsi rsi{w}_{p}pct at the component codes) that
--      are NEVER portfolio rows themselves: they live in signal_rules
--      under the is_in_portfolio TRUE tier (a SEPARATE row from any
--      standalone FALSE-tier twin — is_in_portfolio is a PK member of
--      signal_rules), and the portfolio references them ONLY through
--      this array (user-directed 2026-10-25: "rsi signals themselves
--      should not sit in portfolio signals — by portfolio
--      signal_rule_ids to map them"). Mapping rule (hedge_pairs): the
--      shared MARKET cells ride every pair's trio_wash row only; a
--      LEG cell rides its own pair's trio_wash AND spread rows (the
--      legs witness both portfolio rules).
--      SELECT ... FROM signal_rules r WHERE r.signal_rule_id =
--      ANY(p.signal_rule_ids) finds this portfolio rule's cells.
--    - is_valid           the PORTFOLIO group's GATE VERDICT
--      (2026-10-20): TRUE iff the src data TOGETHER passed the
--      PORTFOLIO GATE (the audited benefit clearing its bar — the
--      hedge type: the max drawdown reduction vs the MEAN leg >= 0pp)
--      AND at least one ORCHESTRATING composite cell's own is_valid
--      (the dir_ave verdict on the coordination itself). THE GATING
--      conjunction for the member BENEFIT BOOST: only a group with
--      is_valid TRUE lets its member cells see the cell-level
--      confidence increase (the benefit-boost scale on the
--      history_portfolio_signal_performance snapshots' gate digest,
--      applied by the live tier beside the starvation sample scale).
--      FALSE = the group audits below its benefit bar or every
--      orchestration measured out / is observational — members keep
--      their OWN cell verdicts (never discounted), just no boost.
--    - rule_entry         the engine entries that wrote the group (the
--      signal_rules column's portfolio counterpart) — the
--      ORCHESTRATING RuleEntry subclass names under
--      analysis/analysis_signals/engines/signal_families/, '|'-
--      joined (the hedge family's subs all ride the ONE
--      HedgePairTrioEntry since 2026-10-24).
--    - rule_params        JSONB — the CONSTANT PORTFOLIO rule
--      definition in the signal_rules rule_params GRAMMAR (portfolio-
--      level since the 2026-10-20 simplification): the shared
--      top-level keys carry the SAME values the member cells' params
--      carry (universe / pair_weighting / episode_merge_days / the
--      gate bars / the member BENEFIT BOOST scale), the portfolio tier's
--      own CONSTRUCTION block, and the PER-SUB coordination specs
--      nested under ``subs`` keyed by sub-type label (ONE sub per row
--      since the split — the pre-split rows carried both):
--        axis               the portfolio_type label (signal_rules
--                           carries the family label).
--        name               the portfolio rule's HUMAN NAME (2026-10-25,
--                           user-directed "rule param json should have a
--                           name, e.g., trio wash") — "trio wash" /
--                           "spread"; display vocabulary, not a key (the
--                           identity stays (code, portfolio_type,
--                           portfolio_sub_type)).
--        portfolio_sub_type the rule's own portfolio_sub_type LABEL WORD
--                           (2026-10-25, user-directed "in rule params
--                           json should have all") — the JSON mirrors the
--                           column so the rule definition is
--                           self-describing.
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
--        confidence         the member BENEFIT BOOST scale {factor,
--                           unit_pp, cap} — the signal_rules confidence
--                           convention applied to the benefit depth: the member
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
--                           condition, coordination, cell_signals,
--                           observational?}} — the
--                           per-sub-type coordination specs (ONE key
--                           per row since the 2026-10-25 split —
--                           hp_entry_trio on the trio_wash row,
--                           hp_entry_spread + hp_exit_spread both on
--                           the spread row): axis
--                           "<portfolio_type>:<sub>", response (the
--                           sub's response axis — inverse_vol_pair_
--                           portfolio / spread_long_short /
--                           laggard_leg / strong_leg),
--                           coordination (kind-first the bar_def
--                           convention; kind reuses the member cells'
--                           bar_def.kind, the param keys the family
--                           doc's sub_signals params vocabulary —
--                           the trio carries witnesses + tail_pcts +
--                           rsi_windows, the window x tail candidate
--                           grid the measured gate self-picks from),
--                           observational TRUE on the ungated subs.
--                           condition (2026-10-25, user-directed "add
--                           Rule condition in Rule params ... all UI
--                           data must be sourced from DB") — the sub's
--                           STORED plain-language RULE CONDITION line,
--                           engine-written; the UI parses it verbatim
--                           instead of composing prose from the spec
--                           fragments. THE HARD HOOK (2026-10-26,
--                           user-directed "rule condition text is
--                           concat from cell signal names and
--                           actions" — the texts had drifted from the
--                           registered cells by reciting the
--                           candidate grids): every condition text is
--                           BUILT FROM the actual emitted rows — the
--                           sub line from the row's own winner
--                           bar_def, a witness record's condition
--                           from its mapped registered cells' own
--                           (window, tail) vocabulary — never from
--                           the config grids (those stay in the
--                           coordination block). cell_signals — the
--                           sub's
--                           CELL-SIGNAL condition records as ONE FLAT
--                           array [{sub_type, code, role, condition,
--                           bar, action, mapped_signals}, ...]: the
--                           trio's
--                           THREE witness curves (market + both legs —
--                           2026-10-26, user-directed "there are
--                           RSI(10) · 3 witnesses vs RSI(14) · 3
--                           witnesses, remove them": the per-RSI-window
--                           keyed duplication is gone, each curve ONE
--                           window-free record; the mapped registered
--                           cells classify by window in the UI), the
--                           spread subs' two role legs each (payload
--                           conditions, NO rule rows behind them).
--                           action (2026-10-26, user-directed "since
--                           cell signal carry action, should also
--                           show action on UI", refined the same day
--                           "the portfolio level should not have
--                           action") — the record's OWN ACTION label
--                           word (buy/sell): the trio's witnesses are
--                           all buy-side washes, the spread subs' legs
--                           split buy/sell by role (the exit flips
--                           both). Rendered NOWHERE at the portfolio
--                           level (the witness records are rule-params
--                           conditions — the portfolio has no action);
--                           the key resolves the witness's mapped
--                           cells ACTION-SIDE ALIGNED in the UI
--                           (mapped_signals names the sub type only,
--                           which mov_rsi registers on BOTH sides —
--                           the registered rows match on their own
--                           action, so no sell twin leaks under a buy
--                           witness) and chips the registered cell
--                           rows' own action in the cell-signals
--                           table.
--                           mapped_signals (2026-10-25, user-directed
--                           "migrate rsi·witness ... from DB signals to
--                           rule params json ... ensure mapping, ok to
--                           duplicate the mappings") —
--                           the registered mov_rsi sub-type LABELS
--                           this rule maps at the witness's OWN curve,
--                           action-side aligned (the trio's witnesses
--                           all buy-side; the spread's legs split by
--                           role); duplication across witnesses/rules
--                           is fine — every witness carries its own
--                           list. The MAPPED registered cells
--                           (signal_rule_ids) are NOT cell-signal
--                           payload records — the UI retrieves them
--                           from signal_rules through the ids array
--                           behind the row's expand (2026-10-25,
--                           user-directed "onclick to expand").
--
--  Deliberately NO trend / regime / action columns (the tier's PK has
--  no cell axes — a group holds its cells across them), NO cell-level
--  signal_sub_type column (the members' own signal_rules.signal_sub_type
--  carries it; the per-sub specs live in rule_params.subs), and no cell-level
--  gate bars (the members' own gates govern their emission; the
--  portfolio tier's verdict axis is the BENEFIT audit in
--  history_portfolio_signal_performance, denormalized here as
--  is_valid).
--
--  No DB FK to signal_rules (hash-partitioned: no UNIQUE over the id
--  alone, hence no FK target) — the WRITER owns referential
--  integrity: run.py stamps fresh ids on the rule rows and rewrites
--  The ids identify rule ROW EMISSIONS (the rules tier is current-state — a family's scope is
--  wiped and re-inserted every pipeline run), so both tables churn
--  together and are always read as a pair.
--
--  (The former PORTFOLIO knowledge document table,
--  static.portfolio_signal_docs, was DROPPED 2026-10-27 — nothing
--  ever read the joined document; the type's claims live in
--  history_portfolio_signal_performance.benefit.)
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
    code               TEXT         NOT NULL,  -- the PORTFOLIO code — the identity + search key (the pair types' composite 'A~B', lexicographic)
    sec_type           SMALLINT     NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock — the owning rule rows' stamp (the pipeline run's sec_type)
    portfolio_type     SMALLINT     NOT NULL,  -- label code (static.label_dim 'portfolio_type'): 0 neg_corr_hedge_pair — WHAT the code is as a portfolio (replaces the per-rule shape's signal_type discriminator)
    portfolio_sub_type SMALLINT     NOT NULL,  -- label code (static.label_dim 'portfolio_sub_type'): 0 trio_wash | 1 spread — WHICH PORTFOLIO RULE the row is within its portfolio_type (2026-10-25 split: a pair running both rules carries TWO rows)
    signal_rule_ids    BIGINT[]     NOT NULL,  -- THE ARRAY: THIS portfolio rule's member rule-row surrogate ids — the sub's OWN orchestrating cells PLUS the MAPPED REGISTERED CELLS (mov_rsi rows at the component codes, is_in_portfolio TRUE; never portfolio rows themselves — the portfolio references them ONLY through this array) — = ANY(this) finds the rule's cells
    is_valid           BOOLEAN      NOT NULL DEFAULT false,  -- the PORTFOLIO group's GATE VERDICT (2026-10-20): TRUE iff the audited benefit cleared the portfolio gate AND at least one orchestrating composite cell's own verdict holds — the GATING conjunction for the member BENEFIT BOOST (members see the increase only on is_valid groups; their own verdicts are never discounted)
    rule_entry         TEXT         NOT NULL,  -- the engine entries that wrote the group (the orchestrating RuleEntry subclass names under analysis/analysis_signals/engines/signal_families/, '|'-joined) — the signal_rules column's portfolio counterpart
    rule_params        JSONB        NOT NULL DEFAULT '{}',  -- the CONSTANT PORTFOLIO rule definition in the signal_rules rule_params GRAMMAR (portfolio-level: top-level axis / universe / pair_weighting / episode_merge_days + the gate bars + the member BENEFIT BOOST scale + the portfolio {legs, market, aim} construction + the PER-SUB coordination spec under subs — ONE sub per row since the split) — engine-generated; the MEASURED benefit audit lives in history_portfolio_signal_performance (05)

    created_at      TIMESTAMP    NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_portfolio_signal_rules PRIMARY KEY (code, portfolio_type, portfolio_sub_type),
    CONSTRAINT chk_portfolio_signal_rules_sec_type CHECK (sec_type BETWEEN 0 AND 2),
    CONSTRAINT chk_portfolio_signal_rules_ids_not_empty CHECK (cardinality(signal_rule_ids) >= 1)
);

-- Type-first lookup (a portfolio type's registered groups).
CREATE INDEX IF NOT EXISTS idx_portfolio_signal_rules_type
    ON analysis_signals.portfolio_signal_rules (sec_type, portfolio_type,
                                                portfolio_sub_type);

COMMENT ON TABLE analysis_signals.portfolio_signal_rules IS 'Portfolio signal rules — the PORTFOLIO tier over signal_rules (refactored 2026-10-18; convention-aligned to signal_rules'' column order + rule_params grammar 2026-10-20; SPLIT 2026-10-25 — the portfolio_sub_type axis): ONE ROW PER (code, portfolio_type, portfolio_sub_type) whose signal_rule_ids BIGINT[] holds THIS portfolio rule''s member rule-row surrogate ids — the sub''s OWN orchestrating cells PLUS the MAPPED REGISTERED CELLS (mov_rsi rows at the component codes riding the is_in_portfolio TRUE tier; never portfolio rows themselves — the portfolio references them ONLY through the array; a registered cell is a SEPARATE signal_rules row from any standalone twin because is_in_portfolio is a PK member there) — = ANY(signal_rule_ids) finds the rule''s cells — and whose rule_params JSONB carries the CONSTANT portfolio construction in the signal_rules grammar (top-level axis / universe / pair_weighting / episode_merge_days + the gate bars + the member BENEFIT BOOST scale + the portfolio {legs, market, aim} block + the per-sub coordination spec under subs — ONE sub per row since the split; the MEASURED benefit audit lives in history_portfolio_signal_performance). is_valid = the audited benefit cleared the portfolio gate AND at least one orchestrating cell''s own verdict — the GATING conjunction: only is_valid groups let their member cells see the cell-level confidence increase (the benefit-boost scale on the history_portfolio_signal_performance snapshots'' gate digest). code is the identity + search key; portfolio_type is the portfolio tier''s own vocabulary (0 neg_corr_hedge_pair — NOT the signal family); portfolio_sub_type names the PORTFOLIO RULE within it (0 trio_wash, 1 spread — the identity axis that split the merged per-pair row). Populated by python -m analysis.analysis_signals in the same run that stamps the rule ids (both scopes wiped + rewritten together — no DB FK by partition-design, the writer owns integrity).';
COMMENT ON COLUMN analysis_signals.portfolio_signal_rules.code IS 'The PORTFOLIO code — the identity + SEARCH KEY. The pair types: the composite ''A~B'' id (lexicographic, mirroring the offsets table''s dedup) — the long–long pair portfolio the family''s rule group trades.';
COMMENT ON COLUMN analysis_signals.portfolio_signal_rules.sec_type IS 'The owning rule rows'' sec_type stamp as its static.label_dim (''sec_type'') SMALLINT code (0=etf 1=index 2=stock) — the pipeline run''s sec_type scope (hedge_pairs runs the default index scope).';
COMMENT ON COLUMN analysis_signals.portfolio_signal_rules.portfolio_type IS 'WHAT the code IS as a portfolio, as its static.label_dim (''portfolio_type'') SMALLINT code — the portfolio tier''s OWN vocabulary, deliberately NOT the signal family (a portfolio type can outlive family renames; one type may group rules from several families). 0 = neg_corr_hedge_pair, the two-leg long–long negatively-correlated hedge pair. Replaces the earlier per-rule shape''s signal_type discriminator.';
COMMENT ON COLUMN analysis_signals.portfolio_signal_rules.portfolio_sub_type IS 'WHICH PORTFOLIO RULE the row is within its portfolio_type, as its static.label_dim (''portfolio_sub_type'') SMALLINT code (2026-10-25, user-directed "add portfolio_sub_type ... migrate trio wash and spread to as portfolio_sub_type") — the identity axis that SPLIT the merged per-pair row: 0 trio_wash (the hp_entry_trio orchestration — all three curves in their own RSI capitulation tails), 1 spread (the hp_entry_spread + hp_exit_spread orchestrations — the long-short spread''s RSI bottom/top tail). Selected by the row''s own orchestrating signal_sub_type; a pair running BOTH rules carries TWO rows, each with its own ids array, per-sub coordination spec and audit snapshot.';
COMMENT ON COLUMN analysis_signals.portfolio_signal_rules.signal_rule_ids IS 'THE ARRAY — THIS portfolio rule''s member rule-row surrogate ids (signal_rules.signal_rule_id, drawn from signal_rule_id_seq): the sub''s OWN orchestrating cells PLUS the MAPPED REGISTERED CELLS — real standalone-family rule rows (mov_rsi rsi{w}_{p}pct at the market + leg codes, is_in_portfolio TRUE) that are NEVER portfolio rows themselves (2026-10-25, user-directed: rsi signals map by signal_rule_ids, they do not sit IN the portfolio signals; the TRUE-tier row is a SEPARATE signal_rules row from any standalone FALSE-tier twin — is_in_portfolio is a PK member there). Mapping rule (hedge_pairs): the shared MARKET cells ride every pair''s trio_wash row only (the spread disregards the benchmark); a LEG cell rides its own pair''s trio_wash AND spread rows (the legs witness both). SELECT ... FROM signal_rules r WHERE r.signal_rule_id = ANY(signal_rule_ids) finds the rule''s cells. The ids identify ROW EMISSIONS (the rules tier is current-state) and are rewritten with the rules every pipeline run.';
COMMENT ON COLUMN analysis_signals.portfolio_signal_rules.is_valid IS 'The PORTFOLIO group''s GATE VERDICT (2026-10-20, the signal_rules column''s portfolio counterpart): TRUE iff the src data TOGETHER passed the PORTFOLIO GATE (the audited benefit clearing its bar — the hedge type: the max drawdown reduction vs the MEAN leg >= 0pp, judged on the group''s history_portfolio_signal_performance snapshot''s gate digest) AND at least one ORCHESTRATING composite cell''s own is_valid (the dir_ave verdict on the coordination itself). The GATING conjunction for the member BENEFIT BOOST: only is_valid groups let their member cells see the cell-level confidence increase (the benefit-boost scale on the portfolio snapshots'' gate digest; the live tier applies it beside the starvation sample scale). FALSE = the group audits below its benefit bar, every orchestration measured out, or it is observational — members keep their OWN cell verdicts (never discounted), just no boost.';
COMMENT ON COLUMN analysis_signals.portfolio_signal_rules.rule_entry IS 'The engine entries that wrote the group — the ORCHESTRATING RuleEntry subclass names under analysis/analysis_signals/engines/signal_families/, ''|''-joined when a portfolio''s subs were written by more than one entry (the hp_rsi orchestrator + the pair-native sibling); the signal_rules column''s portfolio counterpart (how a group finds its writers).';
COMMENT ON COLUMN analysis_signals.portfolio_signal_rules.rule_params IS 'The CONSTANT PORTFOLIO rule definition (JSONB, the signal_rules rule_params grammar, portfolio-level since the 2026-10-20 cell-level redesign): top-level name (the portfolio rule''s human name — "trio wash" / "spread", 2026-10-25) + axis (the portfolio_type label), universe + pair_weighting (verbatim the member cells'' strings), episode_merge_days (the cells'' K), the gate BARS {metric max_dd_reduction_vs_mean, min_pp} and the member BENEFIT BOOST scale {factor, unit_pp, cap} (the signal_rules conventions — the member cells'' increase is 1 + factor x min(benefit_pp / unit_pp, cap), applied ONLY on groups that passed is_valid), the portfolio CONSTRUCTION block {legs [A, B], market (the offset-corr market leg), aim = max_drawdown_reduction (WHAT BENEFIT the portfolio is run for — the audited claim history_portfolio_signal_performance records)}, and the PER-SUB coordination specs under ``subs`` keyed by sub-type label — ONE sub per row since the 2026-10-25 split (hp_entry_trio on the trio_wash row; hp_entry_spread + hp_exit_spread both on the spread row). Engine-generated, never hand-typed. The MEASURED half (the benefit audit + the combined member stats) lives in analysis_signals.history_portfolio_signal_performance as dated daily snapshots.';
