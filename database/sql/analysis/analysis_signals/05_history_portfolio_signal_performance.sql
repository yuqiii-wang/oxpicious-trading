-- ============================================================================
--  Table: analysis_signals.history_portfolio_signal_performance
--  (RENAMED from portfolio_signal_performance 2026-10-20's
--  cell-level-signals-in-portfolio redesign — the name now declares its
--  family: the dated, never-purged snapshot tier beside
--  history_signal_performance; the sub_type axis DROPPED the same day:
--  one row per PORTFOLIO (code x portfolio_type) x as-of DATE, and the
--  table's audit widened from the structure-only benefit to the COMBINED
--  STATS OF THE INDIVIDUAL IN-PORTFOLIO SIGNALS).
--
--  HISTORY PORTFOLIO SIGNAL PERFORMANCE — one row per (portfolio x
--  as-of DATE): the DAILY SNAPSHOT of the PORTFOLIO-LEVEL AUDIT over
--  the portfolio's MEMBER CELLS (the cells whose signal_rule_ids ride
--  the group's array — exactly the is_in_portfolio cells):
--
--    - pooled  the COMBINED STATS of the individual in-portfolio
--      signals: the member cells' own measured evidence (their
--      history_signal_performance measures) meta-combined —
--      {n_cells, n_episodes, dir_ave_pct, t_stat, hit_rate}: the
--      member-cell count, the total episode count, the n-weighted
--      mean dir_ave / hit, and the t-stat of the combined moments
--      (per-cell n / dir_ave / dir_std pooled). WHAT THE MEMBERS
--      TOGETHER claim — the tier's answer to "how do the individual
--      signals in this portfolio test as one population" (each cell
--      keeps its OWN unified dir_ave test; this block never gates a
--      cell — the portfolio tier only ever records and cascades).
--    - benefit  the type-specific STRUCTURE audit payload (unchanged
--      vocabulary): WHAT THE PORTFOLIO STRUCTURE took that its
--      standalone components would not have. The benefit vocabulary
--      is per portfolio_type (the benefit JSONB's ``kind`` names it;
--      one type claims ONE primary benefit):
--
--        neg_corr_hedge_pair (0) — kind "max_drawdown_reduction": the
--          inverse-vol long–long pair's DRAWDOWN benefit vs its legs
--          standalone. The block:
--            window              "full_history" — the gauge's span
--                                (the prepared pair frame's whole
--                                history).
--            portfolio_pct       the pair PORTFOLIO value path's own
--                                max drawdown (pct, <= 0; running-max
--                                trough over the frame, per pair
--                                code).
--            leg_a_pct / leg_b_pct / worst_leg_pct / mean_leg_pct
--                                the same gauge on each LEG's
--                                standalone composite close (px_a /
--                                px_b) — what an undiversified holder
--                                of that leg ate.
--            reduction_vs_worst_pct / reduction_vs_mean_pct
--                                the BENEFIT TAKEN: how many
--                                percentage POINTS shallower the
--                                portfolio's max drawdown sits than
--                                the worst / mean leg's (drawdowns
--                                are negative pcts, so the depth
--                                reduction = portfolio − leg in
--                                signed space; >= 0 = the
--                                coordination paid — the negative
--                                covariance cancellation is real).
--
--    - gate    the PORTFOLIO GATE VERDICT digest: {metric, min_pp,
--              passed, conf_scale} — the audited benefit vs its bar,
--              with conf_scale = the CONFIDENCE CASCADE scale (1 +
--              factor x min(benefit/unit, cap); 1.0 when the gate
--              failed — the increase the group's member cells take,
--              gated by portfolio_signal_rules.is_valid; the live
--              tier applies it beside the starvation sample scale).
--              The bars ride the snapshot so a later config change
--              never rewrites what an old snapshot was judged by.
--    - dd      the portfolio's COORDINATED-EVENT drawdown lens
--              {min20_pct, p10_pct, baseline_pct, n} — the
--              ORCHESTRATING cells' own dd lenses combined at the
--              portfolio level (n-weighted mean of the mean/p10
--              troughs, the shallowest baseline, the total trigger
--              count; NULL where no orchestrating cell measured a
--              lens).
--
--  WHY A SEPARATE TABLE (not columns on portfolio_signal_rules): the
--  rules table is CURRENT-STATE (wiped + rewritten every run); the
--  audit is MEASURED and dated — the drawdown benefit and the members'
--  combined evidence evolve as the pair's history grows, and their
--  history (the day the hedge stopped paying, the day the reduction
--  widened) is the point. Same split, same lifecycle, as signal_rules
--  vs history_signal_performance: upserted on the PK + date, NEVER
--  purged (--force included).
--
--  Population (python -m analysis.analysis_signals, the portfolio
--  families — hedge_pairs today): one snapshot per emitted PORTFOLIO
--  per run (every code the run rewrote in portfolio_signal_rules);
--  readers take the LATEST snapshot per portfolio (the portfolio API
--  laterally joins it into its response — the UI renders the
--  drawdown-reduction benefit + the members' combined evidence).
--
--  Not hash-partitioned (the portfolio_signal_rules precedent): a
--  small daily-audit table (tens of portfolios per sec_type per day),
--  PK-addressed reads only.
-- ============================================================================
DROP TABLE IF EXISTS analysis_signals.portfolio_signal_performance CASCADE;
DROP TABLE IF EXISTS analysis_signals.history_portfolio_signal_performance CASCADE;

CREATE TABLE analysis_signals.history_portfolio_signal_performance (
    code            TEXT         NOT NULL,  -- the PORTFOLIO code — the composite 'A~B' pair id (mirrors portfolio_signal_rules.code)
    sec_type        SMALLINT     NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock — the pipeline run's sec_type scope
    portfolio_type  SMALLINT     NOT NULL,  -- label code (static.label_dim 'portfolio_type'): 0 neg_corr_hedge_pair — mirrors portfolio_signal_rules.portfolio_type

    date            DATE         NOT NULL,  -- the measurement AS-OF date — the prepared frame's data horizon the audit was measured through (one snapshot per measured day per portfolio; never purged)
    pooled          JSONB,                  -- the COMBINED STATS of the individual in-portfolio signals: the member cells' own measured evidence meta-combined — {n_cells, n_episodes, dir_ave_pct, t_stat, hit_rate} (the n-weighted mean dir_ave/hit + the combined-moments t over the members' n/dir_ave/dir_std) — how the portfolio's member cells test AS ONE POPULATION; never a cell gate
    benefit         JSONB        NOT NULL,  -- the type-specific PORTFOLIO BENEFIT audit payload: {kind, max_dd {window, portfolio_pct, leg_a_pct, leg_b_pct, worst_leg_pct, mean_leg_pct, reduction_vs_worst_pct, reduction_vs_mean_pct}} for the hedge type — WHAT THE PORTFOLIO STRUCTURE took (the coordination's claim, recorded as evidence)
    gate            JSONB        NOT NULL,  -- the PORTFOLIO GATE VERDICT digest: {metric, min_pp, passed, conf_scale} — the audited benefit vs its bar; conf_scale = the CONFIDENCE CASCADE scale the passing group's member cells take (1.0 when failed — never a discount); the bars ride the snapshot so config changes never rewrite history
    dd              JSONB,                  -- the portfolio's COORDINATED-EVENT drawdown lens {min20_pct, p10_pct, baseline_pct, n}: the orchestrating cells' own dd lenses combined at the portfolio level (n-weighted trough means, shallowest baseline, total n; NULL where none measured a lens)

    created_at      TIMESTAMP    NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_history_portfolio_signal_performance PRIMARY KEY (code, portfolio_type, date),
    CONSTRAINT chk_hpsp_sec_type CHECK (sec_type BETWEEN 0 AND 2)
);

-- Date-first lookup (the latest-snapshot lateral joins).
CREATE INDEX IF NOT EXISTS idx_history_portfolio_signal_performance_date
    ON analysis_signals.history_portfolio_signal_performance (sec_type, portfolio_type, date);

SELECT public.validate_pending_checks('analysis_signals');

COMMENT ON TABLE analysis_signals.history_portfolio_signal_performance IS 'History PORTFOLIO SIGNAL PERFORMANCE (renamed from portfolio_signal_performance 2026-10-20): one row per (portfolio x as-of date) — the DAILY snapshot of the PORTFOLIO-LEVEL AUDIT over the group''s MEMBER CELLS (the is_in_portfolio cells whose ids ride the portfolio_signal_rules array): ``pooled`` = the COMBINED STATS of the individual in-portfolio signals (their own measures meta-combined — how the members test as one population), ``benefit`` = the type-specific STRUCTURE claim (the neg_corr_hedge_pair: max drawdown reduction — the pair value''s own max drawdown vs each leg''s standalone), ``gate`` = the portfolio gate verdict with conf_scale = the member CONFIDENCE CASCADE scale, ``dd`` = the orchestrating cells'' coordinated-event forward-trough lens combined. The portfolio counterpart of history_signal_performance (per-cell evidence); upserted on the PK + date and NEVER purged — a --force rebuild re-measures and upserts its own as-of date (past snapshots are the point). Populated by python -m analysis.analysis_signals (the portfolio families) in the same run that rewrites portfolio_signal_rules.';
COMMENT ON COLUMN analysis_signals.history_portfolio_signal_performance.date IS 'The measurement AS-OF date — the prepared frame''s data horizon the audit was measured through (identical convention to history_signal_performance.date). One snapshot per measured day per portfolio; idempotent re-runs upsert the same date row; never purged.';
COMMENT ON COLUMN analysis_signals.history_portfolio_signal_performance.pooled IS 'The COMBINED STATS of the individual in-portfolio signals (2026-10-20): the member cells'' own measured evidence (their history_signal_performance measures) meta-combined — {n_cells (the member-cell count), n_episodes (total episodes), dir_ave_pct (the n-weighted mean favorable response), t_stat (the combined-moments t over the members'' n / dir_ave / dir_std), hit_rate (the n-weighted mean hit)}. Each member cell keeps its OWN unified dir_ave test (signal_rules.is_valid); this block is the portfolio tier''s combined RECORD — never a cell gate. NULL on portfolios whose members measured nothing that run.';
COMMENT ON COLUMN analysis_signals.history_portfolio_signal_performance.gate IS 'The PORTFOLIO GATE VERDICT digest: {metric (max_dd_reduction_vs_mean for the hedge type), min_pp, passed, conf_scale} — the audited drawdown-reduction benefit vs its bar, denormalized into portfolio_signal_rules.is_valid TOGETHER WITH at least one orchestrating cell''s own verdict (the gating conjunction for the member confidence cascade). conf_scale = the CASCADE scale the passing group''s member cells take as a confidence INCREASE — 1 + factor x min(benefit_pp / unit_pp, cap), the cell depth-scale formula on the benefit depth; 1.0 when the gate failed (the portfolio tier never discounts a member''s confidence). The live tier reads this scale for is_in_portfolio cells whose group is_valid, beside the starvation sample scale. The bars ride the snapshot so a later config change never rewrites what an old snapshot was judged by.';
COMMENT ON COLUMN analysis_signals.history_portfolio_signal_performance.dd IS 'The portfolio''s COORDINATED-EVENT drawdown lens {min20_pct, p10_pct, baseline_pct, n}: the ORCHESTRATING cells'' own dd lenses (their replayed trigger days'' forward worst-trough within 20d vs the response path''s all-days baseline) combined at the portfolio level — the n-weighted mean of the mean/p10 troughs, the shallowest baseline, the total trigger count. NULL where no orchestrating cell measured a lens.';
COMMENT ON COLUMN analysis_signals.history_portfolio_signal_performance.benefit IS 'The type-specific PORTFOLIO BENEFIT audit payload (JSONB; ``kind`` names the vocabulary, one primary benefit per portfolio_type). neg_corr_hedge_pair: kind "max_drawdown_reduction" — max_dd {window, portfolio_pct (the inverse-vol pair value''s own max drawdown), leg_a_pct / leg_b_pct / worst_leg_pct / mean_leg_pct (the same gauge on each leg''s standalone close), reduction_vs_worst_pct / reduction_vs_mean_pct (the BENEFIT TAKEN: percentage points shallower the portfolio''s drawdown sits than the worst / mean leg''s — drawdowns are negative pcts, so the depth reduction = portfolio − leg in signed space; >= 0 = the coordination paid)}.';
