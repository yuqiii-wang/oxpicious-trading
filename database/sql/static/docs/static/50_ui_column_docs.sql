-- ============================================================================
--  static.docs seed — table-column info docs (the ExpandedTable `info`
--  marks of the signal / portfolio panels).
-- ============================================================================

DELETE FROM static.docs WHERE doc_id LIKE 'ui.signal-rules.col.%';
DELETE FROM static.docs WHERE doc_id LIKE 'ui.signal-history.col.%';
DELETE FROM static.docs WHERE doc_id LIKE 'ui.portfolio-rules.col.%';
DELETE FROM static.docs WHERE doc_id LIKE 'ui.portfolio-history.col.%';

INSERT INTO static.docs (doc_id, title, intro, series, notes, ordinal) VALUES
(
  'ui.signal-rules.col.valid', 'Valid',
  'The per-cell gate verdict (is_valid — the latest performance snapshot''s gate.passed): true iff the cell''s measured future returns cleared its side''s dir_ave bar at the sample floor (buy ≥ 0.25% @ n ≥ 3 / sell ≥ 0% @ n ≥ 2). False = observational (the wait watchlist / the ungated twin) — negative future returns are discouraged: never valid, never trades, never fires live',
  NULL, NULL, 0
),
(
  'ui.signal-rules.col.bar', 'Bar',
  'The cell''s CONSTANT breach bar definition (rule_params.bar_def) — the engine picked it per (code, sub, trend, regime) from the candidate grid on the code''s own history',
  NULL, NULL, 0
),
(
  'ui.signal-rules.col.evidence', 'Evidence',
  'The cell''s measured evidence on its trigger days (the latest performance snapshot): episodes, the sign-aligned blended dir_ave (0/3/5/10/20d fixed mix) and the hit rate — expand the row for the full measured block (σ · t · base · the recomputed starvation haircut · as-of)',
  NULL, NULL, 0
),
(
  'ui.signal-history.col.pe-boosted', 'PE boosted',
  'The PE-conditioning variant: TRUE = the PE-gated population (the candidate masks AND-gated by the code''s own PE-percentile condition — the rebound family emits both variants); FALSE = the plain population',
  NULL, NULL, 0
),
(
  'ui.signal-history.col.ohlcv-boosted', 'OHLCV boosted',
  'The retired OHLCV-boost variant marker (the boost twins were retired 2026-10-16 — their tuned confidence lives in the rules panel''s performance snapshot): FALSE on every row; kept for the record''s identity',
  NULL, NULL, 0
),
(
  'ui.signal-history.col.reference', 'Reference',
  'The crossed cell bar — the firing rule''s prepared rule_params bar, denormalized per event (history_signals.signal_reference)',
  NULL, NULL, 0
),
(
  'ui.signal-history.col.excess', 'Excess',
  'value − reference: the signed breach depth past the cell''s own bar (buy breaches below → negative)',
  NULL, NULL, 0
),
(
  'ui.signal-history.col.confidence', 'Confidence',
  'The cell''s measured base (the sign-aligned blended dir_ave its rule shipped) amplified over the breach depth — basis points / 100, shown as a %',
  NULL, NULL, 0
),
(
  'ui.signal-history.col.regime', 'regime',
  'The breach day''s market regime (stats.market_regimes: calm / hot / panic / quiet; pool = the regime-free families'' stamp) — recorded context, never a gate',
  NULL, NULL, 0
),
(
  'ui.portfolio-rules.col.triggers', 'Trigger conditions',
  'when the portfolio rule''s coordinated signals fire — one line per its own sub rule; the construction, DD audit and cell signals expand on click',
  NULL, NULL, 0
),
(
  'ui.portfolio-history.col.type', 'Portfolio signal type',
  'The coordinated event''s SIGNAL TYPE — the orchestrating family''s word (hp_rsi "Hedge Pairs RSI" = the sourced composite orchestration, hedge_pairs = pair-native); the coordinated sub rule sits in the next column, the event''s CELL SIGNALS under the row in the expansion',
  NULL, NULL, 0
),
(
  'ui.portfolio-history.col.sub-type', 'Portfolio signal sub type',
  'The coordinated event''s SIGNAL SUB TYPE — the coordinated sub rule''s word; the raw literal rides the chip with the portfolio kind and event class; the event''s coordinated CELL SIGNALS sit under the row in the expansion',
  NULL, NULL, 0
),
(
  'ui.portfolio-history.col.members', 'Cell signals',
  'THIS portfolio signal''s coordinated CELL SIGNALS — the sourced component cells'' and the legs'' witnesses'' own records (each on its OWN day) plus the pair response legs on the event day; a portfolio event records >= 2 actions on >= 2 codes (different dates allowed) — expand the row for them',
  NULL, NULL, 0
),
(
  'ui.portfolio-history.col.confidence', 'Confidence',
  'The portfolio signal''s scaled expected-move confidence (basis points / 100, shown as a %); per-member values in the expansion',
  NULL, NULL, 0
),
(
  'ui.portfolio-history.col.regime', 'regime',
  'The event day''s market regime (stats.market_regimes: calm / hot / panic / quiet; pool = the regime-free families'' stamp) — recorded context, never a gate',
  NULL, NULL, 0
);
