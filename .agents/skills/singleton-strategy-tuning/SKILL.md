---
name: singleton-strategy-tuning
description: >
  The Singleton Strategy's TRAINING model logic (Train Model =
  strategy.factors_and_algos._optm_engine — NestedTrainer 5 steps,
  Omega/Calmar regime losses, Kelly sizing, algo_configs trained rows)
  and the CAUTION considerations for parametrizing the trained configs
  (signal cancel rules between families, signal weights, position-aware
  sizing utils — sell on large position / buy on low position — and the
  max-return goal) — plus the 2026-11-16 EMPIRICAL comb study (Part 3:
  which family combinations actually measured better on the 10 study
  indices, and the honest negatives — IS weight tuning, TOP-K
  selection, veto rules, position overlay) and the nearby-date cluster
  study (Part 4: the 1st signal day vs the best entry/exit date —
  waiting is a cost, cluster co-occurrence is the edge variable) plus
  the online-methods self-test (Part 5: meta-labeling rebalances the
  winner's weakest segment, Elder triple screen is the OOS-dominant
  mirror, tuned/inverse-vol/regime weights all ≤ equal) and the
  self-adaptive comb utilities (Part 6: rolling-edge weights, quantile
  evidence sizing, vol-state exposure, equity guard — no tuned params,
  +47.6% full / +22.7% OOS vs the naive sum's −6.3% / −26.6%) and the
  creative batch + meta-combs (Part 7: breadth finishing, EWMA edges,
  comb-of-combs — champion ca 7.55 at dd 6.6%; stacking/ Kelly/crash-
  stop honest negatives), the mutual-overlap matrices (Part 8:
  family / sub-type / comb trade-set overlap rates) and the risk study
  (Part 9: the official risk_score separates naive from the adaptive
  combs 8-12x; V8c best OOS Sortino/Sharpe/Ulcer/CVaR; run
  compute_risk_seq in-memory inside Set-B evaluation) and the
  wide-universe validation (Part 10: 508 indices — P6_pair is the wide
  champion, P6_mc's 10-index edge does NOT transfer, scale-safe
  difference-array/bisect algorithms required at 50x events) and the
  zero-weight filter study (Part 11: conventional 20/60/252-row
  windows — 252 is the only predictive window, best shipped form soft
  bar-0.25% w252) and the mean/variance adaptive study (Part 12:
  t-stat weights w ∝ max(t, 0) at w252 — variance helps at the
  margins, EWMA-h63 is the defensive variant, one statistic implements
  weight + zero filter) and the signal-amplification studies (Part 18:
  the OHLCV-anatomy amplifier stage 3b is a measured NO-OP at shipped
  strength on the 10 study codes — thin mass, near-zero evidence
  validity, blend-denominator dilution; the strength sweep s1-s4: OOS
  cushion monotone in strength paid by FULL quality decay past s2,
  s2/s3 the sweet spots; no trainer axis, no validity gate on this
  evidence) and the oracle confidence-scaling study (Part 19: the
  _oracle/ scaling group + its own isolated optimizer — convex power
  1.5 + full exits dominates the raw-grade replay on every metric;
  exit_mode retired from TUNABLE_SPACE into the group). Use before ANY
  change to the trainer,
  the losses, the search spaces, algo_configs params shape, the signal
  blend / collector, or the execution engine's sizing — and whenever
  asked to "refactor Trained configs", comb/combine signal families,
  add cancel rules, tune weights, or make sizing position-aware.
---

# Singleton strategy training model + trained-config parametrization cautions

Distilled from the 2026-11-16 study of the strategy tier
(`strategy/factors_and_algos`, `strategy/_trading`, `strategy/singleton_trading`,
`database/sql/strategy/`), verified against the code. Part 1 is the map;
Part 2 is the caution list for the planned refactor: trained configs →
a small number of interpretable param groups (which signal conditions
cancel which other signals; what weight each signal family gets;
position-aware sizing utils; ultimate goal max return).

## Part 1 — how the training model works today

### The two entries

- **Run Strategy** — `python -m strategy.singleton_trading`
  (`strategy/singleton_trading/__main__.py`). BINARY (`--algo macd`) or
  MIXED (`--algo macd,mov_rsi`, equal blend). Writes
  `strategy_identity` / `trade_decision` / daily rows, then risks.
- **Train Model** — `python -m strategy.factors_and_algos._optm_engine`
  (`_optm_engine/__main__.py`). **Binary-mode only** (UI
  `POST /api/strategy/singleton/train` → `runTrainingScript`). Writes
  `strategy.training_runs` + `strategy.training_trials` and the trained
  `strategy.algo_configs` row. In-memory backtests only during the study.
  FINAL STEP (2026-11-18, user-directed): after the params are upserted
  the trainer spawns the run-once CLI (`strategy.singleton_trading`,
  `--force`, pinned default FT) for the same (algo, sec-type, codes) —
  a finished training ends with a real strategy run recorded in the run
  history, with no browser open. `--no-run` skips (study-only);
  skipped under `--no-upsert` too (the run would load stale params).

### Signals are PICKED UP, never recomputed (2026-11-07, user-directed)

Every algo subclasses `AnalysisSignalAlgoBase`
(`_algo/analysis_source.py`): the only legal signal source is
`analysis_signals.history_signals` (standalone tier,
`is_in_portfolio = FALSE`, `action IN (buy, sell)`), one event per
episode-FIRST day (the analysis tier's K=5 absorb already de-whipsaws).
`TUNABLE_SPACE` is empty — there are NO model params to tune; the
optimizer tunes only the COMMON trading space. Sizing is a FLAT replay
(`EVENT_CONFIDENCE = 100`): BUY adds 100 qty units, SELL exits 100% of
the position. The recorded bp confidence is EVIDENCE, not sizing.

### Signal pipeline order (the composition seam)

`AlgoBase.run_backtest` (`_algo/base.py`) / `AlgoSignalCollector.run_backtest`
(`algo_signal_collector.py`):

1. `apply_signals` — event → signed `signal_confidence` ∈ [-100, 100]
   (>0 BUY, <0 SELL rising-edge-episodes, 0 none) + `signal_value`.
2. MIXED only: `_blend_signals` — per-algo signals weight-blended
   (equal weights since 2026-11-07, normalized to sum 1, clipped
   [-100,100]; **netting** — the sign of the weighted sum decides the
   side) + `__MIX__` JSON prefix into `signal_reason` for the UI tooltip.
3. `tune_signals` (`_algo/tuning.py`) — zero out |conf| <
   `conf_threshold` (Stage-A tunable, default 5.0) — dust filter.
4. `apply_exec_delays` — BUY / SELL shifted independently by
   `buy_exec_delay` / `sell_exec_delay` bars per code group; a delayed
   BUY + delayed SELL landing on the same bar NET.
5. Engine (`strategy/_trading/engine.py`) — reads ONLY
   `signal_confidence` + `signal_value` + trading-layer keys
   (`min_holding_period`, `buy_notional`, `skip_final_liquidation`,
   `fault_tolerance`, `fee_rate`, `slippage_band`). Long-only, no
   capital budget (cash goes negative on BUY); BUY qty = confidence;
   SELL qty_sold = (conf/100) × total_qty; SELL gated by
   `min_holding_period` bars since the last BUY; dust SELL skip
   (<0.01 units); worst-case OHLC fills + slippage band + fee; last
   bar reserved for final liquidation (vol-scaled momentum projected
   fill); all metrics normalized (anchor = 100 at first BUY fill).

### The NestedTrainer 5 steps (`_optm_engine/training/trainer.py`)

| Step | What | Loss | Segment |
|---|---|---|---|
| 1 | Set A (conf_threshold + algo TUNABLE_SPACE) — Optuna TPE (startup trials scaled to `trials//3`) | **OmegaLoss** −Ω of per-exit returns; hard constraint >55% positive months | IS (first 80%) |
| 2 | top-K DISTINCT candidates (default 5, deduped param sets) | | |
| 3 | Analytical Kelly per candidate: f* = μ/σ² → cap 20% → ×0.25 fractional → static `buy_notional` | | IS, neutral Set B |
| 4 | Set B (`buy_exec_delay` 0–5, `sell_exec_delay` 0–5, `min_holding_period` 1–15) — exhaustive vanilla grid, 6×6×8 = 288 points | **CalmarLoss** −(ret/maxDD); hard constraint max DD ≤ 25% of peak capital, breaching points discarded | OOS (last 20%) |
| 5 | Final selection: best Calmar under the DD cap across candidates + full-series sanity check (report only) | | IS+OOS |

Loss grading template (`loss/base.py` `RegimeLoss`) — invariants that
must hold for ANY new loss: valid trial → −metric; constraint-violating
→ `100 + 20·deficit − discount` (discount clamped [0,10] — the deficit
is the gradient TPE follows toward feasibility); no-trade → 1000
(worse than ANYTHING that trades). Omega/Calmar caps 1e6 keep TPE loss
finite. Rationale for the two regimes (`objective.py` docstring): Set A
is path-independent signal quality → Omega ignores upside magnitude so
TPE can't chase bull-market leverage; Set B is path-dependent
compounding → Calmar forces capital preservation.

### Trained-config storage (`database/sql/strategy/algo_configs.sql`, `persist.py`)

- `strategy.algo_configs` PK `(sec_type, sec_code, strategy_name,
  start_date, end_date)`, `params JSONB`, HASH-partitioned by sec_code.
- Trained row: `[train_date, 9999-12-31]`, `is_default = FALSE`;
  same-day retrain overwrites in place; later training inserts a NEW
  row (history preserved).
- Default row: `[1900-01-01, 9999-12-31]`, `is_default = TRUE` —
  NEVER overwritten by training; both coexist (UI shows both).
- Loader precedence (`factors_and_algos/loader.py` `load_params`):
  algo `DEFAULT_PARAMS` < DB row < `strategy_overrides`; the loader
  picks the row whose range contains today, `ORDER BY start_date DESC
  LIMIT 1` → the newest trained row wins over the default row, and a
  hand-authored DATED row with a later start_date keeps precedence.
- `_STRIP_KEYS` = `skip_final_liquidation, fault_tolerance, fee_rate,
  slippage_band, buy_notional` — per-study assumptions never stored.
- Training-process persistence (`training/training_store.py`):
  `training_runs` header 'running' → 'completed'/'failed' (best params
  + metrics + Kelly + full `log_text` replay); `training_trials` one
  row per evaluated point tagged `loss_type` `set_a_omega` /
  `set_b_calmar` (PK run_id + loss_type + trial_no + grid_idx). Trials
  are inserted under EVERY per-code run row (the study is cross-code).
  All persistence is best-effort — it must never kill a finished run.

## Part 2 — caution considerations for the trained-config refactor

### A. Config schema & lifecycle

- `algo_configs.params` is schemaless JSONB with NO DB validation —
  any new parametrization (cancel rules / weights / sizing params) must
  validate in Python: a pydantic `BaseModel` config payload
  (`model_validate` at the loader, `model_dump()` at the writer), plus
  a version tag inside the JSON so old rows and new rows coexist.
  House pattern for tolerance: `snap_categorical_params`
  (`space.py`) — normalize out-of-vocabulary values defensively, never
  crash a load on a stale row.
- Old trained rows keep their old param shape by design (history rows).
  Every consumer (loader, UI `train-info`, TrainConfigsDialog) must
  tolerate BOTH shapes.
- Add any NEW per-study assumption to `_STRIP_KEYS` or it leaks into
  every trained row.
- JSONB comes back as a STRING (this project registers no codec) —
  always load through `load_algo_config` (`_coerce_params`), never
  read the table ad hoc.
- `strategy_name` is the join key everywhere (binary = algo name,
  mixed = `portfolio:<families>` (+ `_ft{N}`); skip-if-already-found
  idempotency keys on it). A trained config is per strategy_name —
  training a portfolio config requires training to support portfolio
  names (it is binary-only today).
- DB changes are SQL-first (`database/sql/strategy/`); any new
  type/kind/state/family/rule column is SMALLINT + a
  `static.label_dim` domain row (db-smallint-type-codes), never TEXT;
  update the table's KG banner in its DDL if its semantics change
  (kg-db-semantics).

### B. Signal-cancel rules (what conditions cancel which signals)

- Today the ONLY cross-signal semantics are: blend **netting**
  (weighted sum, sign decides side), the `conf_threshold` dust filter,
  and the engine's `min_holding_period` SELL gate. A cancel rule is NEW
  semantics — decide its stage deliberately:
  - **pre-blend** (a family's condition suppresses another family's
    event before the weighted sum) — changes the blend inputs and
    therefore every sub-algo's contribution in the `__MIX__` tooltip;
  - **post-blend** (a veto on the consolidated signal before the
    engine) — keeps per-algo contributions intact, matches the
    "engine reads one consolidated signal" contract.
  Do NOT put cancels inside the engine — the engine is
  signal-agnostic by contract.
- Order-of-operations trap: `apply_exec_delays` shifts BUY/SELL
  independently, and same-bar delayed BUY+SELL nets. A cancel evaluated
  pre-delay cancels a signal that may execute on a different bar;
  evaluate cancels at a defined stage and document it (the pipeline
  order in Part 1 is the seam).
- Lookahead: a cancel rule may only read information available at the
  signal bar (row columns / earlier bars). Never future bars — this
  silently inflates IS metrics.
- Discrete cancel rules give TPE no gradient: keep few,
  economically-motivated rules whose THRESHOLDS are the tunable
  numerics (continuous), not many binary on/off rules. Validate on the
  OOS segment, not just IS.
- Population collapse: families are small (macd buys p50 ≈ 77bp
  evidence; episodes, not bars). An aggressive cancel rule can zero
  most trades → every trial hits `NO_TRADE_LOSS` 1000 and Stage A
  stops trending. Watch `n_trades` in the trial log.
- Do NOT re-add whipsaw filters per bar — the analysis tier's
  K-absorb episode shaping already de-whipsaws; a second filter
  double-counts and shifts episodes.
- Interactions: a canceled SELL still counts for nothing in
  `min_holding_period` (the gate is bars-since-last-BUY, not
  sell-attempts); a canceled BUY reduces entries and therefore future
  SELL eligibility (no position → SELL skipped). Measure both sides.
- New rule vocabulary (family/kind of cancel rule) →
  db-smallint-type-codes; the cancel semantics of the nine families is
  signals-biz-logic / signals-live-vs-history territory — check there
  before inventing family conditions.

### C. Signal weights

- Weighting was REMOVED 2026-11-07 by user direction ("remove weighting
  from signals") — the blend is EQUAL, `_parse_algo_arg` strips legacy
  `:weight` suffixes, `portfolio_name` carries family names only.
  Re-introducing weights is a semantics REVERSAL: confirm with the
  user before restoring, and decide whether weights live in
  `--algo` (CLI surface) or in the trained config (DB surface).
- Blend math (`blend_signal_confidence` / `_blend_signals`): weights
  normalized to sum 1, result clipped [-100,100], netting decides the
  side. Consequences to keep in mind:
  - a dominant weight can silence the other family entirely (its
    contribution never flips the net sign) — the trial metrics then
    degenerate to the single-family strategy;
  - equal weights are the degenerate optimum the optimizer may always
    return; if weights are tuned, constrain them (e.g. bounds + sum
    to 1) and inspect the per-algo contribution in the `__MIX__` JSON.
- Idempotency: `portfolio:<families>` is stable-sorted and weight-free;
  skip-if-already-found keys on it. If weights become per-config
  tunable, either encode them in the name (legacy `portfolio:macd*0.5`
  style) or accept one shared strategy_name serving different weight
  configs — and then trained-row precedence (newest start_date wins)
  silently changes weights on every retrain.
- Keep the `__MIX__` tooltip contract (`algo_signal_collector`
  `build_signal_reason`: `{algos:[{a,algo,w,c,n}], blended, netted}`)
  in sync with any weight change — the UI (`SignalTagsInput` /
  mix tooltip) parses that prefix.
- If weights join the optimizer: they are continuous but path-coupled
  through netting (sign flips are discontinuous) — tune them in Stage
  A with FIXED sizing, and keep the equal-weight point in the space as
  a baseline trial.

### D. Position-aware sizing utils (sell on large position, buy on low position)

- Today sizing is FIXED (flat 100-unit replay) and the trainer's
  backtest is notional-invariant — Omega is sizing-neutral by
  construction, and the Kelly amount is a static deployment assumption
  (reported, never persisted). A dynamic sizing rule BREAKS both
  properties: it makes signals path-dependent (`POSITION_AWARE`
  semantics) and couples sizing into the Stage-A loss.
- Blend consistency: `check_position_aware` forbids POSITION_AWARE
  algos in mixed mode because each algo's signal would depend on its
  own book. A position overlay applied AFTER the blend (one shared
  book) is consistent; per-algo position overlays before blending are
  not. Put position-state sizing at the consolidated-signal layer.
- Feedback loops: "large position → encourage sell, low position →
  encourage buy" is a mean-reverting overlay that fights trend signals.
  Without guards it churns: buy-into-dip → trend continues → position
  grows → overlay forces sells into the falling market. Required
  guards: dead-bands (hysteresis between "large"/"low" thresholds),
  capped overlay magnitude, respect `min_holding_period`, and check
  the turnover/fee drag against `fee_rate` / `slippage_band` statics.
- Sizing multiplies into qty = confidence: an overlay implemented as a
  confidence multiplier interacts with BOTH dust gates — a
  multiplied-down signal can fall under `conf_threshold` (5.0) and
  silently become a no-trade, and a partial SELL can fall under the
  0.01-unit dust skip. Trial metric populations then shift between
  configurations — compare `n_trades`, not just loss.
- Define "position" on a scale: the engine is long-only with NO
  capital budget (cash negative on BUY); position exists as
  `total_qty` units and `position_value` in normalized units (anchor
  = 100). Express "large/low" as fractions of a notional cap or of
  peak deployed capital — pick one definition and keep it in the
  config schema.
- The Stage-B hard constraint (max DD ≤ 25%) is the guardrail that
  will reject aggressive sizing; sizing params must go through the
  SAME OOS/Calmar path (e.g. as extra grid axes — the trainer docstring
  reserves "sizing-multiplier sensitivity" for Step 4), never bypass
  the constraint. Kelly constants (cap 20%, ×0.25) were derived under
  flat sizing — re-derive or re-fit when sizing becomes dynamic.

### E. The "ultimate goal is max return"

- Do NOT collapse the two-regime design into one max-return objective —
  `objective.py`'s rationale is explicit: one objective on both regimes
  ruins the optimization. Express max return WITHIN the constraint
  system: Omega + >55% positive months (signal quality), Calmar + DD ≤
  25% (execution), Kelly cap 20% × 0.25 (sizing). A raw max-return
  objective degenerates in-sample to all-in leverage chasing the bull
  segment; that is exactly what the regime split and caps exist to
  prevent.
- IS max ≠ OOS max — Stage B grades on the OOS split for that reason;
  keep the split (`--oos-frac`, clamped [0,0.5], <60-row segments fall
  back to full series WITH a warning) and the full-series check
  report-only.
- Position overlays (D) trade return for path safety — if the user's
  goal is max return, the overlay's cost must show up in Calmar, not
  be hidden by metric population shifts.
- Fees/slippage are real: total_return is net of the statics; a
  cancel rule that "improves return" by never trading hits
  `NO_TRADE_LOSS`, and one that trades slightly less but avoids fee
  drag is a legitimate win — read `n_trades` + `per_code` together.
- Any new loss must preserve the `RegimeLoss` ordering invariants
  (Part 1) or TPE stalls: violating < valid, no-trade worst, deficit
  gradient toward feasibility.

### F. Implementation & verification mechanics (pointers)

- Backend changes class-first OOP (pydantic for the config payload);
  load the `cudf-dtypes` skill before ANY dataframe change (the
  bit-identical A/B bar applies to refactors).
- SQL-first for schema (`database/sql/strategy/`), writes through
  `_common/db_commons` helpers (db-sql-write), one-time migrations
  from `temp_scripts/` only.
- Run through WSL conda, `--sec-type index`, only the 10 named study
  indices; clear `__pycache__` after edits.
- Verification is the UI path (never ad-hoc Python): Train Model from
  `data_viz` → check `GET /api/strategy/singleton/train-info`
  (default + trained rows + runs) and the run log replay
  (`training_runs.log_text`); Run Strategy from the UI → check the
  Express stdout + browser console (ui-test skill).
- Remember the trainer warns when cudf.pandas is inactive — the
  trials × top-K × 288-grid loop is compute-intensive.

## Part 3 — the comb study (2026-11-16): how to combine the families, measured

Empirical study on the 10 study indices, index scope, standalone tier
(`is_in_portfolio = FALSE`), production pickup contract (flat ±100
replay, DISTINCT ON per (code, date)), production engine
(`strategy._trading.engine`) + production regime losses (the SAME
bundles the trainer uses). Segments: IS = 2020-01-01..2025-07-04
(first 80%), OOS = 2025-07-07..2026-11-16 (last 20%); split-stability
re-checked at oos_frac 0.1/0.3/0.4. Harness:
`temp_scripts/sig_comb_study.py` (modes sweep / wsearch / wsidesearch /
gates / regime / overlap / overlay), `sig_comb_census.py`,
`sig_comb_cuts.py`, `sig_comb_analyze.py`; result CSVs
`temp_scripts/sig_comb_*.csv`. Plain-host pandas study, DB read-only.

### The combinable universe (census)

- 8 families carry events on the study indices: ma_cross (1446 buy /
  1156 sell), rf_break (1327, **BUY-only**), crowdedness (1189/719),
  momentum (656/347), mov_rsi (511/172), rebound (266/72), macd
  (203/279), bollinger (184/222). options_wall / options_vix trade the
  options underlyings (000300/000016/000852) — ZERO events on the 10
  indices; hedge_pairs / hp_rsi live at pair codes. No reject-tier
  (confidence < 0) rows exist yet (the 2026-11-16 DDL landed first).
- Young codes (931594, 931994) have thin families (macd 4-9 events,
  bollinger 1-5) — per-family comb weights on those codes are
  noise-dominated; comb decisions should use the pooled population.

### Signal-overlap structure (why equal-blending all families fails)

- **mov_rsi × bollinger × momentum agree 1.00** (event-day side
  agreement; buy Jaccard .09-.17) — ONE reversion thesis in three
  clothes. Equal-blending all families triple-counts it. Also
  rf_break × rebound agree 0.96 (both capitulation V-bottom). (The
  agreement structure is episode-date identity — stable across
  snapshots even when populations shift.)
- ma_cross × macd agree 0.78 (both trend-following, different
  windows) — high but they are the family pair that MEASURED best
  together (below).
- mov_rsi/bollinger × macd share ~ZERO event days (agree 0.00-0.33) —
  reversion vs trend-following are structurally complementary.

### Singles: IS stars are OOS disasters

| single | IS omega / calmar | OOS omega / calmar | OOS ret |
|---|---|---|---|
| momentum | 8.74 / 0.40 | 1.01 / −0.76 | −30.9% |
| rf_break | 3.38 / 0.30 | 0.00 / −0.61 | −43.1% (dd 71%) |
| mov_rsi | 2.85 / −0.06 | 0.15 / −0.94 | −33.5% |
| bollinger | 0.66 / −0.50 | 0.14 / −1.00 | −31.5% |
| macd | 1.00 / −0.36 | 0.71 / −0.38 | −10.9% |
| rebound | 0.89 / −0.13 | 0.40 / −0.38 | −17.9% |
| ma_cross | 1.29 / 1.87 | 0.92 / 0.24 | +4.4% |
| crowdedness | 1.31 / 0.46 | 0.52 / 0.56 | +7.9% |

The reversion/momentum/rf families dominated the 2020-2025 IS era and
collapsed in the 2025-07..2026-11 adverse regime; **ma_cross and
crowdedness are the only OOS-stable singles**. Never rank comb
candidates by IS omega alone. (macd's row moved between two SAME-DAY
snapshots — see snapshot sensitivity below.)

### What measured BETTER (the positive findings)

1. **Pair blends beat their best single 28/28 on OOS omega** — blending
   helps robustness even when the pair includes a weak family.
2. **ma_cross+macd under REGIME-MATCHED composition (regime_matched120)
   is the best measured comb** — gate the FAMILY columns pre-blend
   (trend-family BUYs only above the 120d SMA, SELLs ungated; the pair
   has no reversion families): IS om 1.64 ca 0.31 ret +6.7% dd 21.6%;
   OOS om 2.20 ca **1.57** ret **+16.5%** dd **10.5%**. Split
   stability: OOS ret +0.8% / +16.5% / +20.9% / +23.0% at oos_frac
   .1/.2/.3/.4 — the ONLY scheme positive in every cut (the recent
   6-month window, frac .1, is mildly negative for everything else);
   per-code OOS: 7/10 beat ALL8, only 3/10 negative (best Σ ret
   +1.41). W255 similar (IS ca 0.52 / OOS ca 0.77). trend3
   (ma_cross+macd+rf_break) reg_matched120: IS ca 0.31 / OOS ca 1.03.
3. **ma_cross + crowdedness (equal, ungated)** — the robust
   no-gate comb: IS ca 1.87 ret +17.9% dd 9.6%; OOS ca 0.51 ret
   +9.9% dd 19.3%; cuts −0.9% / +9.9% / +18.8% / +23.4%; 8/10
   per-code wins vs ALL8. crowdedness is the most regime-stable
   single (9/10 per-code OOS wins vs ALL8).
4. **Regime evidence, not a recommendation**: trendbuy60 (BUYs only
   above SMA60) turns IS-flat into OOS-spectacular (ma_cross+macd OOS
   ca 3.81 ret +22.9% dd 6.0%; ma_cross+crowdedness OOS ca 4.84 ret
   +24.4% dd 5.1%) — but its IS side is negative, so it is an OOS-era
   bet, not a shippable default (see the flip in the negatives).
5. **Verdict for the trained-config refactor**: family SELECTION (the
   subset, thesis-aware) is the first-class param; regime-matched
   gating is the strongest measured comb form; equal weights beat
   every tuned weighting OOS. Weights are a second-order param,
   thresholds third.

### The honest negatives (measured — do not ship without new evidence)

- **Snapshot sensitivity (process caution first)**: the analysis_signals
  nightly re-replay moved several families' event populations BETWEEN
  TWO SAME-DAY study runs (macd IS trades 103 → 235, its single IS
  calmar 1.10 → −0.36; mov_rsi/bollinger similar) — event history is
  re-measured, not immutable. The robust ordering (ma_cross /
  crowdedness stable, ALL8 bad) survived both snapshots, but
  single-family rankings did not. Freeze ONE snapshot for a whole comb
  study; re-validate the chosen comb after any analysis_signals
  rebuild; prefer schemes whose edge does not ride the volatile
  populations.
- **IS weight tuning does NOT generalize**: 400-draw Dirichlet search
  (objective IS-Omega) — ALL top weight vectors lose to the plain
  equal-weight base OOS (base OOS ret −26.6%). The 16-dim per-side
  (buy-vector / sell-vector) search: same verdict (best OOS ret
  −25.0%, still deep negative). IS-Omega rewards the IS-era
  reversion stars; the OOS regime punishes exactly them.
- **TOP-K selection by IS quality overfits**: equal:TOP3/TOP5 (the IS
  omega leaders) OOS ret −35.6% / −17.6% vs the ALL8 base −26.6%.
- **Vote / cancel rules are near-no-ops under equal ±100 blends**:
  veto_sell moves IS calmar 0.510→0.544 and OOS −0.516→−0.532 (no
  gain); majority is IDENTICAL to the base (with flat ±100 events the
  weighted netting already implements majority); vetoes only
  re-shuffle trade days via min_holding_period. If weights ever become
  unequal, re-test — the no-op property is weight-dependent.
- **Raising the blend conf_threshold hurts OOS**: thr20/50 improve IS
  Calmar (0.51→0.64/0.68) and collapse OOS (om 4.02→1.97/0.00, ret
  −31.5%/−38.3%) — the disagreement trades (small net magnitudes)
  were carrying OOS.
- **The position overlay (sell-large / buy-low) FAILED at the tested
  parameters** (hi_band .6 / lo_band .2, k_buy 1.0 / k_low 0.5 /
  k_sell 1.0, 2-pass iterative with the real engine): it cushions the
  fragile ALL8 marginally (OOS ret −26.6%→−24.9%, dd 51.5%→50.6%) and
  DEGRADES the robust schemes (ma_cross+macd OOS calmar 0.11→0.04, dd
  23.8%→29.1%; reg120 OOS calmar 1.57→1.18). The drawdowns are MARKET
  direction, not position concentration — the overlay's low-position
  buy encouragement just adds exposure in falling markets. Any
  production "encourage sell when large / buy when low" util must beat
  the scheme's own no-overlay Calmar on the OOS segment, not just
  raise total return.
- **A static BUY-side regime gate is a regime bet**: dipbuy60 (buys
  only below SMA60) is IS-spectacular (ALL8 om 7.27 ca 1.06) and
  OOS-disastrous (−32.4%); trendbuy60 is IS-negative and OOS-
  spectacular (ca 3.8-4.8). The eras flip the sign — never ship a
  single-era gate as a config default; regime_matched
  (thesis-matched) is the defensible form.
- **ALL8 equal blend (the current production mixed behavior) is
  fragile**: OOS ret −26.6% dd 51.5% (9/10 codes negative) despite a
  HIGH OOS omega 4.02 — omega of per-exit returns stayed positive
  while the open-position path drew down. Judge combs on Calmar/DD +
  per-code wins per segment, never omega alone.
- **Regime-matching does NOT rescue reversion families**: rev5 (all 5
  reversion-ish families, buys below SMA) OOS ret −29.1% — reversion
  buys below a falling SMA are knife-catching. The gate is only sound
  where the family thesis matches the regime (trend families above
  SMA). The reversion families' OOS problem is a SELECTION problem
  (exclude or de-weight), not a gating problem.

### Method notes for reproducing

- The harness reuses the production fetch SQL / blend semantics /
  tune_signals / engine / loss classes verbatim; results are
  trainer-comparable. It does NOT apply exec delays (mixed-mode
  production doesn't either — only AlgoBase.run_backtest does).
- The IS/OOS split mirrors `_split_is_oos` (per-code positional 80/20,
  ≥60 rows per side else full) — each segment backtests standalone
  (position resets at the boundary), same as the trainer. IS =
  2020-01-01..2025-07-04, OOS = 2025-07-07..2026-11-16 at frac 0.2.
- The overlay is a 2-pass approximation (pass-1 position path from the
  real engine, rescale signals, re-run; iterate) — not an in-engine
  loop; production implementation would be an engine feature (the
  POSITION_AWARE caution in Part 2 D applies).
- Every conclusion above is at INDEX scope on the 10 study indices
  with flat ±100 replay and default trading params (min_hp 5, no exec
  delays, threshold 5) — re-measure before generalizing to etf/stock
  or to trainer-tuned Set B params. Numbers quoted are the
  2026-11-16 evening snapshot (the second run; see snapshot
  sensitivity).

## Part 4 — the nearby-date cluster study (2026-11-16): the 1st signal day vs the best buy/sell date

Hypothesis (user-directed): when signals sit in nearby dates, the FIRST
one is usually unstable and after a few days the best buy/sell date
reveals itself. Measured at three levels on the same harness cache
(`temp_scripts/sig_cluster_study.py` — modes wait / cluster / engine;
CSVs `temp_scripts/sig_cluster_*.csv`; cluster = same-side family
events within a 5-trading-row window per code, the K=5 absorb
convention; forward metric = the repo's dir_ave blend
0.10/0.15/0.50/0.15/0.10 over 0/3/5/10/20d, only events with the full
20d future realized).

### Study A — wait-cost curves (per family, population level)

The forward edge is highest ON the signal day and decays MONOTONICALLY
with waiting — for every event-driven family:

- BUY quality (dir_ave from t+d): momentum d0 +2.57%@74% → d2 +1.75% →
  d5 +0.82%; crowdedness +1.86%@70% → +1.61% → +1.18%; rf_break +1.89%
  → +1.33% → +1.06%; ma_cross +1.20%@60% → +0.80% → +0.30%; rebound
  +1.34% → +0.80% → +0.39%. The slow reversion tails (mov_rsi,
  bollinger) show only a tiny d1–d2 confirmation hump (mov_rsi +0.69%@
  57% → +0.81%@58% → +0.78%@61%; hit rate +3–4pp, mean ~+0.1pp).
- The ENTRY COST of waiting is real: price drifts UP after buy signals
  (the measured continuation) — momentum buys +1.29% dearer by d2,
  +2.80% by d5; rf_break +1.01% by d2; crowdedness +0.71% by d2; ma_cross
  +0.63% by d2. Only mov_rsi / macd / bollinger re-visit (≤ +0.2% by d2).
- SELL quality decays even faster: crowdedness sells +1.32%@69% →
  +0.92%@64% (d2) → +0.25%@54% (d5) → NEGATIVE by d10; the price FALLS
  while you wait (sell-side drift −0.34% by d1, −1.44% by d5 — waiting
  to sell hands the move back). The one exception: **bollinger day-0
  sells measure NEGATIVE** (−0.75%@49% — the band breach keeps running
  for a couple of days) and waiting 2–3 days reduces the damage (−0.21%
  by d2) — the equity asymmetry again (tops persist).

**Verdict: day 0 is the best buy date on average; waiting is a cost,
not a confirmation.**

### Study B — cross-family clusters: SIZE is the edge variable, not the day

Cluster size distribution (buy clusters): 832 solo (1 family), 422
two-family, 229 three-family, then a long tail (the crash/rally days
when everything fires). Forward edge by cluster size × entry policy:

| | solo | 2-family | 3-family | 4+ |
|---|---|---|---|---|
| BUY, enter at 1st event | **−0.33%@44%** | +1.35%@68% | +2.16%@69% | +0.72%@51% |
| BUY, enter at 2nd event | — | +0.15%@52% | +1.85%@71% | +1.18%@59% |
| BUY, enter at 3rd event | — | — | +0.75%@56% | +1.39%@61% |
| SELL, enter at 1st event | **−0.42%@45%** | +1.06%@63% | +1.10%@66% | −0.81%@48% |
| SELL, enter at 2nd event | — | +0.38%@58% | +0.65%@65% | +0.03%@57% |

Three findings, in priority order:

1. **Solo signals carry NO edge** — a single family firing alone
   measures negative on BOTH sides. The instability the hypothesis
   senses is real, but it is a MEMBERSHIP property (nobody joined), not
   a timing property.
2. **Within a confirmed cluster, the FIRST event is still the best
   entry** — m1 > m2 > m3 everywhere (the first event day captures the
   turn; by the 2nd event the price has already moved).
3. The 4+ sells turn negative (−0.81%) — by the time 4+ families all
   fired SELL the decline is mature; late mega-cluster exits are poor.

**Verdict: the best buy/sell date is day 0 of a CONFIRMED (≥2-family)
cluster. The fix for solo instability is co-occurrence, not waiting.**

### Study C — engine validation (causal policies on the finalists)

Causal implementations (no lookahead) on the Part-3 finalist schemes,
IS / OOS:

- **No cluster policy improves the robust schemes.** ma_cross+macd
  reg120: base OOS ca 1.574; suppress-cluster-first buys (fire only at
  the 2nd+ event) 1.427; keep-only-1 0.257; keep-only-2 −0.345; wait-2
  0.644. ma_cross+crowdedness: base OOS ca 0.511; conf 0.396; only1
  0.683 (IS 1.87→0.99 — worse); only3 1.925 with IS 1.14 (a one-off
  OOS curiosity — the selection-on-OOS trap, not shippable). The
  staggered first-day entries ARE the edge optimum for a good comb.
- **Solo-scaling (fire the cluster-first event at half size, full size
  once a 2nd family joins — the date-preserving translation of the
  finding) also gains nothing consistently**: reg120 IS 0.31→0.40 but
  OOS 1.57→1.03; ma_cross+crowdedness IS 1.87→1.26; ALL8 ≈ flat.
- **On the fragile ALL8 the buy-side confirmation policies DO cushion
  the OOS loss** — only1 OOS ret −10.8% / only2 −7.5% vs base −26.6%
  (still negative): the cluster-first knife-catch buys were exactly the
  unstable population there. The scheme's problem remains SELECTION
  (Part 3), not timing.
- **Sell-side policies are uniformly harmful**: sell wait-2 / confirm /
  keep-only-2 all worsen every scheme (ALL8 S=only2 OOS dd 61%; reg120
  OOS ca 1.574→0.772 at best). Exits fire on the first sell event,
  immediately.

### Recommendation (what this means for the trained-config refactor)

1. **Do NOT build wait-N-days entry/exit policies** — measured cost on
   both sides (the trainer's Set B `buy_exec_delay`/`sell_exec_delay`
   grids will rediscover 0/0 on good combs; the Set-B tuning may still
   find small delays useful on specific families, but the population
   prior is 0).
2. **Cluster co-occurrence is the usable form of the insight**: the
   number of same-side families within a trailing W-row window is a
   proven edge discriminator (solo −0.33% vs 2-family +1.35% vs
   3-family +2.16%). If the trained config carries a comb param, express
   it as a cluster-aware CONFIDENCE feature (scale solo events down /
   confirmed events up) rather than a date filter — the engine test
   shows date-changing filters cost the robust schemes their best
   entries, while the fragile schemes' cushion comes from NOT trading
   solo knife-catches (better handled by Part-3 selection).
3. **bollinger sells are the one measured timing exception** (day-0
   negative, d2–d3 better) — a family-local exit-delay note, consistent
   with the equity asymmetry; do not generalize it.
4. **Consistency with the analysis tier**: the K=5 absorb + episode-
   first-day firing already encodes "nearby re-entries are one
   episode"; this study extends the validation to the comb level and
   adds membership (cluster size) as the missing confidence axis.

## Part 5 — the online combination methods, self-tested (2026-11-17)

The literature's canonical signal-combination frameworks (Lopez de
Prado meta-labeling, Elder triple screen, ensemble/stacking, signal
pools / confluence, IC and risk-based weighting), implemented causally
and self-tested on the same harness (10 indices, production engine +
losses, IS = 2020-01..2025-07 / OOS = 2025-07..2026-11). What the
literature recommends vs what MEASURED:

### M1 Meta-labeling (Lopez de Prado) — the balance-improver

A per-side secondary model (RandomForest + LogisticRegression,
features ALL TRAILING at event time: same-side cluster counts in the
trailing 5-row window, close/SMA60/120/255 ratios, SMA60/255 slope
proxy, 20d/5d returns, vol20/vol252 ratio, |ret1|, family one-hot;
label = sign-aligned dir_ave > 0) trained on IS events only
(buy n=3442 base rate 60.2%, sell n=2270 base rate 58.8%), applied to
OOS events. Policies: filter (drop p < tau) and size (BUY confidence ×
max(2p−1, 0); exits never scaled down).

- Population OOS: filter does NOT lift mean event quality (base buys
  +1.84%@64.7% → p≥0.5 +1.79%@66.2%, p≥0.6 +1.64%) — the model's
  discrimination is modest. Sells: base −0.06% → p≥0.6 +0.09%@57.3%
  (marginal).
- ENGINE (the real effect): on ma_cross+macd reg120 the p≥0.5 filter
  REBALANCES the segments — IS ca 0.313 → **1.025**, OOS 1.574 → 1.248
  (worst-segment Calmar ×3.3, both segments positive, trade count
  preserved). On ma_cross+crowdedness IS 1.87 → 2.05 but OOS 0.51 →
  0.28 (mixed). On ALL8 IS 0.51 → 1.06 but OOS still −0.72.
- **tau=0.6 overfits**: OOS trade count collapses (reg120 150 → 16),
  OOS ca −0.66 — the tighter the meta threshold, the more it is an
  IS artifact. Sizing by p is WORSE than filtering (OOS ca 0.51 on
  reg120) — consistent with Part 4 (exit/size manipulation hurt).
- Verdict: meta-labeling is the principled wrapper for exactly the
  Part-4 features (cluster state + trend + vol), and at a LOOSE
  threshold it is the one measured way to lift the robust winner's
  weakest segment. Ship only p≥0.5-style filtering with the
  trade-count floor watched.

### M2 Triple screen (Elder) — the strongest OOS lift, era-dependent

Screen 1 = higher-timeframe trend direction (SMA w rising: sma[t] >
sma[t−5]); screen 2 = the event fires. BUY events only when screen 1
is up; SELLs ungated (long-only strategy).

- Population OOS: **sma60-rising buys +3.00%@72.4%** vs base
  +1.84%@64.7% — the largest buy lift measured anywhere in this whole
  study (sma255 version: +1.98%, mild).
- Engine: reg120 OOS ca **3.80** (ret +23.2%, dd 6.1%);
  ma_cross+crowdedness OOS ca **4.85** (ret +26.8%, dd 5.5%); ALL8 OOS
  turns POSITIVE (+11.1%, ca 0.40) — the ONLY thing that made ALL8
  OOS-positive. But the IS side is NEGATIVE everywhere (reg120 −0.34;
  ma_cross+crowdedness −0.07; ALL8 −0.23) — the same era flip as
  Part 3's trendbuy60 (the IS era's edge was dip-buying; the screen
  suppresses it).
- Verdict: the best OOS economy in the study and the natural companion
  of regime_matched (level gate vs slope gate — regime_matched is
  IS-balanced / OOS good, triple_sma60 is OOS-dominant / IS bad). Do
  NOT ship either as an unconditional default; the trained-config
  refactor should carry the screen as an explicit regime-conditional
  param (and the IS/OOS disagreement itself is the warning).

### M3 Regime-switched quality weights — mixed

Per-bar weights per (family, regime) (regime = close vs SMA120),
learned from IS per-regime family edges. ma_cross+crowdedness: OOS ca
0.511 → 0.759 (better) with IS 1.87 → 1.44 (worse) — balance-improving
there; reg120: OOS 1.574 → 0.128 (kills the OOS edge). Learnt weights
again ≤ equal (consistent with Part 3's search failures) — the only
use is balance, and only sometimes.

### M4 Inverse-vol weights — no effect

Static weights ∝ 1/std(IS event returns): ≈ base or worse on every
scheme (reg120 OOS ca 1.574 → −0.233). Honest negative.

### What "combining signals" reduced to, empirically (Parts 3-5)

1. **Family SELECTION dominates everything** (Part 3): the ma_cross+macd
   pair, thesis-aware, beats every weighting scheme.
2. **Regime-matched gating** (Part 3) is the best robust comb form for
   the winner; **triple screen** (Part 5) is its OOS-heavy mirror.
3. **Meta-labeling at a loose threshold** (Part 5) is the measured way
   to lift the winner's weakest segment without touching dates.
4. Everything else the literature offers — tuned weights (Dirichlet,
   per-side, quality, IC-cousin, regime-switched, inverse-vol),
   voting/veto/confluence rules (netting already is one), entry/exit
   waiting, position overlays — measured ≤ equal on this data.
5. The vote/average/stacking family the literature recommends is
   LARGELY EQUIVALENT to what the collector already does (weighted
   netting IS the average; majority IS its sign) — the study confirms
   the collector's design and shows where the real edge lives
   (selection + regime matching + loose meta-filtering).

### Sources (online research behind Part 5)

- Meta-labeling: [waylandz AI Quant — Meta-Labeling Method](https://waylandz.com/quant-book-en/Meta-Labeling-Method);
  [What Works in Trading — Meta-Labeling](https://whatworksintrading.substack.com/p/meta-labeling-the-technique-that);
  [Medium — ML Meta-Labeling walkthrough](https://medium.com/mlearning-ai/momentum-trading-use-machine-learning-to-boost-your-day-trading-skill-meta-labeling-509f11d10184)
- Ensemble/stacking: [Mental Momentum Research — Ensemble & Stacking in Quant](https://research.mental-momentum.ai/r/ensemble-stacking-methods-quantitative-7xzgf4);
  [CFA Institute Research Foundation — Ensemble Learning in Investment](https://rpc.cfainstitute.org/research/foundation/2025/chapter-4-ensemble-learning-investment);
  [Build Alpha — Trading Signal Pools](https://www.buildalpha.com/trading-signal-pool);
  [Quant SE — combining signals](https://quant.stackexchange.com/questions/12792/how-to-combine-trading-signals-to-achieve-higher-capital-efficiency)
- Triple screen: [Investopedia — Triple Screen](https://www.investopedia.com/articles/trading/03/040903.asp);
  [Elder Triple Screen Method PDF](https://tradingwriters.com/wp-content/uploads/2026/05/Elder-Triple-Screen-Method.pdf);
  [Trading Setups Review — Elder Impulse System](https://www.tradingsetupsreview.com/trading-impulse-system-alexander-elder)

## Part 6 — the self-adaptive comb utilities (2026-11-17): no tuned params, common stats only

The user-directed follow-up: MORE comb utilities, parameters NOT
fine-tuned — self-adaptive by COMMON STATISTICS (conventional windows,
1-sigma bands, the repo's own floors/gate bars) — such that final
returns increase a lot vs the naive comb (equal-weight sum of
confidences). Scripts: `temp_scripts/sig_adaptive_study.py` +
`sig_adaptive_robust.py`; CSV `sig_adaptive_results.csv`. All utilities
are CAUSAL (trailing completed information only) and production-shaped
(they slot into the collector blend / per-family sig columns).

### The utilities

- **U1 rolling-edge family weights** — per family, the mean SIGN-ALIGNED
  realized forward return (the repo's dir_ave blend) of its own events
  whose 20d forward window COMPLETED before the bar and whose event
  date is inside the trailing window (one season = 91 calendar days, or
  one year = 365). Weight ∝ max(edge, 0), normalized per bar; families
  with < 5 completed trailing events (the repo's sample floor) inherit
  the mean known weight. The regime adaptation IS the statistic — the
  season-window snapshots show it working: mov_rsi edge −0.034 and
  bollinger −0.030 by 2025-10 (auto-de-weighted in the adverse regime)
  while rf_break/momentum/ma_cross rise to +0.03..+0.06 by 2026-06.
- **U2 quantile event sizing** — each event's recorded bp confidence is
  percentile-ranked within its OWN family's trailing-year events (the
  mov_rsi per-code percentile convention applied to confidence);
  size = 0.5 + 0.5×pct (the starvation-scale idea). Grounded by the
  diagnostic: recorded bp confidence DOES predict outcome — Spearman
  vs sign-aligned dir_ave: mov_rsi +0.63, momentum +0.42, bollinger
  +0.41, crowdedness +0.39, rf_break +0.30, macd +0.25, rebound +0.24,
  ma_cross +0.15 (all p≈0). **The production flat replay ignores
  evidence the analysis tier already stamps on every event.**
- **U3 vol-state exposure** — per code, EWMA(20) vol z-score vs its own
  trailing 255d (the stats.market_regimes convention, shifted 1): buys
  × 0.5 when z ≥ +1 (stress), × 1.25 when z ≤ −1 (calm), × 1 inside the
  1-sigma band. Exits never scaled.
- **U4 equity-curve guard (2-pass)** — pass 1 runs the U1..U3 comb; the
  strategy's own pooled daily PnL path gates pass-2 new buys: × 0.5
  while the trailing 60-trading-day slope is negative (a quarter
  window), shifted 1 day.

### Results vs the naive sum (ALL8, engine + regime losses)

| config | FULL ret / ca | IS ret / ca | OOS ret / ca / dd |
|---|---|---|---|
| naive_sum (equal sum) | −6.3% / −0.12 | +11.2% / 0.51 | −26.6% / −0.52 / 51.5% |
| U1_edge_season | −4.5% / −0.09 | +13.2% / 0.63 | −23.7% / −0.45 / 52.7% |
| U1+U2 | −1.8% / −0.03 | +14.2% / 0.58 | −24.2% / −0.44 / 55.0% |
| U1+U2+U3 | +3.6% / 0.06 | +11.9% / 0.46 | −20.7% / −0.39 / 53.4% |
| U1+U2+U3+U4 | +9.6% / 0.22 | +7.9% / 0.32 | −7.2% / −0.15 / 48.7% |
| **pair_reg120+U1+U2+U3** | **+36.9% / 2.38** | +11.9% / 0.65 | **+24.2% / 2.77 / 8.7%** |
| **pair_reg120+U1+U2+U3+U4** | **+36.6% / 2.53** | +12.0% / 0.57 | **+25.9% / 3.67 / 7.0%** |
| **mc_pair+U1+U2+U3** (ma_cross+crowdedness) | **+47.6% / 5.13** | +16.1% / 1.62 | **+22.7% / 2.17 / 10.5%** |

Selection + adaptation COMPOSE: the utilities alone on the naive
selection (ALL8) lift full return −6.3% → +9.6%; on the right
selection they deliver **+47.6% full / +22.7% OOS vs the naive sum's
−6.3% / −26.6%** — a ~54pp full-return and ~49pp OOS swing with ZERO
tuned parameters. Adaptation cannot rescue naive selection
(all8_reg120+U1: OOS −29.7% — gating + weights without selection still
fails), and selection without adaptation leaves the OOS swing on the
table (plain pair_reg120: OOS +16.5% vs +25.9% with the stack).

### Robustness

- Split stability (oos_frac 0.1/0.2/0.3/0.4): pair_reg120+U1U2U3 OOS
  ret +8.0% / +24.2% / +26.3% / +27.2% — positive in EVERY cut
  including the worst recent window where naive is −15.9%;
  mc_pair+U1U2U3 −1.3% / +22.7% / +33.1% / +36.2%. The naive sum is
  NEGATIVE at every cut (−15.9 / −26.6 / −15.2 / −11.0).
- Per-code OOS (0.2 split): pair 8/10 wins vs naive (3/10 negative);
  mc_pair 9/10 wins; naive 9/10 codes NEGATIVE.

### Implementation cautions (the two bugs this study hit)

- **Guard ordering**: an equity guard that scales the family sig
  columns AFTER the blend already computed signal_confidence is a
  NO-OP — the engine reads the blended column. The pass-2 guard must
  transform the columns BEFORE the final blend. (First run measured
  identical numbers with and without U4 because of exactly this.)
- **2-pass state must be keyed by values, not frame identity** — the
  pass-2 frames are copies; per-(code, date, family) dicts survive
  copying, id()-keyed caches do not.
- The U1 edge needs COMPLETED forward windows — an event only feeds the
  edge 20 traded days after firing; families late-starting codes
  (931594/931994) spend their first year on the floor fallback (equal
  weight), which is the safe default.

### Production shape (for the trained-config refactor)

These utilities are the self-adaptive form of the trained-config param
groups: U1 = the weights group (window convention = the param, no
searched values), U2 = the sizing group (uses the stamped evidence),
U3 = the regime overlay (1σ bands), U4 = the position-aware util done
RIGHT (it de-risks from realized PnL, passes the OOS Calmar bar — the
Part-2 D overlay failed because it scaled by POSITION STATE; U4 scales
by STRATEGY PERFORMANCE). Recommend the trained config carry: family
selection (Part 3) + the regime gate (Part 3) + U1 window {season,
year} + U2 on/off + U3 on/off + U4 on/off — six booleans/conventions,
zero continuous tuned params.

## Part 7 — the creative batch (2026-11-17): breadth, dynamic Kelly, stacking, comb-of-combs, crash stop

Seven more comb utilities from the online research (contrarian
capitulation breadth, rolling fractional Kelly, stacking ensembles),
all self-adaptive by common stats, on the same harness
(`temp_scripts/sig_creative_study.py` + `sig_creative_round2.py`; CSVs
`sig_creative_results.csv` / `sig_creative_round2.csv`). Baselines:
naive sum (−6.3% full / −26.6% OOS) and the Part-6 champions
(P6_pair +36.9% / +24.2%; P6_mc +47.6% / +22.7%).

### The utilities and what they measured

- **V1 breadth sizing** (contrarian capitulation breadth): cross-code
  event breadth per date (# codes with a same-side family event in the
  trailing week), buys × (0.5 + 0.5×breadth pct), sells × (1.5 −
  0.5×breadth pct) — market-wide capitulation = the strongest buys;
  late mega-cluster sells = the weakest exits (the Part-4 s4+ finding).
  On the finalists: P6_mc FULL +47.6% → **+48.2% ca 6.14**, IS 16.1 →
  17.3, OOS dd 10.5 → 9.0 — improves BOTH segments. A keeper.
- **V2 rolling relative Kelly** (the trainer's own KELLY_CAP 0.20 ×
  KELLY_FRACTION 0.25 on season-window event μ/σ², normalized to
  [0.25, 2.0]): ≈ no-op (P6_pair OOS ca 2.77 → 2.93) — the relative
  multipliers cluster near 1 because family edges are similar-scale.
  Honest near-negative: adds machinery, no return.
- **V3 EWMA edge weights** (halflife 10 events — faster adaptation than
  U1's windowed mean): pair OOS ca 2.77 → **4.51, dd 8.7 → 5.2%** at
  IS 6.7% — the best pure-OOS pair variant; the speed/adaptation
  trade-off is real (faster adaptation buys OOS robustness with IS
  balance).
- **V4 consistency weights** (edge × hit rate): ≈ P6 base. No-op.
- **V5 stacking combiner** (GBM/Ridge per side predicting sign-aligned
  dir_ave from the Part-5 features, filter at the NATURAL 0 bar — no
  threshold tuning): the GBM on naive selection shows the classic
  overfit signature (IS +28.0% / OOS −29.3%) — **stacking on naive
  selection learns the IS era's reversion edge and dies OOS**. The
  Ridge filter on the pair is balanced (IS 16.6 / OOS 16.0) but ≤ the
  unfiltered champion. Verdict: stacking does not beat the
  self-adaptive utilities here; if used at all, Ridge-class only, and
  the IS/OOS disagreement is the tripwire.
- **V6 comb-of-combs** (average the two Part-6 finalists' blended
  signals per bar): FULL **+50.0% ca 6.14**, IS +19.2% (vs 16.1 for
  the better member — diversification ADDS return), OOS +24.5% ca
  2.77. Adding the naive comb as a third member HURTS (OOS dd
  20.7%) — comb-of-combs must combine only champions.
- **V7 vol-scaled crash stop** (2-pass fixed point: exit when unrealized
  PnL < −2σ×√10 of position value, the code's own trailing 20d vol,
  ≥ min_hp rows after entry): 81–139 pass-1 stops converge to 3–9 and
  the engine results are IDENTICAL to the base — on signal-driven combs
  the stops never bind (signals already exit first). On the naive
  comb the OOS drawdown is a sustained adverse trend, not a discrete
  crash — stops do not fix it. Honest no-op; do not build for this
  strategy.

### The meta-combs (round 2) and the champion

| config | FULL ret / ca | IS ret / ca | OOS ret / ca / dd |
|---|---|---|---|
| naive sum | −6.3% / −0.12 | +11.2% / 0.51 | −26.6% / −0.52 / 51.5% |
| P6_mc (Part 6) | +47.6% / 5.13 | +16.1% / 1.62 | +22.7% / 2.17 / 10.5% |
| V6 mean(pair, mc) | +50.0% / 6.14 | +19.2% / 1.48 | +24.5% / 2.77 / 8.9% |
| **V8c mean(pair+breadth, mc+breadth)** | **+46.1% / 7.55** | **+18.4% / 1.69** | **+24.8% / 3.76 / 6.6%** |
| V8a mean(pair_ewma, mc+breadth) | +44.8% / 6.41 | +16.5% / 1.48 | +25.4% / 3.48 / 7.3% |
| V8b mean(pair, mc, pair_ewma, pair_cons) | +50.0% / 4.63 | +16.8% / 1.05 | +28.5% / 3.45 / 8.3% |

Robustness: every meta-comb is OOS-positive at every split cut
(worst window frac 0.1: naive −15.9% vs V8a +6.7%, V6 +4.8%); V8a
OOS ca 3.48 / 5.36 / 5.43 at fracs 0.2/0.3/0.4; per-code OOS: V8a
6/10 wins vs P6_pair with only 2/10 negative codes.

### The full ladder (what "combining signals" is worth, empirically)

| step | FULL ret | OOS ret |
|---|---|---|
| naive equal sum of all families | −6.3% | −26.6% |
| + selection (ma_cross+crowdedness) | +47.6% | +22.7% |
| + self-adaptive stack (U1-U3) | +47.6% → same selection | +22.7% |
| + breadth finishing | +48.2% | +23.3% |
| + comb-of-combs (V8c) | +46.1% (ca 7.55) | +24.8% (dd 6.6%) |

The jump from naive is worth ~52pp of full-series return and ~50pp of
OOS return with dd cut 51.5% → 6.6% — achieved with ZERO tuned
parameters (every constant is a calendar/season/year convention, a
1-sigma band, the repo's own floors/gate bars/Kelly constants, or a
natural zero bar).

### Sources (online research behind Part 7)

- Breadth / capitulation: [AAII — Arms Index (TRIN) for Market Breadth](https://insights.aaii.com/p/using-the-arms-index-trin-for-market);
  [Market Internals: TICK, TRIN & A/D](https://journalplus.co/learn/glossary/market-internals);
  [Investopedia — Capitulation](https://www.investopedia.com/terms/c/capitulation.asp);
  [Schwab — Breadth Check](https://www.schwab.com/learn/story/breadth-check-strength-and-weakness-trend-tracker);
  [Alpha Architect — Pitfalls When Assessing Market-Timing Strategies](https://alphaarchitect.com/pitfalls-when-assessing-market-timing-strategies)
- Dynamic Kelly: [TradersPost — Kelly Criterion Position Sizing](https://blog.traderspost.io/article/kelly-criterion-position-sizing-automated-trading);
  [ScienceDirect — Sizing the risk: Kelly, VIX, and hybrid approaches](https://www.sciencedirect.com/science/article/pii/S0950705126010579);
  [Quanterlab — Fixed, Volatility-Targeted, and Kelly](https://quanterlab.com/articles/foundations-position-sizing)
- Stacking ensembles: [CFA Institute — Ensemble Learning in Investment](https://rpc.cfainstitute.org/research/foundation/2025/chapter-4-ensemble-learning-investment);
  [scikit-learn — Ensembles](https://scikit-learn.org/stable/modules/ensemble.html);
  [IBM — What is Gradient Boosting?](https://www.ibm.com/think/topics/gradient-boosting)

## Part 8 — analysis-signal mutual overlap rate (2026-11-17)

Full overlap matrices at three granularities
(`temp_scripts/sig_overlap_matrix.py`; CSVs `sig_overlap_family.csv`,
`sig_overlap_subtype.csv`, `sig_overlap_schemes.csv`). Metrics:
containment both ways |A∩B|/|A| and |A∩B|/|B|, Jaccard, per-side
Jaccard, ±3-row soft overlap. NOTE: same-day same-side events carry the
IDENTICAL forward return by construction (the asset's own dir_ave), so
"quality correlation" is trivially 1 — the diversification metric is
the overlap rate itself.

### Family level (the strategy's pickup view)

- Same-day Jaccard peaks at just **0.139** (mov_rsi×bollinger); the
  reversion trio sits at 0.08–0.14; ma_cross×macd 0.051; and
  **mov_rsi×macd / bollinger×macd are EXACTLY 0.000** (zero same-day
  co-firing in the whole study). All 28 pairs < 0.14 — at DAY
  granularity the families are near-independent.
- The ±3-row soft metric exposes the TEMPORAL co-location inside a
  thesis: every bollinger event has a mov_rsi event within 3 rows
  (soft 1.00), every momentum event has one (1.00), every macd event
  has a mov_rsi event within 3 rows (1.00 — though only 9% of mov_rsi
  events sit near a macd event; the dense-into-sparse direction is a
  density artifact). Interpretation: theses co-locate in TIME, families
  co-fire on the SAME DAY rarely — the cluster-confirmation study
  (Part 4) measured exactly this structure.
- Buy/sell sides differ: rf_break has no sells (sellJ 0.00 vs
  everything), and its buy overlap with the reversion trio (0.06-0.11)
  is what makes it a distinct capitulation source.

### Sub-type level (within family, 456 pairs)

- Only **9 of 456** sub-type pairs reach Jaccard ≥ 0.5, 64 sit in
  0.15..0.5, **383 are < 0.15** — the sub-type inventory is largely
  independent populations.
- The measured twins confirm the biz-skill flags precisely:
  **mom5_1pct × momshort_1pct Jaccard 0.82** ("momshort ≈ mom5"),
  rf window neighbours rf60/120_floor_deep 0.69 (containment 0.89 —
  the smaller window's events nearly contained), rf252/504_roof_deep
  0.69, rsi10_1pct × rsi14_1pct 0.41. The nested-widest-first carving
  holds (window neighbours overlap but never merge).
- Practical: a comb that weights families is implicitly weighting these
  sub populations once each — the twins (mom5/momshort, rf window
  neighbours) are the only double-counting inside a family, and the
  carve already assigns each day to its deepest claim.

### Comb trade overlap (executed decisions)

- P6_pair's trades are **74% contained** in the naive ALL8's (Jaccard
  0.24) — the finalists are strict SUBSELECTIONS of the naive trade
  set, not different traders.
- **P6_pair × P6_mc Jaccard 0.35** (containment 0.75/0.40): 65% of
  mc's trades are unique to mc — the two finalists trade substantially
  different days, which is WHY averaging them (Part 7's comb-of-combs)
  diversified and improved Calmar rather than just re-weighting the
  same trades.
- Rule of thumb for the trained-config refactor: check the trade-set
  Jaccard of any two candidate combs before combining them — combining
  combs whose trade sets overlap > ~60% adds exposure, not
  diversification (the naive third member in Part 7's V6 test hurt
  exactly because of this).

## Part 9 — the risk study (2026-11-17): beyond return

The REPO'S OWN risk pipeline (`strategy._risks.compute.compute_risk_seq`
— the function the RiskRunner upserts: exponential rolling-window loss
score over 1/30/90/365d windows, losing-month streak penalty, per-period
distribution signals, concentration, top drawdowns, the
LITTLE/LOW/MODERATE/ELEVATED/HIGH grade) run IN-MEMORY per comb per code
(no DB writes), plus pooled-path metrics (Sortino, Ulcer, CVaR5,
underwater time, drawdown duration, profit factor, streaks, turnover).
Script `temp_scripts/sig_risk_study.py`; CSV `sig_risk_results.csv`.
(2026-11-20: the ORACLE mode is a second STRUCTURAL LITTLE route in
`_risk_grade` — the perfect-foresight replay is the risk floor regardless
of score; its score + factor rows stay computed as diagnostics. The
former oracle MODERATE was entirely the losing-month streak's +1.0
crossing the LOW bound, with the gain-dominated asymmetry discounts
floored at 0 giving no offsetting credit.)

### The official risk score separates naive from everything else 8-12×

| comb | score mean/max (FULL) | grades FULL | score mean (OOS) | grades OOS |
|---|---|---|---|---|
| naive_sum | **206.2** / 379 | 9 HIGH, 1 ELEV | 206.8 | 8 HIGH, 1 ELEV, 1 MOD |
| P6_pair | 21.3 / 59.8 | 10 HIGH | 25.9 | 4 HIGH, 2 ELEV, **4 MODERATE** |
| P6_mc | 27.0 / 138 | 9 HIGH, 1 ELEV | 75.1 | 4 HIGH, 2 ELEV, 4 MOD |
| V6 pair+mc | 20.3 / 84.5 | 9 HIGH, 1 ELEV | 72.1 | 6 HIGH, 2 ELEV, 2 MOD |
| V8a | 18.2 / 76.6 | 9 HIGH, 1 ELEV | 41.0 | 6 HIGH, 2 ELEV, 2 MOD |
| **V8c** | **16.7** / 56.7 | 9 HIGH, 1 ELEV | 47.6 | 5 HIGH, 2 ELEV, 3 MOD |

The official grade scale is absolute and punishing (every comb grades
HIGH full-series) — the RELATIVE 12× reduction naive → V8c is the
finding. P6_pair is the most DEFENSIVE OOS profile (the only comb with
MODERATE codes — its regime gate keeps it out of the market, OOS
time-in-market 91.8% vs 99.7% for the rest), but pays with the longest
full-series underwater duration (1264 trading days, Ulcer 8.9%).

### Pooled-path risk metrics

| comb (FULL / OOS) | Sortino | Sharpe | Ulcer% | CVaR5% cap | UW% | DD dur | PF* | LStreak | TiM% |
|---|---|---|---|---|---|---|---|---|---|
| naive | −0.07 / −0.81 | −0.06 / −0.73 | 7.4 / 8.9 | 2.9 / 6.5 | 94 / 84 | 930 / 96 | 3.4 / 4.0 | 37 / 10 | 99.9 |
| P6_pair | 1.10 / 2.39 | 0.77 / 1.60 | 8.9 / 2.9 | 1.6 / 2.2 | 96 / 84 | 1264 / 99 | 0.56* / 1.4 | 52 / 13 | 96.9 / 91.8 |
| P6_mc | 1.57 / 2.15 | 1.03 / 1.41 | 3.3 / 3.5 | 1.4 / 2.2 | 93 / 83 | 946 / 99 | 1.95 / 1.83 | 21 / 14 | 99.8 |
| **V8c** | **1.61 / 2.61** | **1.07 / 1.70** | 3.5 / **2.25** | 1.4 / **1.93** | 94 / 84 | 1216 / 95 | 1.24 / 1.31 | 25 / 18 | 99.8 |

\* PF is computed on per-exit RETURN RATES (unweighted by position
size) — P6_pair's 0.56 full with a positive total is the trend-following
profile: many small-rate losses on big positions, few large-rate wins
(payoff 1.04, win rate 34.8%, skew +0.73). Compare combs, not absolutes.

Key findings beyond return:

1. **V8c dominates the OOS risk surface**: best Sortino 2.61, Sharpe
   1.70, Ulcer 2.25% (naive 8.85%), CVaR5 1.93% of peak capital (naive
   6.52%) — while holding the best full-series official score (16.7).
2. **The naive comb's pathology is EXPOSURE, not trades**: 99.9%
   time-in-market, 37-trade losing streak, 930-day underwater duration,
   NEGATIVE Sortino/Sharpe — despite win rate 65% and right-skew +1.45
   (its returns come from a few tail episodes; everything between
   bleeds). Its HIGH × 9-10 grades are structural.
3. **Fat tails are structural**: per-exit kurtosis 5.9-8.1 on every comb
   (episode-driven populations) — no comb fixes kurtosis; position
   sizing (U2/U3) is the only lever, and it shows up in Ulcer/CVaR, not
   kurtosis.
4. **Turnover/fees**: naive 171-211 SELLs/yr (fee drag 1.21% of capital
   full) vs combs 120-200 (0.4-1.6%) — the adaptive stack does not
   raise turnover; the breadth/EWMA variants add ~10-20%.
5. **Trainer recommendation**: `compute_risk_seq` is a pure in-memory
   function — the trained-config refactor should run it inside the
   Set-B evaluation and carry the official risk_score as a SECOND hard
   constraint (or the tiebreaker) beside the DD cap: the combs differ
   8-12× on it while Calmar alone barely separates them.

## Part 10 — the wide-universe validation (2026-11-17): 508 indices

The champion combs re-run on the FULL index universe (508 codes with
>=252 OHLC rows, 841k bars, 352k pickup events — median 1668 rows/code;
crowdedness covers 407 codes) — the genuine OUT-OF-UNIVERSE test, since
every earlier part tuned and ranked on the 10 study indices.
Script `temp_scripts/sig_wide_study.py`; CSVs `sig_wide_matrix.csv` /
`sig_wide_risk.csv` / `sig_wide_percode.csv`. Scale-safe algorithm
replacements REQUIRED at 50x the event pool (keep these forms when
extending further):
- U1 edge series via DAY DIFFERENCE ARRAYS (+quality at
  completion_date+1, −quality at event_date+window+1, prefix-summed
  per family) — exact, O(events + dates); the 10-code insort version
  goes quadratic.
- U2 quantile sizing via per-(family, code) bisected date lists (the
  per-event full-family scan is O(n²) at this scale).

### Results (FULL / IS / OOS ret; OOS ca / dd)

| comb | FULL | IS | OOS | OOS dd |
|---|---|---|---|---|
| naive_sum | +7.3% | +12.2% | **−13.8%** | 17.1% |
| **P6_pair** | **+18.0%** | +10.3% | **+9.8%** | 12.1% |
| P6_mc | +15.2% | +9.8% | +0.2% | 11.1% |
| V6 pair+mc | +19.1% | +13.4% | +4.7% | 11.9% |
| V8a | +21.2% | +14.5% | +6.6% | 12.5% |
| V8c | +20.7% | +14.3% | +6.5% | 12.2% |

Per-code OOS return distribution (508 codes — the decisive statistic):

| comb | median | q25 | q75 | % codes positive |
|---|---|---|---|---|
| naive_sum | **−8.88%** | −19.5% | −0.4% | 18.9% |
| **P6_pair** | **+4.27%** | −0.7% | +11.2% | **66.1%** |
| P6_mc | −0.70% | −8.0% | +4.2% | 40.7% |
| V6 | +1.45% | −3.1% | +7.5% | 56.5% |
| V8a | +2.66% | −1.5% | +9.2% | 61.6% |
| V8c | +2.75% | −1.5% | +9.1% | 62.6% |

Official risk (per-code score): naive OOS mean 203.7 / **median 276**;
P6_pair OOS **median 4.9** (43 LOW + 104 MODERATE + 3 LITTLE = 150/508
codes below HIGH; naive has 11); V8a/V8c OOS median ~8.

### What transferred and what did not

1. **The naive-vs-adaptive story transfers completely**: naive is
   OOS-negative (−13.8%; median per-code −8.9%; 81% of codes negative)
   and every adaptive comb is OOS-positive — now measured on 498 codes
   the tuning never saw.
2. **The champion RANKING shifts**: P6_pair (regime-gated
   ma_cross+macd) is the wide-universe champion on every statistic
   (OOS ret, per-code median, % positive codes, OOS risk median), while
   **P6_mc's 10-index edge does NOT transfer** (OOS +22.7% → +0.2%;
   per-code median −0.7%) — crowdedness's coverage (407/508 codes) and
   10-code selection luck. The meta-combs are the robust middle: all
   three OOS-positive with positive medians even without mc.
3. **Selection must be validated on the wide universe before it is
   shipped into a trained config**: the 10-index champion (mc) is not
   the wide champion (pair). The trained-config refactor should treat
   the wide-universe run as the acceptance gate for any comb param.
4. The wide universe is a harder risk test: champion OOS dd ~12% (vs
   6.6-10.5% on 10 codes) — pooled-capital drawdowns aggregate 508
   names.

### Runtime reference

508 codes x 1668 bars: fetch+cache 23s, lookups 26s, 6 combs x 3
segments engine matrix ~2.5 min, per-code OOS + risk (compute_risk_seq
x ~6k calls) ~5.5 min — 9 min total on one host, with the two
scale-safe algorithms above. Everything else from Parts 3-9 runs
unchanged.

## Part 11 — the zero-weight filter study (2026-11-17): when to set a family's weight to 0

The U1 weight zeroes a family when its trailing completed-event edge
crosses the bar. This part studies the filter itself: predictive power,
lag, flapping, and the bar/window/hysteresis/floor variants — all bars
are the repo's own constants (0 and the 0.25% buy gate), all windows
calendar conventions. Script `temp_scripts/sig_zero_weight_study.py`;
CSVs `sig_zero_diag.csv` / `sig_zero_variants.csv`. Engine matrix: the
P6_pair transform on the WIDE universe (508 codes), the Part-10
acceptance metrics.

### Diagnostics: the window decides whether zeroing predicts or lags

- **Year window (365d) = PREDICTIVE**: mov_rsi zeroed periods have
  P(next-91d edge < 0) = **0.88** vs 0.30 when active — the year-scale
  edge captures persistent regime breaks (7 episodes, mean 81d zeroed,
  1.9 transitions/yr — stable).
- **Short windows (63/91d) = CONTRARIAN**: mov_rsi P(bad | zeroed)
  0.23-0.24 vs 0.43-0.41 when active — INVERTED. A season-window
  zeroing fires AFTER the damage (the trailing mean has already taken
  the loss) and just before the family's performance mean-reverts;
  zeroing then removes exactly the recovery trades. macd/bollinger
  flap hardest at short windows (8.7-9.9 transitions/yr at w63).
- Families that essentially never zero: momentum, rebound, crowdedness
  (0 episodes at every window — their season edges never went negative);
  ma_cross/rf_break rarely (the gate bars rarely bind).

### Variant matrix (P6_pair transform, wide universe, OOS acceptance)

| variant | OOS ret / ca / dd | per-code median / pos | trades (full) |
|---|---|---|---|
| naive (no filter) | −13.8% / −0.81 / 17.1% | −8.9% / 18.9% | 59,955 |
| soft bar0 w31 | +10.3% / 0.81 / 12.7% | +4.2% / 65.9% | 33,657 |
| soft bar0 w91 | +9.8% / 0.81 / 12.1% | +4.3% / 66.1% | 26,885 |
| soft bar0 w365 | +9.7% / 0.82 / 11.8% | +4.2% / 67.5% | 32,256 |
| soft bar0.25% w91 | +9.0% / 0.80 / 11.3% | +3.8% / 67.9% | 8,808 |
| hard bar0 w91 | +9.0% / 0.75 / 12.1% | +3.1% / 62.2% | 28,072 |
| hard bar0.25% w91 | +9.0% / 0.80 / 11.3% | +3.8% / 67.9% | 8,812 |
| **hysteresis w91 (zero < 0, restore > 0.25%)** | **+10.4% / 0.86 / 12.1%** | **+4.8% / 70.9%** | **14,480** |
| floor-half w91 | +9.8% / 0.80 / 12.3% | +3.9% / 67.9% | 38,282 |
| starved-zero w91 | +9.8% / 0.81 / 12.1% | +4.3% / 66.1% | 26,856 |

### Conclusions — the zero-weight rule for the trained config

1. **Zero the family when its trailing edge < 0; restore it ONLY when
   the edge recovers above the repo's +0.25% buy gate** — hysteresis
   beats every alternative on all three acceptance metrics (OOS calmar
   0.86, per-code median +4.8%, 70.9% of codes positive) at HALF the
   turnover of plain soft-zeroing. The mechanism: hysteresis suppresses
   exactly the flapping that the diagnostics show at short windows
   (8-10 transitions/yr) without locking a family out after a transient
   dip.
2. **Hard (binary) gates lose to soft weight decay** (hard_bar0 pos
   62.2% vs soft 66.1%) — a family near its bar should fade, not
   switch.
3. **Raising the bar to 0.25% cuts trades ~3x at equal return** —
   higher per-trade efficiency but lower OOS return (concentration into
   fewer families); ship it only as a turnover throttle, not an edge
   improvement.
4. **Window: 91-365 all near-neutral for the engine** (the pair's own
   regime gate does the heavy lifting), but the DIAGNOSTIC says the
   year window is the only one whose zero-state PREDICTS the next
   window's failure (0.88 vs 0.30); short windows are contrarian. If
   the zero filter is a standalone decision (not inside a gated comb),
   use the year window; inside a regime-gated comb, the season window
   plus hysteresis is fine.
5. **Floor-half (zeroed families trade at half weight) ≈ soft zero** —
   keeping a half-line open neither helps nor hurts; starved-zero
   (families with < 5 completed events start at 0) is ≈ neutral because
   by the time a comb trades, most families have enough history.
6. Every variant beats the naive sum by ~24pp OOS — the zero filter's
   EXISTENCE matters far more than its exact form; hysteresis is the
   best measured form of it.

### Re-study on the repo's conventional windows (20/60/252 TRADING ROWS)

The Part-11 windows were calendar conventions (31/63/91/182/365 days);
re-run on the repo's own trading-row windows (the 255→252 standard
year). Script `sig_zero_weight_study2.py`; CSVs `sig_zero2_diag.csv` /
`sig_zero2_variants.csv`. The edge series and sizing windows are now
union-calendar ROWS (the scale-safe difference-array form unchanged).

Diagnostics:
- **w=20: the zero filter NEVER fires** (0 episodes on every family —
  with FLOOR=5 a 20-row window rarely assembles a measurable edge) →
  w20 degenerates to near-equal weights; OOS per-code positivity 61.6%
  (worst window). A 20-row window is NOT a valid zero-filter window.
- **w=60: fires often and CONTRARIAN** (mov_rsi 28 episodes, macd 32,
  8-9 transitions/yr; P(bad|zero) 0.21-0.31 vs P(bad|active) 0.47-0.61
  for mov_rsi/bollinger) — zeroed before recoveries, exactly the
  whipsaw hysteresis was built for.
- **w=252: predictive for the persistent families** (mov_rsi
  P(bad|zero)=0.83 vs 0.34 active; stable 3.4 trans/yr) — the standard
  year window is the only conventional one whose zero-state predicts
  the next period. macd is INVERTED at every window (0.22-0.52 vs
  0.42-0.48) — its zeroing is contrarian; per-family zero-state
  diagnostics should gate the filter, not a blanket rule.

Variant matrix (P6_pair, wide universe):

| variant | OOS ret / ca / dd | per-code median / pos | trades |
|---|---|---|---|
| soft bar0 w20 | +9.3% / 0.74 / 12.6% | +2.9% / 61.6% | 41,088 |
| soft bar0 w60 | +10.0% / 0.81 / 12.4% | +4.5% / 67.5% | 19,924 |
| **soft bar0 w252** | **+10.1% / 0.85 / 12.0%** | **+4.8% / 71.5%** | 27,888 |
| **soft bar0.25% w252** | **+10.2% / 0.86 / 11.9%** | +4.8% / **71.9%** | **20,008** |
| hard bar0 w252 | +9.2% / 0.79 / 11.6% | +3.4% / 68.9% | 29,255 |
| hyst w60 | +7.3% / 0.73 / **9.9%** | +3.5% / 63.4% | 19,638 |
| hyst w252 | +9.4% / 0.82 / 11.5% | +3.7% / 68.3% | 25,977 |
| floor-half w252 | +10.6% / 0.81 / 13.1% | +4.1% / 64.4% | 39,050 |
| starved-zero w252 | +10.1% / 0.85 / 12.0% | +4.8% / 71.5% | 27,859 |
| naive | −13.8% / −0.81 / 17.1% | −8.9% / 18.9% | 59,955 |

U1-stack re-check (the Part-6 stack re-based): pair_U1_w252 OOS +10.1%
ca 0.85 on **3,663 trades** (w20: +9.3% on 8,900 — the year window cuts
pair turnover 59% at equal-or-better return); mc_U1 OOS +0.3-0.8% at
EVERY window — mc's 10-index edge does not transfer at any window
(consistent with Part 10).

Conventional-window conclusions (supersede the Part-11 calendar
conclusions where they differ):

1. **Window = 252 trading rows** (the standard year) — the only
   conventional window that is both engine-neutral-to-positive AND
   diagnostic-predictive; w20 is structurally inert, w60 flaps and is
   contrarian.
2. **Hysteresis matters only at flappy windows** (its w60 variant is
   the most defensive — dd 9.9% — but costs return; at w252 there is
   little flapping left to suppress and the plain soft bar ≥ hyst).
3. **Best balanced shipped form: soft bar-0.25% at w252** (OOS +10.2%,
   ca 0.86, 71.9% of codes positive, turnover −28%) — with the caveat
   that the 0.25% bar is a turnover throttle; plain bar-0 at w252 is
   the max-per-code-median form (+4.8%).
4. Keep the per-family zero-state diagnostic next to the config: a
   family whose zeroing measures contrarian (macd) should be excluded
   from the zero filter.

## Part 12 — mean/variance self-adaptive weights + filter (2026-11-17)

The Part-11 filter used the trailing edge MEAN only. This part makes
weights and the zero filter adapt to BOTH moments — the t-statistic
t = μ·√n/σ over the conventional 252-row completed-event window
(variance via a second day-difference array of q²), per-event Sharpe
μ/σ, t-hysteresis (deactivate t<0, restore t>1 — the repo's pooled t≥1
leg as the gate), and streaming EWMA moments (halflife 63 rows,
gap-decayed Welford). Script `temp_scripts/sig_moments_study.py`; CSVs
`sig_moments_diag.csv` / `sig_moments_results.csv`. Engine matrix:
P6_pair transform on the wide universe.

| variant | FULL ret / ca / tr | IS ret / ca | OOS ret / ca / dd | per-code med / pos |
|---|---|---|---|---|
| naive | +7.3% / 0.30 / 60.0k | +12.2% / 0.68 | −13.8% / −0.81 / 17.1% | −8.9% / 18.9% |
| mean_w252 | +20.4% / 0.99 / 27.9k | +13.0% / 0.74 | +10.1% / 0.85 / 12.0% | +4.8% / 71.5% |
| mean_bar25_w252 | +20.4% / 0.99 / 20.0k | +13.4% / 0.77 | +10.2% / 0.86 / 11.9% | +4.8% / 71.9% |
| **tstat_w252** | **+20.7% / 0.99 / 26.9k** | +13.6% / 0.76 | +10.1% / 0.85 / 11.9% | **+4.9% / 72.2%** |
| tstat_bar1_w252 | +20.9% / 0.99 / 26.1k | **+13.7% / 0.77** | +10.1% / 0.85 / 11.9% | +4.9% / 71.9% |
| sharpe_w252 | +20.5% / 0.99 / 27.9k | +13.2% / 0.74 | +10.1% / 0.85 / 12.0% | +4.7% / 71.5% |
| t_hyst_w252 | +17.6% / 0.96 / 29.2k | +10.7% / 0.67 | +9.2% / 0.79 / 11.5% | +3.4% / 68.9% |
| ewma_sharpe_h63 | +9.5% / 0.96 / 19.9k | +3.6% / 0.49 | +7.7% / 0.84 / **9.2%** | +3.8% / 68.5% |
| ewma_mu_h63 | ≈ ewma_sharpe | ≈ | ≈ | ≈ |
| mc_mean / mc_tstat_w252 | +17.9/+18.9% | +14.5/+15.2% | +0.3/+0.5% | −0.2% / ~45% |

Findings:

1. **t-stat weights are the best balanced shipped form** — the
   variance term helps at the margins (per-code median +4.9% vs +4.8%,
   positivity 72.2% vs 71.5%, IS +13.6% vs +13.0%, 1k fewer trades)
   and one statistic implements BOTH the weight and the zero filter
   (t ≤ 0 → weight 0). tstat_bar1 (only MORE significant than the
   repo's pooled floor earns weight) is the best IS variant with the
   same OOS profile.
2. **Binary hysteresis on t loses to the graded t fade** (OOS +9.2% vs
   +10.1%) — significance-weighted fading is better than switching.
3. **EWMA moments (halflife 63 rows) are the DEFENSIVE variant**: they
   trade the IS era away (IS +3.6%) for the best OOS drawdown of any
   weight variant (9.2% vs 11.9%) at OOS Calmar parity (0.84). The
   fast/slow adaptation trade-off from Part 7's V3 confirmed at wide
   scale. sharpe_w252 ≈ mean_w252 (per-event σ barely reorders the
   families beyond what the window already does).
4. **The t-filter diagnostics at w252**: mov_rsi stays predictive
   (P(bad|t≤0)=0.83 vs 0.34), macd stays INVERTED (0.22 vs 0.42) —
   consistent with Part 11; per-family zero-state gating remains the
   standing caveat. Flapping is modest (3.2-3.6 transitions/yr).
5. **The snapshots show the adaptation working**: mov_rsi t +2.6
   (2022-04) → +15.7 (2024-08) → −11.5 (2026-11); momentum +30.8 →
   +33.9 → +32.7 (always significant); crowdedness +33-43 throughout —
   the t-stat swings far harder than the mean (significance, not
   level), which is exactly why noisy families fade.
6. **mc is moments-invariant**: t-stat does not rescue it (OOS +0.5% vs
   +0.3%) — its failure is selection (Part 10), not weighting.

Recommendation for the trained config: **w_fam ∝ max(t_fam, 0) at the
252-row window** (tstat_w252), with the 0.25% bar as the turnover
throttle where needed, the EWMA-halflife-63 form as the defensive
alt when drawdown matters more than IS balance, and the per-family
zero-state diagnostic (macd inverted) kept beside the config.

## Part 13 — the SHIPPED implementation (2026-11-17)

Parts 3-12 are now PRODUCTION code (UI-verified the same day):

- **`strategy/factors_and_algos/_comb/`** — the segregated comb package:
  `config.py` (pydantic `CombParams` + `DEFAULT_COMB` = the champion:
  gate120 + tstat@252 + U2(365d) + U3 + U4, breadth off; tolerant
  `coerce_comb`; `resolve_comb` = the ONE precedence point code < DB
  row < `--comb-json`), `data_definitions/` (SignalEvent / WeightTable /
  GuardState), `events.py` (extraction + dir_ave quality + union
  calendar), `edge.py` (difference-array moments + EWMA streams, mode
  dispatch equal|mean|mean_bar|tstat|tstat_bar1|ewma),
  `transforms.py` (RegimeGate BUY-only thesis sets / QuantileSizing /
  VolStateScaling / BreadthFinisher), `guard.py` (EquityGuard 2-pass
  state), `pipeline.py` (the ONLY orchestrator; stage order documented).
  Import rules: collector imports ONLY the facade; guard/pipeline never
  import the engine; config sourcing stays in portfolio/__main__.
- **Collector seam** (`algo_signal_collector.py`): `_blend_signals`
  delegates to CombPipeline when `params["comb"]` is enabled (bp stash
  from `hist_confidence`; `algo_w_<fam>` per-bar weight columns;
  re-entry hygiene drops stale `algo_conf_*`/`algo_w_*` for the FT
  stress re-run); `run_backtest` drives the U4 2-pass via the in-memory
  `_comb_guard` params key and reads `conf_threshold` from params;
  `build_signal_reason` uses per-bar `algo_w_` weights (UI `__MIX__`
  shape unchanged, 103 distinct weight pairs observed on 000001).
- **`--comb-json`** CLI arg on `python -m strategy.singleton_trading`
  (validated at startup; `{"enabled": false}` restores the equal blend).
  Comb is DEFAULT ON for all mixed runs.
- **Trainer (Part 9 shipped)**: `_optm_engine/risk_score.py`
  (`official_risk_score`, in-memory `compute_risk_seq`) wired into
  `evaluate_set_b`; `_preference` = (rank, loss, risk_score);
  `persist.upsert_best_params` stamps `params_version: 2`. Verified:
  1440/1440 Set-B trials carry risk_score; winner logs
  `official_risk=1.3622`.
- **UI**: `TrainConfigsDialog.fmtVal` renders nested params as JSON;
  `algo_configs.sql` params comment documents the comb block.
- **Port-fidelity evidence** (`temp_scripts/comb_port_check.py` +
  `comb_ui_reference.py` + `comb_db_compare.py` + `comb_reason_check.py`
  + `comb_train_check.py`): study-harness vs production A/B identical
  except the early-history mean-known fallback scope (study spans the
  8-family census, production the selected families — 2-3 rows per
  code, Feb-Mar 2020 only, ≤0.1pp return impact); U4 guarded dates
  795/795 overlap; the UI-driven run's 178 decisions are BIT-EXACT vs
  the reference; single-code 000001 champion run = 79B/99S, buy cost
  335.93, pnl/capital +11.23%.
- **Deviation discovered**: the study harness SQL predates the reject
  tier — production correctly drops negative-confidence rows
  (`confidence >= 0`); 54 macd reject rows on 000001 now exist. Any
  future study re-run must filter them or it will diverge from
  production.
- **cudf note**: the strategy fetch frames' OBJECT python-date columns
  (pre-existing `rows_to_df` contract) keep this pipeline CPU-fallback
  heavy; the comb adds only small classes (`ewm().std()` unsupported →
  CPU; du `drop_duplicates`/`merge` slow paths) while REDUCING total
  fallback count vs the equal blend. NEVER build lookup frames from
  python-date lists (cudf auto-converts ctor datetimes to `[s]` and the
  merge against the object column raises) — build them from the big
  frame's own (code, date) columns.

## Part 14 — the Set C anti-collapse funnel + mixed Set B trim (2026-11-17, UI-verified)

The first mixed training (run 18, macd+mov_rsi on 000001) exposed two
trainer defects, both fixed and re-verified from the UI the same day:

1. **IS-Calmar top-K collapse** — plain top-K filled every slot with
   near-identical gate-OFF cells (run 18: cells 66-85 were all
   equal/ewma + gate-off + vol-on; the thr 2.5/5.0 cells measured
   IDENTICALLY — 100/100 same trades), so the regime-gated thesis —
   the study's strongest measured comb form — never reached the OOS
   stage that is supposed to choose between theses. Winner degenerated
   to a near-naive cell (OOS ret −9.4%, official risk 192).
   **Fix (`trainer.optimize_set_c`)**: STRUCTURAL DEDUPE (one variant
   per weight-mode × gate × vol-state; thr variants collapse, ties
   prefer thr 5.0) → 90 cells become 30 variants; then GATE-DIVERSE
   TOP-K (the best variant of EACH gate family off/120/255 takes a
   slot first, breach-ranked leaders skipped, remainder by global
   rank). IS prunes WITHIN a thesis; the OOS Set-B stage chooses
   BETWEEN theses — exactly the Parts 3/5 warning made structural.
2. **Mixed Set-B grid waste** — `buy/sell_exec_delay` are NO-OPs on
   the collector path (only `AlgoBase.run_backtest` shifts per-family
   signals), so 280 of 288 grid points were duplicates. **Fix
   (`space.set_b_grid(mixed=True)`)**: the mixed grid is the
   `min_holding_period` axis only (8 points); persisted Set-B keys
   simply lack the delay keys — the loader's 0/0 defaults reproduce
   the graded behavior.

Measured after the fix (000001, ft 10, fee 0.002, runs 21/22 + UI Run
Strategy; trainer wall time 5.5 min for 2 families / 11 min for 10 —
was 33+ min):

| portfolio | naive equal blend | trained (funnel fix) |
|---|---|---|
| ma_cross+macd | +13.62% (242 dec, cap 713) | **+15.40%** (163 dec, cap 353; winner mean_bar/gate120/thr10/hp7) |
| all-10 families | +17.88% (456 dec, cap 928) | **+27.28%** (250 dec, cap 277; winner equal/gate120/vol/U2+U4/thr5/hp13) |

Both trainings picked a GATE-120 winner with every gate-off candidate
grading negative OOS — the funnel is what lets the OOS stage see the
gated thesis at all. UI basis: trained all-10 +26.36% vs naive +16.83%
on the same page. The trained row is the complex nested comb JSON
(`params.comb` + `config_source` provenance on the run identity,
`params_version: 3`).

Operational notes: the auto-run after training can fail with an empty
HTTP 500 when GPU VRAM is exhausted (the strategy pre-check needs ≥16
GiB free; the resident `server_cluster.llm` host holding ~30 GiB is
the usual holder — `POST /v1/models/{id}/offload` on 8767 frees it);
re-fetch the exact run URL or click Run Strategy again. A hard-killed
training leaves its `training_runs` row 'running' — flip it to
'failed' by hand (one-time temp script).

## Part 15 — the ad hoc action module (2026-11-18): self-proposed trim

The strategy's SELF-PROPOSED actions — buy/sell the singleton strategy
proposes itself on bars where NO family signal fired (user-directed;
first action = the position-aware, profit-aware TRIM). Production code
in `strategy/factors_and_algos/_comb/adhoc.py` +
`data_definitions/trim_action.py` (`TrimAction`), config group
`CombParams.adhoc_actions` (`AdhocActionsCfg`), collector seam =
`AlgoSignalCollector.run_backtest` → `_apply_adhoc_actions`; UI reason
prefix `__ADHOC__<json>__<human>` parsed by `data_viz` `mixReason.ts`.

Semantics (defaults; every constant a config field):

- Position unit: lots = total_qty/100 (one full-confidence BUY);
  "multiple buys" ⇔ lots > 1. Per-code book.
- Profit state (causal, pass-A daily rows): pnl_ratio = total_pnl /
  capital_deployed (realized + unrealized MTM).
- Profit-aware cap: pnl_ratio < 0.05 (loss OR little gain) → 1.0 lot;
  ≥ 0.05 (healthy) → 2.0 lots.
- Trigger (small rise once stable, all trailing): 0 < r5 ≤ +3%
  (K=5 window) AND close ≥ SMA5 AND vol5 ≤ its own 60-row median.
- Trim: sells min((lots−cap)/lots, 50%) as an injected negative
  `signal_confidence` (partial SELL — engine sells conf/100 of the
  book); conf must clear the blend conf_threshold (natural
  dead-band); cooldown 5 rows; family bars never overridden (zero-signal
  bars only); engine min_holding_period still gates.
- 2-pass protocol: pass A = engine on the final blended+tuned frame
  (stub reasons) → per-code daily rows → SHADOW walk (shadow
  qty/pnl updated per planned trim — consecutive trims size against
  the already-trimmed book) → inject → final engine run (wrapped
  reason fn renders the `__ADHOC__` payload).
- Approximation (documented): the shadow models only the trims'
  effect; family BUY/SELL decisions are identical between the passes
  (a SELL-only injection never changes buys) — family sells sell a
  fraction of a slightly different absolute qty in the final pass.
  Same genre as the equity guard's 2-pass.
- FT interaction: trim bars carry ft_stressed_conf 0/0 (the stressed
  re-blend has no family signal on those bars — annotation only,
  baseline decisions unchanged).

Design grounding: the Part 2/3 position-STATE overlay failed because
its buy encouragement added exposure in falling markets; this module
never buys and scales the cap by STRATEGY PERFORMANCE (the U4 lesson —
sell into strength only). SELL-only-into-strength ≈ scale-out
discipline; the loss-state low cap is the anti-martingale convention.

Standing cautions:

1. Default ON for mixed Run Strategy runs (user direction); the
   trainer's Set C grid pins `adhoc_actions.enabled=False` — the
   training landscape stays frozen at the Part 10-14 stack until the
   module beats the no-overlay OOS Calmar bar (Part 2 D). Re-enable
   in the grid only after that study.
2. The module is NOT yet OOS-validated (no Part-3-style engine matrix
   has measured it) — treat any regime where it underperforms as a
   candidate for tuning the cap tiers via `--comb-json`, not for
   removing the guards (dead-band/cooldown/min_hp are the Part 2 D
   churn guards).
3. Disable per run: `--comb-json '{"adhoc_actions": {"enabled":
   false}}'`; per config: the `adhoc_actions` group in the
   algo_configs row (resolve_comb precedence code < DB < CLI).
4. UI-verified 2026-11-18 (Run Strategy, 000001 index, all-10 mixed,
   ft10, seq 17972): 124 buys UNCHANGED, sells 126→127 — the one new
   row is the trim (SELL 2020-05-19, qty 27.47 of 127.47 units =
   1.275 lots, cap 1.0 at pnl +1.9% < the 5% bar → LOW tier, r5
   +0.24%, sell frac = overshoot = 21.5%, realized +0.41 — sold into
   the rise, position lands at exactly 100 units = the cap). Total
   return on that single-code path +26.36% → +24.49% (the trim's
   compounding cost — the honest price of the overlay; judge it on
   the OOS Calmar bar above, not this one path). The run's stored
   `params.comb.adhoc_actions` carries the full group; the
   `__ADHOC__` reason renders via the extended `mixReason.ts` parser
   (DecisionTable's no-mix fallbacks now show the parsed human tail,
   not the raw prefix).

## Part 16 — the REJECT-tier offset check + refactor (2026-11-18)

The user question: can a NO-ACTION rejection signal (the analysis
tier's negative-confidence rows, engines/reject.py) have a NEGATIVE
effect by CANCELLING other families' positive signals — and is that
offsetting optimized in the training model? Studies
`temp_scripts/reject_census_singleton.py` +
`reject_offset_study.py` + `reject_transfer_robust.py`; per-event CSV
`reject_offset_results.csv`; functional checks
`reject_refactor_check.py`.

### The check answers (verified in code + live DB)

1. **Today rejects CANNOT cancel anything**: the ONE guard
   `confidence >= 0` at `analysis_source.fetch_signal_data` drops them
   before any blend — binary, mixed, comb and trainer all flow through
   that fetch. Netting only happens among picked-up positive-side
   events.
2. **The guard was load-bearing and fragile**: `apply_signals` signed
   by ACTION alone — a leaked "no-buy" reject would have replayed as a
   full-size BUY (sign semantics live in the confidence SIGN). Fixed:
   the replay mask `(is_buy|is_sell) & hist_confidence.fillna(-1) >= 0`
   (sign-safety, defense in depth; bit-identical while the guard
   holds — checked PASS on real frames).
3. **The offsetting was NOT trained** (no cancel param anywhere in
   Set A/B/C) — because no cancel semantics existed.
4. **The tier is in soft-launch**: live census 0 rows (the −2.0%
   high-confidence bar + the 20-row birth probation; negative cells
   only started recording 2026-11-16). S1 projection from
   history_signal_performance: ~6.7k cells clear the −2.0% bar
   index-scope (164 on the 10 study codes) and age out of probation ≈
   week of 2026-12-31 — mass: mov_rsi sell 3117 / bollinger sell 1207
   / rf_break buy 993 / mov_rsi buy 784 / bollinger buy 423 / macd
   168. The question goes LIVE then.

### The transfer study (would cross-family offsets help or hurt?)

Historical per-bar reject state is NOT reconstructible (negative
cells emitted nothing before 2026-11-16), so the measurable necessary
condition: does one family's negative trailing side-state (t ≤ 0 over
its own completed events, w252, per-code) transfer to OTHER families'
same-side events? 7,349 events, 10 indices:

- **BUYS get BETTER under exposure**: 0 negative others +1.89%@68.1%
  → ≥2 others +2.90%@71.5%; era-stable (early +1.20→+1.57%, late
  +2.30→+5.26%@81.5%). A per-code reversion washout is the
  capitulation context where complementary families' buys do BEST —
  a cross-family reject veto would cancel exactly the best buys. The
  user's suspected negative effect is REAL.
- **Sells flat** (no relation either way). **Own-family control**:
  t_own≤0 buys +1.995% ≈ t_own>0 +1.843% — even family-local per-bar
  suppression keyed on the winners' trailing state shows no
  event-level edge (the family-level tstat WEIGHT is the form that
  works — pooled, Part 12).
- Pooled-across-codes state (R2/R3): mostly no exposure; the one
  family with mass (bollinger's whole-family bad era, n=58) reads
  worse — family-WIDE bad eras ≠ per-code washouts; reject rows are
  per-cell (per-code) evidence, the per-code reading is the relevant
  one.

### The shipped design (measured-grounded)

- **Cross-family reject offsets are STRUCTURALLY EXCLUDED** — not a
  mode, not a config field, not a grid axis. Never re-add without new
  measurements contradicting the transfer study.
- **Family-local same-side self-suppression** is the only legal
  semantics: `_comb/reject.py` `RejectOffset` (pipeline stage 0,
  pre-gate, after the EventDataset build — the edge statistics learn
  from the UNSUPPRESSED population, so weight-fading and rejection
  stay independent). Config group `CombParams.reject`
  (`RejectCfg.enabled`, DEFAULT OFF — the adhoc precedent: dormant
  seam while unmeasured). A BUY reject zeroes the family's own
  positive `sig_` that bar; the opposite side and other families are
  untouched; nothing is ever invented.
- **The fetch CARRIES rejects as evidence**: a second DISTINCT-ON
  LEFT JOIN (`rej_action` / `rej_confidence` columns) — the positive
  pickup join untouched (bit-identical `hist_*`), the replay
  sign-safe. The collector stashes them per family (only when
  `reject.enabled` — zero default-path overhead) into
  `CombPipeline.blend(rej_stash=...)`.
- **The trainer optimizes the offsetting exactly when there is
  evidence**: `set_c_comb_grid(has_reject_rows=...)` — the trainer
  computes availability from the fetched frames
  (`NestedTrainer._reject_evidence_present`); with rows present every
  cell gains a reject-ON twin (90→180 cells; the structural-dedupe
  key gains the reject flag) and a reject-ON winner must beat its
  reject-OFF twin through the SAME preference/DD-cap/OOS funnel (the
  Part 2 D acceptance bar, structural). With no rows (today) the grid
  is UNCHANGED.
- Verified: `reject_refactor_check.py` A-D all PASS (bit-identity,
  sign-safety, offset semantics, end-to-end blend). UI-verified the
  same day (000001, all-10 mixed, ft10): force re-run through the
  strategist reproduced the stored run EXACTLY (+26.19%, 120B/124S,
  P&L +66.76, cost 245.59 — behavior-neutral default path); UI Train
  Model run 25 (started after the edit) logged the new `rej=off`
  cell axis on the UNCHANGED 90-cell grid with every cell calmar
  identical to the pre-edit run 24 (winner trial#55 both), and the
  trained row persists `comb.reject = {"enabled": false}`.

### Standing cautions

1. **Re-validate when real rows flow (~2026-12-31)**: the transfer
   study is a necessary-condition proxy (winners' trailing state, not
   failed cells' per-bar rejects — a different, unmeasurable-today
   population). Once cells age out, re-run the engine matrix for
   `reject.enabled` on/off before trusting any trained reject-ON
   winner; watch `n_trades` for population collapse.
2. Keep the SQL guard AND the apply mask together — the invariant is
   "a reject is never replayed" enforced twice (fetch + apply).
3. Binary mode has NO reject behavior (comb-stage feature, like
   vol_state/guard) — a single family's rejects cannot affect its own
   binary replay.
4. Study harnesses MUST filter `confidence >= 0` on positive pickups
   (the Part 13 deviation note, now doubled by the reject mirror
   join).

## Part 17 — the NO-ACTION overlay mirror (2026-11-18)

The day-level sibling of Part 16, shipped the same day (user-directed
"ensure the no action can get used in singleton strategy"): the
NO-ACTION overlay marks (`analysis_signals.no_action_signals` —
"do NOT take this action at this price" ON FIRED POSITIVE DAYS,
`engines/no_action`, the sibling skill `signals-no-action`) joined the
strategy through the SAME five seams, verbatim:

- **Fetch** (`analysis_source.py`): a THIRD own-DISTINCT-ON LEFT
  JOIN (`na_action` / `na_confidence` from `no_action_signals`; no
  portfolio axis, negative-bp by CHECK). The positive pickup and the
  reject mirror are untouched — bit-identical.
- **Offset** (`_comb/no_action.py`): `NoActionOffset` — a mark on
  (code, date, family, side) zeroes the family's OWN same-side
  `sig_<family>` that bar. The semantic difference from a reject: a
  mark BY CONSTRUCTION sits on a FIRED day, so the suppression
  cancels the marked fired event itself — exactly the tier's claim
  (the calibrated boll_squeeze_breakdown: marked fired buys −0.27%
  vs kept +2.24% aligned blend, monotone in the squeeze cut).
- **Config**: `CombParams.no_action` (`NoActionCfg.enabled`, DEFAULT
  OFF — the dormant-seam precedent), tolerance via `coerce_comb`,
  `resolve_comb` precedence unchanged.
- **Pipeline**: stage (0b), immediately after `RejectOffset` —
  pre-blend pre-gate, AFTER the EventDataset build (edge stats learn
  from the unsuppressed population). The two offsets are independent
  and idempotent (both zero the same side).
- **Trainer**: `set_c_comb_grid(has_reject_rows=...,
  has_no_action_rows=...)` — nested rej×na twins (both evidences
  present ⇒ 90→360 cells); `_no_action_evidence_present` gates on
  non-null `na_action` in the fetched frames; `_cell_group` and the
  progress log carry the `na=` flag. An ON winner must beat its OFF
  twin through the same DD-cap/OOS/official-risk funnel.

Standing cautions (inheriting Part 16's, plus the tier's own):

1. **Cross-family no-action offsets are structurally absent** — the
   Part-16 transfer finding applies verbatim (cross-family vetoes
   cancel the best buys), and the tier is family-local by design.
2. Binary mode has NO no-action behavior (comb-stage feature).
3. The mark population is ONE calibrated rule (boll_squeeze_breakdown
   on bollinger, ~5% of that family's fired days). Re-run the engine
   matrix when the tier's active set grows (`NO_ACTION_ACTIVE_RULES`)
   before trusting trained na-ON winners on new rules; watch
   `n_trades` for population collapse.
4. The lowest-sub_type-wins DISTINCT tiebreak applies to the na
   mirror like the other two joins — a mark keyed at a losing
   sub_type still suppresses that (code, date, side) family cell
   (the day-level context claim covers the family's same-side event).
5. Study harnesses reusing the fetch now carry `na_*` columns —
   positive-pickup filters are unaffected (the marks never touch
   `hist_*`).

**2026-11-18 same-day update (user-directed "set model config to
adapt the no action")**: `DEFAULT_COMB` now carries
`no_action=NoActionCfg(enabled=True)` — the STANDING mixed strategy
suppresses marked fired events out of the box; the `NoActionCfg`
schema default stays OFF (stale/coerce fallbacks and the trainer's
own cell constructors unchanged), and a trained algo_configs row
still overrides group-wise via `resolve_comb` precedence
(code < DB < CLI). First UI verification (training run 30,
000991 · bollinger+macd): the fetched frames carried marks
(6 `boll_squeeze_breakdown` rows) so the Set C grid grew 90→180
with `na=on` twins (`rej=off` constant — the reject tier is still
empty); the na-OFF twin won through the same DD-cap/OOS funnel and
the trained row (`params_version` 4) persists
`comb.no_action {enabled: false}` — the axis works (explored +
persisted); the verdict is per-population, and the spawned run-once
(seq 18055, `config_source` = the trained row) plus the
TrainConfigsDialog rendering verified the full chain.

## Part 18 — the signal-amplification path, measured (2026-11-19): shipped no-op on the study scope, the strength sweep, and the verdicts

The SIGNAL AMPLIFICATION tier (built 2026-11-18,
`analysis/analysis_signals/signal_amplification/` — the close-only
families' OHLCV-anatomy strengthener: flush_washout / gap_reclaim /
breakout_confirm / spike_blowoff / exhaustion_gap AMPLIFY, dry_rebound
DISCOUNTS; `amplify_bp = base_bp × (1 + 0.5·min(depth, 2))` capped
±600bp; the AMP_BINDINGS matrix binds 9 families; evidence per
(code, family, amplifier) on the rules rows, n ≥ 3 + delta sign =
`is_valid`) enters the strategy as comb stage 3b
(`_comb/amplifier.py`): the fetch carries the per-(code, date) Σ
amplify_bp per side (`amp_bp_buy` / `amp_bp_sell` — a dedicated
JSONB-lateral mirror, bit-identical positive pickup), the collector
stashes it when `comb.amplifier.enabled` (DEFAULT ON, schema v4,
`resolve_comb` precedence), and the transform scales the family's OWN
signed sig by `1 + clamp(Σbp/2000, [×0.5, ×1.5])` (the U3 band
convention) before the weighted blend — per-event evidence sizing,
family-local both sides, never weights, never dates. The engine
converts confidence to size (BUY qty = conf; SELL conf/100 of the
book) — the "amplified signal carries more size" channel. The trainer
does NOT explore it (Set C cells inherit the ON default; no
reject/no-action-style twin axis). Port parity shipped with the stage:
`temp_scripts/comb_amp_check.py`.

### The yield study (`temp_scripts/amp_yield_study.py` + `amp_yield_*.csv`, 2026-11-19)

The Part-3 harness verbatim (production fetch + CombPipeline champion
stack + 2-pass equity guard + engine + regime losses +
`compute_risk_seq`; the 10 study indices; parity vs the fetch mirror
9,946 day rows / 0 mismatches). Variants: off / blanket (shipped) /
valid (only evidence-`is_valid` bindings contribute bp) / valid_n5
(is_valid AND n ≥ 5).

1. **The mass is thin and the evidence mass is ~zero on the study
   codes**: 702 of ~6,400 event bars (~11%) sit on an amplifier day;
   17/155 (code, family, amplifier) bindings are evidence-valid, and 6
   of 9 families have literally 0% valid bp mass (ma_cross 25.6% /
   momentum 30.2% / macd 9.8% are the only ones with any) — the
   tier's 698 valid configs live mostly on the ~500-code universe,
   not the 10 study codes. Typical per-family factors ×1.06–1.15 (the
   ±600bp cap almost never binds).
2. **Blanket (shipped) is a measured NO-OP**: blended |conf| moves
   ≤ ×1.08 (p90) on amp-day events — the netting blend divides by ALL
   families' weights, so a per-family ×1.1 dilutes to ≤ ×1.08
   consolidated; 2 killed / 3 created at the 5.0 dust gate; engine
   ±2 decisions of 1,321; OOS calmar −0.214 → −0.213. Passes the
   Part 2 D no-harm bar BY ROUNDING — no harm, no yield.
3. **The strict validity gate FAILS as an improvement** (OOS calmar
   −0.216; 0/10 per-code wins): the gate strips UNMEASURED mass
   (validity 0% = never assembled n≥3 on these codes, not disproven)
   and lands on the off baseline. The optimization ceiling
   (blanket vs off) is itself noise, so NO gate variant can matter on
   this scope.
4. **A trainer ON/OFF axis is NOT justified** (the Part 16/17
   availability-gated twin pattern pays only when the axis has mass):
   an ON winner would have to beat OFF between configurations that
   differ by ±2 trades — grid compute spent selecting noise.

### The strength sweep (`temp_scripts/amp_strength_study.py` + `amp_strength_*.csv`, 2026-11-19, user-directed "more buy and sell means confidence therefore more quantity")

Same harness; the shipped bp→factor mapping swept by patching the
`_comb/amplifier.py` module constants per variant (study-only,
restored in finally — `_factor` reads AMP_FACTOR_DIVISOR / FLOOR / CAP
at call time; they are NOT config fields): s1 shipped (div 2000, band
[0.5, 1.5]) / s2 (1000, [0.25, 1.75]) / s3 (500, [0.10, 2.0]) / s4
(250, [0.05, 3.0]); a typical 150bp binding → ×1.08 / ×1.15 / ×1.30 /
×1.60.

1. **The dial works, super-linearly**: amp-day threshold crossings
   +3/−2 (s1) → +24/−10 (s4), 10 sign flips, blended |conf| p90
   ×1.08 → ×1.65. But the effective action surface stays ~190 bars ≈
   3% of all event bars: p50 = ×1.00 at EVERY strength — ~73% of
   amp-day event bars fire a family with NO same-side bp that day
   (the amp day belongs to another family's binding) or are already
   zeroed by the gate/no-action stages.
2. **The quantity channel is structurally damped**: the blend's
   denominator sums ALL families' weights, so average BUY fill qty
   moves just +0.2 units (+2%) at 8× strength. Amplification is a
   trigger/cushion nudger, not a size multiplier. Moving SIZE
   materially requires bypassing the denominator — a post-blend
   consolidated-scaling design change, cross-family by construction
   (the Part 16 offset caution's territory): do not build without its
   own transfer study + user direction.
3. **Strength buys an OOS cushion and pays in full-series quality**:
   OOS improves MONOTONE with strength (calmar −0.214 → −0.166 at s4,
   ret +1.3pp, official risk −25%, per-code median −4.35% → −3.89%)
   while omega decays monotone (3.16 → 3.03) and FULL calmar drops
   past s2 (0.91 → 0.88–0.89, dd 0.35 at s3). The per-code pattern
   (median better, wins only 4/10) = negative codes LESS negative —
   crisis-anatomy days de-risked (the tier's own grounding); IS flat
   everywhere — the mechanism is regime-conditional (adverse-regime
   cushion, not an edge engine).
4. **Sweet spots if kept hotter than shipped**: s2 (div 1000) is the
   conservative all-metrics-≥-off point on both segments; s3 (div
   500) maximizes the risk benefit (OOS official risk 136) for a
   −0.03 FULL calmar cost; s4 is past the knee. Any standing strength
   change must become a config field + pass its own OOS matrix — the
   constants are code today.

### Standing cautions

1. Keep the stage DEFAULT ON at shipped strength (measured no-harm,
   user-directed); spend nothing on the validity gate or a trainer
   axis on this evidence.
2. Re-run both studies when (a) the tier's evidence matures on the
   study codes (bindings assemble n≥3 verdicts), (b) the strategy
   scope runs codes where valid evidence exists, or (c) a rebuild
   shifts the amplifier populations (the tier re-measures nightly —
   snapshot sensitivity; it runs AFTER the families in the daily
   pipeline because its evidence reads their history_signals).
3. Single-window caveat: one OOS window (2025-07..2026-11), 10 codes,
   one snapshot — the OOS cushion is one regime's reading.

## Part 19 — the oracle CONFIDENCE SCALING + its own optimizer (2026-11-20)

User direction: "adjust oracle signal confidence ... max profits and
min risks, just confidence scaling; do not change other modules; the
oracle optimization engine is totally isolated from Omega/Calmar."
Everything lives in `strategy/factors_and_algos/_oracle/` ONLY
(scaling.py, optimizer.py, `__main__.py`, algo.py edits):

- **`scaling.py`** — `ConfidenceScaleCfg` (pydantic: `grade_power`,
  `grade_floor`, `null_conf`, `exit_mode`) + `ConfidenceScale` — the
  replay's grade→confidence transform (curve → null fill → exit mode →
  floor → event mask; identity = bit-identical to the raw-grade
  replay). Fidelity invariant: scaling changes MAGNITUDE only — every
  graded pivot still fires at its own date.
- **`optimizer.py` + `python -m strategy.factors_and_algos._oracle`** —
  the isolated engine: exhaustive 60-config grid, production
  fetch/run_backtest seam, DIRECT metrics computed locally (Σpnl/Σcapital;
  pooled-equity max DD) — NO `_optm_engine` imports (no Omega/Calmar
  losses), no persistence; CSV → `temp_scripts/oracle_conf_scale_results.csv`;
  champion rule = max FULL return within identity-DD +0.5pp; built-in
  identity guard (PASS). Champion pinned BY HAND in
  `DEFAULT_PARAMS["conf_scale"]` — re-run after every
  analysis.oracle_signals grammar change (weekly snapshot sensitivity).
- **`exit_mode` RETIRED from `TUNABLE_SPACE`** (now empty): exit
  semantics are confidence scaling, owned by the oracle optimizer.
  Precedence: `conf_scale.exit_mode` > legacy top-level `exit_mode`
  (the 2026-11-19/20 trained rows — applies only when no group is
  present) > "swing".
- **Measured (2,648 pivots, 10 indices, 19s)**: champion p=1.5 + full
  exits = FULL +207.7% dd 5.7% / OOS +66.7% dd 4.5% (identity p=1.0
  swing: +198.0% / 7.8% / +59.8% / 6.9%) — dominates on every metric,
  10/10 codes positive; min-DD p=2.0 swing (dd 4.6%, ret/dd 40.5).
  The 2026-11-19 concave-winner (p=0.5) is INVERTED on the v7.1 grade
  population (now worst on both return and dd — boosting small grades
  = size on weak pivots). Curve shape ≫ floor/null (near-inert);
  convex+swing LOSES return vs identity (partial exits scaled by
  grade^1.5 leave residual book) — the exit interaction is decisive.
  Digest: `docs/oracle_confidence_scale_study_2026-11-20.md`.
- Verified end-to-end from the UI: identity-defaults run reproduced
  pre-edit behavior exactly (seq 22264 oracle_ft10, +65.14% on 000001,
  risk LITTLE), and the champion run follows the same path with scaled
  reasons (`[scale ...]` suffix on every decision reason).
- **Round 2 (2026-11-20, user-directed "push realized pnl to 700+,
  compare the naive")**: `ConfidenceScaleCfg` gained SIDE-SPLIT knobs
  (`buy_power`/`sell_power`/`buy_floor`/`sell_floor`, None = shared;
  bf100+sf100 ≡ the naive flat ±100 replay) and the optimizer gained
  `--grid pnl` (`build_pnl_grid()`, ranked by ABSOLUTE realized PnL
  with naive + identity reference cells). The 000001 diagnosis
  (`temp_scripts/oracle_champion_missed_study.py`) showed full exits
  liquidating the book on conf-13..21 pivots (the 2022-10-25 and
  2025-05-09 missed rallies; flat 47% of the window, +42.2% up-moves
  missed) — a balanced p1.5+swing re-pin intervened (seq 22266,
  +64.65%). The FINAL standing pin is the 700+ pnl cell (user
  directive, seq 22267: realized pnl +792.53 stored, LITTLE):
  `buy_power 0.25 / buy_floor 100 / sell_power 4 / swing` — every BUY
  at full size, convex sells persist the book. REVERTED the same day
  by user direction ("undo last session change") — the STANDING pin is
  the balanced p1.5+swing (seq 22268: 000001 +64.65% / realized
  +234.96 / LITTLE; pooled +195.6% / dd 4.9%). LEVERAGE DISCLOSURE
  (standing): absolute-pnl pins ride a 7-9.6× deployed book — the
  Total Return card collapses (~+20% on 000001 vs identity's +71%,
  pooled 123% vs 198%) and dd ~6×; the balanced p1.5+swing cell
  (pooled +195.6% / dd 4.9%) is the efficiency optimum, one edit away.
  Naive-vs-identity pooled: near-identical absolute pnl (17.1k both) —
  the grade buys CAPITAL EFFICIENCY (163%→198% return), not pnl mass.

(2026-11-20: the oracle mode is a SEPARATE isolated module — its study
knowledge lives in `docs/oracle_*.md` digests, not in this skill.)
