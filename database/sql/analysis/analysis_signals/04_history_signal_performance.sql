-- ============================================================================
--  Table: analysis_signals.history_signal_performance
--
--  history_signal_performance — one row per (rule cell x as-of DATE): the
--  DAILY SNAPSHOT of the cell's OWN MEASURED evidence (2026-10-19 split;
--  slimmed 2026-10-20's cell-level-signals-in-portfolio redesign): the
--  per-cell measure block ONLY — n_episodes, dir_ave_pct, dir_std_pct,
--  t_stat, hit_rate — plus the is_in_portfolio membership flag. The cell's
--  test is the UNIFIED one (the dir_ave bar → signal_rules.is_valid);
--  everything COMBINED (the pooled evidence tier, the gate verdict
--  digest, the ohlcv boost conf, the drawdown lens, the portfolio
--  cascade) moved OFF the cell snapshot 2026-10-20 — the combined audit
--  over a portfolio's member cells lives in
--  history_portfolio_signal_performance (the renamed 05), and the
--  starvation sample scale is recomputed by readers from this table's
--  n_episodes + the rule's gate.sample_floor constant.
--
--  WHY THE SPLIT: signal_rules is CURRENT-STATE (a family's scope is
--  wiped and re-inserted every pipeline run), so everything measured
--  used to be lost with the wipe — a cell's evidence history (how its
--  dir_ave / hit_rate evolved, the day it graduated past the sample
--  floor, the day it measured out and died) was overwritten in place.
--  Since this table the split of duties is:
--
--    - signal_rules records ONLY the CONSTANT rule: the identity PK
--      (code, sec_type, signal_type, signal_sub_type, trend,
--      regime_state, action), the engine entry, and rule_params with
--      the constant items only (axis, episode merge K, the reentry
--      flags, the family's bar_def / params, the gate BAR constants
--      min_pct + sample_floor + the observational ungated designation,
--      the confidence SCALE depth_factor + depth_cap) + the
--      is_in_portfolio membership flag.
--    - THIS table records the MEASURED items, one dated snapshot per
--      daily run — never purged (a --force rebuild re-measures and
--      upserts the run's own as-of date in place; PAST snapshots are
--      the point of the table and survive every wipe).
--
--  Columns (the cell key mirrors signal_rules' PK; the payload is the
--  cell's own measured evidence):
--    - date            the measurement AS-OF date — the prepared frame's
--                      data horizon (max date) the cells were measured
--                      on. A trading day's evening run stamps that day;
--                      idempotent re-runs upsert the same date row.
--    - n_episodes     measure.n_episodes — the cell's episode count.
--    - dir_ave_pct    measure.dir_ave_pct — the sign-aligned blended
--                      dir_ave (0/3/5/10/20d fixed mix), in PERCENT.
--                      This IS the confidence base (the history/live
--                      writers' confidence base_pct) by construction.
--    - dir_std_pct / t_stat / hit_rate — the dispersion pair + hit
--                      rate (nullable: std/t undefined at n < 2).
--    - is_in_portfolio the membership flag mirroring the rule row's
--                      column AT MEASUREMENT (a dated record of which
--                      portfolio groups claimed the cell that day).
--
--  Population (python -m analysis.analysis_signals, daily): every
--  emitted rules row — trading (incl. the starvation-scaled
--  under-floor cells) plus the ungated twins — carries its
--  _performance payload through the engine; run_family peels it and
--  upserts
--  (copy-or-upsert split on the PK; the date column is the fast path).
--  Readers take the LATEST snapshot per cell (ORDER BY date DESC
--  LIMIT 1 lateral — the rules API re-merges the measure block +
--  confidence.base_pct into its response; the live tier reads its
--  confidence base as-of the checked date, so a --date replay uses
--  the measurement known that day, and recomputes the starvation
--  sample scale min(n_episodes / sample_floor, 1) from the snapshot
--  + the rule's gate constant).
--
--  NEVER PURGED: --force deletes the family's signal_rules and
--  history_signals scopes and re-emits them, but this table only ever
--  gains/upserts the run's as-of date — the per-day evidence history
--  of a cell (including its death: a cell that stops emitting simply
--  stops gaining snapshots; its last one is its final measurement).
-- ============================================================================
DROP TABLE IF EXISTS analysis_signals.history_signal_performance CASCADE;

CREATE TABLE analysis_signals.history_signal_performance (
    code            TEXT         NOT NULL,  -- ticker (etf "510050" / index "000300" / stock) — the rule cell's code
    sec_type        SMALLINT     NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock
    signal_type     SMALLINT     NOT NULL,  -- label code (static.label_dim 'signal_type') — the detection family (mirrors signal_rules.signal_type)
    signal_sub_type SMALLINT     NOT NULL,  -- the family config grid code (static.label_dim 'signal_sub_type') — mirrors signal_rules.signal_sub_type
    trend           SMALLINT     NOT NULL,  -- label code (static.label_dim 'trend'): 0 top | 1 bottom | 2 rise | 3 fall | 4 all (POOLED) | 5 unknown — the cell's trend split
    regime_state    SMALLINT     NOT NULL DEFAULT 0,  -- label code (static.label_dim 'regime'): 0 calm / 1 hot / 2 panic / 3 quiet / 4 all (pooled)
    action          SMALLINT     NOT NULL,  -- label code (static.label_dim 'action'): 0 buy | 1 sell | 2 wait — the row's action AT MEASUREMENT (a cell's snapshots show its evidence evolution across dates; wait rows = the ungated twins only since 2026-10-19)

    date            DATE         NOT NULL,  -- the measurement AS-OF date — the prepared frame's data horizon the cells were measured on (one snapshot per measured day per cell; never purged)

    n_episodes      INTEGER      NOT NULL,  -- measure.n_episodes: the cell's episode count at measurement
    dir_ave_pct     FLOAT8       NOT NULL,  -- measure.dir_ave_pct: the sign-aligned blended dir_ave (0/3/5/10/20d mix), PERCENT — the confidence base (base_pct) by construction
    dir_std_pct     FLOAT8,                 -- measure.dir_std_pct: the per-cell std (dispersion; NULL at n < 2 / non-finite)
    t_stat          FLOAT8,                 -- measure.t_stat: the mean / (std / sqrt(n)) t-stat (NULL at n < 2 / zero std)
    hit_rate        FLOAT8,                 -- measure.hit_rate: the favorable-response share of episodes
    is_in_portfolio BOOLEAN      NOT NULL DEFAULT false,  -- the membership flag mirroring signal_rules.is_in_portfolio AT MEASUREMENT (2026-10-20): the cell was claimed by a portfolio group that run — the combined audit over those cells lives in history_portfolio_signal_performance

    created_at      TIMESTAMP    NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_history_signal_performance PRIMARY KEY (code, sec_type, signal_type, signal_sub_type, trend, regime_state, action, date),
    CONSTRAINT chk_hsp_action CHECK (action BETWEEN 0 AND 2),
    CONSTRAINT chk_hsp_trend CHECK (trend BETWEEN 0 AND 5),
    CONSTRAINT chk_hsp_regime CHECK (regime_state BETWEEN 0 AND 4),
    CONSTRAINT chk_hsp_sec_type CHECK (sec_type BETWEEN 0 AND 2)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_signals', 'history_signal_performance', 16);

-- Date-first lookup (the latest-snapshot lateral joins + per-cell
-- time-series reads).
CREATE INDEX IF NOT EXISTS idx_history_signal_performance_date
    ON analysis_signals.history_signal_performance (sec_type, signal_type, date);

SELECT public.validate_pending_checks('analysis_signals');

COMMENT ON TABLE analysis_signals.history_signal_performance IS 'History SIGNAL PERFORMANCE: one row per (rule cell x as-of date) — the DAILY snapshot of the cell''s OWN measured evidence (the 2026-10-19 split: signal_rules keeps only the CONSTANT rule; slimmed 2026-10-20 to the per-cell measure block + the is_in_portfolio flag — the combined tiers moved to history_portfolio_signal_performance). date = the prepared frame''s data horizon at measurement; readers take the latest snapshot per cell (the rules API re-merges the measure / confidence base into its response; the live tier reads its confidence base as-of the checked date and recomputes the starvation sample scale min(n_episodes / sample_floor, 1) from the snapshot + the rule''s gate constant). Populated by python -m analysis.analysis_signals every measured run; --force NEVER purges it — past snapshots are the point.';
COMMENT ON COLUMN analysis_signals.history_signal_performance.date IS 'The measurement AS-OF date — the prepared frame''s data horizon (max date) the cells were measured on. One snapshot per measured day per cell; idempotent re-runs upsert the same date row; the table is never purged (--force re-measures and upserts its own as-of date — past snapshots survive every wipe, so a cell''s evidence evolution, graduation and death are all preserved).';
COMMENT ON COLUMN analysis_signals.history_signal_performance.dir_ave_pct IS 'measure.dir_ave_pct — the sign-aligned blended dir_ave (0/3/5/10/20d fixed mix) in PERCENT. This IS the confidence base (the history writer''s rule_params.confidence.base_pct and the live tier''s measured base) by construction: confidence = dir_ave_pct x (1 + depth_factor x min(depth, depth_cap)) x sample_scale(min(n / floor, 1), recomputed by the reader).';
COMMENT ON COLUMN analysis_signals.history_signal_performance.is_in_portfolio IS 'The membership flag mirroring signal_rules.is_in_portfolio AT MEASUREMENT (2026-10-20): the cell''s rule-row id rode some portfolio_signal_rules.signal_rule_ids array that run — a dated record of which portfolio groups claimed the cell. The combined stats audit over a portfolio''s member cells (the pooled evidence over the members) lives in analysis_signals.history_portfolio_signal_performance.pooled; the cell''s own test stays the unified dir_ave gate (signal_rules.is_valid).';
