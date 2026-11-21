---
name: signals-no-action
description: >
  The NO-ACTION / negative-confidence tier of analysis_signals in
  oxpicious-trading: the independent per-family INVALIDATION module
  (engines/no_action — when each signal family's fired triggers are
  NOISY or MISLEADING: the mov_rsi/bollinger band walks, the
  bollinger squeeze breakdown, the ma_cross whipsaw chop, the
  rebound PE dead zone, the macd chop, and the cross-family
  recent-days same-type FATIGUE filter), its sibling the REJECT
  tier (engines/reject — deeply negative measured cell means, the
  -2.0% bar + 20-traded-row system-birth probation), the
  analysis_signals.no_action_signals table, the reason-code
  vocabulary, and the CONSERVATISM contract (overlay-never-
  suppress, isolation A/B, calibration-gated rules, no-action
  << positive). Use for ANY work touching no-action signals,
  negative confidence, signal invalidation conditions, the fatigue
  filter, or questions like "when is this signal family
  misleading" — even when the user does not mention no-action
  explicitly. Architecture of the positive tier is the sibling
  skill signals-live-vs-history; the families' trigger semantics
  live in signals-biz-logic.
---

# The no-action tier — invalidation conditions, fatigue, and the negative-confidence contract

Two negative-confidence layers, both ADVICE AGAINST an action
("do NOT buy/sell here", NEVER the inverse action), both kept
OUTSIDE the positive-confidence computation:

- **REJECT** (`engines/reject.py`, 2026-11-16) — the CELL-level
  verdict: a cell whose best candidate measured a DEEPLY negative
  dir_ave (strictly below `REJECT_DIR_AVE_MAX` −2.0%; the band
  [−2.0%, the gate bar) stays SILENT) emits its rule row at
  `is_valid FALSE` + negative-confidence history rows, UNLESS the
  SIGNAL-CLUSTER PROBATION suppresses it (the cell's SYSTEM BIRTH —
  first-ever `history_signal_performance` snapshot, read through
  the `cell_birth_dates` view — falls within the past
  `REJECT_FIRST_APPEARANCE_DAYS` = 20 of the code's own traded
  rows). Lives inside the run_cells emission but as its own module;
  never feeds pooled facts or boost anchors; the live tier fires
  reject cells with the symmetric bp clamp. 2026-11-18: `is_valid`
  joined `pk_signal_rules` (superset-key migration from
  `temp_scripts/`) — the reject verdict row registers under its OWN
  identity, independent of the positive row's slot; `RULES_PK`
  (run.py) and the `audit_family_rows` cell keys (history rows
  reconstruct `is_valid` from the confidence sign) carry the flag.
- **NO-ACTION** (`engines/no_action/`, 2026-11-18) — the DAY-level
  overlay: per-family trigger-time INVALIDATION conditions plus the
  FATIGUE filter, computed INDEPENDENTLY per signal type after the
  positive run, written to its OWN table
  (`analysis_signals.no_action_signals`), never touching a positive
  row. This skill's main subject.

The deep study behind every condition:
`docs/no_action_invalidation_study_2026-11-18.md`; the module
architecture: `docs/no_action_module_design_2026-11-18.md`; the
rejection study: `docs/negative_confidence_reject_study_2026-11-16.md`;
the HURT study + the strict gate:
`docs/no_action_hurt_study_2026-11-19.md` (temp_scripts/
no_action_hurt_study.py — the full-universe harness, authoritative
for gate purposes since 2026-11-19).

## The standing rule that governs every invalidation condition

Measured repeatedly: **sharp falls revert; sharp rises continue.**
No BUY-side invalidation may fire on plunge depth or velocity —
big-drop days are the measured BUY edge everywhere tested (rf floor
>7% one-day drops +6.84% @98.2% hit; the 2025-04-07 / 2026-10-15
capitulations fire on exactly those days). Misleading-BUY conditions
are STRUCTURAL only (squeeze breakdowns, whipsaw chop, dead-zone
valuation, fatigue). Misleading-SELL conditions are the band walks /
continuation states ("tops continue", sell side pooled negative for
every window in the record).

## Per family — valid vs noisy/misleading (the one-by-one study)

Each row: VALID = where the measured edge lives. NOISY = the
conditions under which a fired trigger misleads. ENCODED = what the
shipped design already does (do NOT double-invalidate). RULE = the
no-action rule (reason_code).

| family | VALID when | NOISY/misleading when | already ENCODED | no-action rule |
|---|---|---|---|---|
| mov_rsi | bottom-tail BUY at capitulation corners (bottom/fall trend, hot/panic regime; rsi10_1pct +1.91% @72.2%) | SELL at top-tail mid-RISE with agreeing guard — the band walk, tops continue | sell gate 0, trend-aware absorb, rsi3-sell gone | `rsi_bandwalk_sell` NO SELL (rise trend + positive Kaufman guard). NO buy rule ever |
| bollinger | −k pierce from normal/wide band at capitulation corners; +k as stationed-top exit | BUY out of a SQUEEZED band (compression→breakdown continues, −0.15% measured); SELL riding an expanding band with positive SMA slope | boll5 untriggerable, W=20 only, squeeze-reversal candidate measured out | `boll_squeeze_breakdown` NO BUY (deepest separating bandwidth cut under the <3% budget: q0.02, 2.4% share — MEASURED, separation 6-7/7 yrs at every cut); `boll_bandwalk_sell` NO SELL (top-tercile rising width + slope) — RETIRED 2026-11-19 (hurts: marked sells BETTER 7/7 yrs) |
| ma_cross | first close back above MA with real cross excess, uninterrupted run; gdepth deep gaps (buy only); retest sell as exit timing | BUY when recent history is below↔above flips — the whipsaw range wearing a new run | `is_typical` split, EMA out, no gdepth top side | `ma_whipsaw` NO BUY (≥3 MA flips in trailing 20 traded rows). No sell rule (retest too thin to mark) |
| options_wall | deep zone-edge breach with VRP passing (pooled t=3.07) | VRP-declined falling knife; wall staleness after expiry roll (UNMEASURED) | the `_rej` wait twin IS the no-action record; strike/center/nearest-expiry gone | none v1 — staleness is a recorded calibration candidate |
| options_vix | the corr×spike INTERACTION; panic peak at a re-armed vol ATH; ATR break from a low base | single-axis traps; euphoria tops that continue; price-stop exits | interaction by construction; measured-out cells emit nothing; exits on corr normalization | none v1 — gap-day atr_break is a recorded candidate |
| momentum | log-return bottom tails (bottom-trend +2.01%); top-tail sells as strongest exit timing (+2.94%) | cross-axis redundancy (momshort≈mom5); dip-in-uptrend conditioning adds nothing | log transform, plain K=5 absorb | none family-specific — FATIGUE carries it |
| rf_break | 3-day-confirmed, trend-matched breaks; floor legs own the reversion (floor-bias 1.24–1.35×) | one-day gap spends the roof move; ANY roof sell (refuted t −0.8..−6.0); unconfirmed crosses | gap guard (roof-only 7%), persistence+trend gate, ALL-BUY, ×0.78 roof scale | none new — the REFERENCE for encoded invalidation; never re-suppress what the mask excludes |
| rebound | capit trough-break (fwd20 +3.42%); fail-sell at the R cliff; reclaim BUY (+4.32%/+7.91%) | capit at MIDDLE PE (±1% around the code's own median) — the barbell DEAD ZONE: cheap20 +6.64%, rich20 +6.46% fwd60, middle NO 60d edge | R boundary exact by construction; the pe variant axis was UNLINKED 2026-11-01 (measurement stands, rows gone) | `rebound_pe_deadzone` NO BUY (own PE percentile in [0.4, 0.6)) — MEASURED |
| macd | fall-trend cross-up buys (+6.07% @77.8%); rise continuation; top/unknown cross-down exit | rapid cross alternation the K-absorb cannot merge; rounding-scale noise crosses | K=5 absorb; trend split; |hist| sigma depth | `macd_chop` NO BUY/NO SELL (≥2 cross episodes in 10 rows AND |hist| below own trailing p25) |
| hedge_pairs | the trio wash BUY | the blow-off mirror (measured thinner twice, discarded) | mirror check-discarded every run | out of scope v1 (portfolio tier) |

## The FATIGUE filter (the recent-days same-type rule — implemented, calibrated, RETIRED)

Per `(code, signal_type, action)`: when the trailing `W` of the
code's OWN traded rows contain ≥ `N` distinct FIRED days of that
triple, fired days BEYOND the Nth carry `fatigue`. Distinct DATES
count once (a crisis day firing rsi14+rsi10+rsi6 together is ONE
day of the same wash); buy and sell fatigue count separately;
market-wide bursts deliberately NOT counted (the cross-sectional
washout is where the BUY edge is strongest). THE CALIBRATION
(2026-11-18) RETIRED IT: every cell of the N{3,4,5} × W{10,20,40}
grid, every family, both actions, measured the marked population
BETTER than the kept one (e.g. mov_rsi buys at N4/W20: marked
+3.23% vs kept +1.75%) — same-type recency marks the CRISIS
CLUSTERS (2024-02, 2025-04, 2026-10), and marking those no-action
would advise against the system's best signals. The filter stays
implemented behind `NO_ACTION_FATIGUE_ENABLED = False`; re-enable
only with a re-measured separating grid.

## The calibration verdict (2026-11-18) — what actually ships

`temp_scripts/no_action_calibration_study.py` measured every
candidate on the SHIPPED fired population (per action,
sign-aligned forward blend). The structural finding: **on
gate-passing fired populations the invalidation conditions mostly
select STRONGER outcomes, not weaker — the positive tier's
measured gates already absorbed the invalidation** (a cell fires
only where its population measured positive; the noisy corners
were measured out). Per-rule verdicts:

- **SHIPS: `boll_squeeze_breakdown`** — the ONLY candidate whose
  marked population measures worse, at EVERY scope. The SHIPPED cut
  (2026-11-19, user-directed "<3% rarity"; the full-universe sweep
  in temp_scripts/no_action_hurt_study.py) is
  `NO_ACTION_BOLL_SQUEEZE_Q = 0.02`: marked fired buys **+0.76% vs
  kept +1.75%** aligned blend (hit 63.1% vs 69.1%, ~100bp per
  marked event) at **2.4% share** — under the tightened
  `NO_ACTION_MAX_SHARE = 0.03`; separation 6/7 years. The gradient
  survives every deeper cut of the sweep (q0.10 0.66% vs 1.86% at
  10.5% share 7/7 years, q0.05 0.74% vs 1.79% at 5.5% 7/7, q0.03
  0.81% vs 1.76% at 3.3% — over budget — 6/7): the deeper the
  compression, the more certainly the fired buy misleads. The
  2026-11-18 study-code numbers (−0.27% vs +2.24% at the decile)
  reproduce in direction; the full-universe honest read is that the
  marked population stays positive on average — the mark improves
  the AVERAGE taken trade, it does not remove losers wholesale.
- **RETIRED — vacuous**: `rsi_bandwalk_sell` (every fired RSI sell
  sits mid-rise with a positive guard BY CONSTRUCTION — a top-tail
  breach requires prior rises; the condition marks 100% of the
  sell population).
- **RETIRED — wrong direction**: `boll_bandwalk_sell` (1.77% vs
  0.80% sells — the fired bandwalk sells are the GOOD sells),
  `ma_whipsaw` (2.04% vs 1.41% buys), `rebound_pe_deadzone` (3.07%
  vs 1.52% buys — the variant-era measurement did not transfer to
  the gate-passing population), `macd_chop` (both actions better,
  34% share).
- **RETIRED — the fatigue filter** (see above).

Config: `NO_ACTION_ACTIVE_RULES = ("boll_squeeze_breakdown",)`;
the retired rules stay implemented for the vocabulary and are
re-activatable only with new measurements (re-running the
calibration study is the gate).

## Module architecture (the isolation contract)

- `engines/no_action/` — `NoActionRule` ABC (base.py) + one module
  per family + a registry keyed by family label, FILTERED by the
  calibration verdict (`config.NO_ACTION_ACTIVE_RULES`;
  `NO_ACTION_FATIGUE_ENABLED` gates the cross-family filter);
  pydantic-ready config carriers; class-first per
  `oxpicious-dev-conventions`. Builds its OWN masks from the
  prepared frame (shared `analysis.trends` / `_common.df_utils`
  helpers allowed; NEVER imports a family engine's
  gate/verdict/confidence code).
- Invoked per family in `run.py run_family` AFTER the positive
  entry loop (frame + in-memory fired `history_rows` in hand);
  writes ONLY `no_action_signals` (force: purge scope + chunked
  COPY; incremental: split upsert; ANALYZE after writes).
- The A/B bar (run the family with the tier off vs on): per-code
  md5 of `signal_rules` + `history_signals` IDENTICAL. Breaking
  this is the worst regression this tier can suffer.
- No rule cells, no live wiring (invariant 5: the live tier never
  writes a bar the strategy tier didn't stamp — no-action has no
  bars), no pooled facts, no boost anchors, no strategy-tier seam
  edits (the `confidence >= 0` filters read history_signals only).
- Rows: the fired row's identity (code, sec_type, signal_type,
  signal_sub_type, date, time) + the declined action + `reason_code`
  + negative-bp confidence + rule_params (condition constants + the
  measured-support digest). CHECK confidence BETWEEN −10000 AND 0.

## The STRICT GATE (the abuse guards — tightened 2026-11-19 by the hurt study)

The 2026-11-19 full-universe hurt study (docs/
no_action_hurt_study_2026-11-19.md) re-measured EVERY registered
rule at scale and found every retired rule HURTS (hurt_bp = marked −
kept: bollinger fatigue −226bp, mov_rsi fatigue −136bp,
boll_bandwalk_sell −132bp in 0/7 years, ma_whipsaw −88bp in 0/7
years, macd_chop −24/−43bp at ~1/3 share, rebound_pe_deadzone −30bp
marked-better 4/6 years, rsi_bandwalk_sell vacuous at 100% share) —
the squeeze rule is the only one that helps (+100..120bp, 6-7/7
years at every cut). THE FIVE HURT MECHANISMS the gate exists to
block: (1) VACUITY — the condition implied by the trigger marks
~100% of the population; (2) CRISIS-CLUSTER SELECTION — same-type
recency IS the capitulation signature, and sharp falls revert, so
recency marks the BEST cohort; (3) CONTINUATION MISREAD — the
corner inverts the average (band-walk sells capture the deepest
rolls); (4) RANGE MECHANICS — flip-heavy history is a range and
range-floor reclaims are the best long entries; (5) NON-TRANSFER +
DILUTION — variant-era evidence does not transfer to the
gate-passing population, and wide marks hurt every measured time.
The common root: the positive gates already measured every fired
population as edge-positive; a merely "scary-looking" trigger-time
state correlates with sharpness/recency, which correlates with the
EDGE (the standing asymmetry), not with noise.

A rule ships (or stays shipped) only if ALL of:

1. **Overlay-only**: a no-action row exists ONLY on a day the
   positive tier fired. Positive rows are never suppressed,
   discounted, or rewritten — the user sees both and decides.
2. **Measured harm, not plausibility, at FULL-UNIVERSE scale**: the
   marked population measures materially worse than kept — mean AND
   hit — under the family contract blend. A study-code-only pass is
   not a pass.
3. **Year-stable separation**: marked < kept in at least 2/3 of
   measurable years — average-only contrasts fail (every retired
   rule demonstrates a failure mode).
4. **Rarity < 3%** of the family's same-action fired rows
   (`NO_ACTION_MAX_SHARE = 0.03`, tightened 2026-11-19 from 0.10),
   met at a SEPARATING CUT of the rule's own bar (the squeeze rule
   re-cut q0.10 → q0.02 the same day for exactly this reason).
5. **Structural premise inversion**: the condition inverts the
   trigger's own measured premise (compression → downside
   continuation inverts pierce-reversion) — never a merely
   scary-looking state; never a BUY-side plunge-depth/velocity
   condition (big-drop days are the measured BUY edge).
6. **Run-time audit**: every run re-measures each active rule's
   marked vs unmarked populations and logs the digest + the share
   against the budget; an inversion or an OVER BUDGET line retires
   the rule at the next calibration pass (the log is the alarm).
7. **Budget**: the combined negative tier (reject + no-action)
   stays well under the positive count; the run summary logs the
   shares. no-action ≈ positive volume means the bars are wrong.
8. **Re-calibration before re-activation**, on the full universe,
   sweep included; failing rules stay documented-but-retired here as
   honest negatives.

## Adding / retiring a rule

1. Propose the condition as trigger-time-observable + measured
   support or adjacency (cite the study doc / docs/ study), and
   name which trigger PREMISE it structurally inverts (strict-gate
   guideline 5 — a merely scary-looking state is rejected by
   mechanism).
2. Extend `temp_scripts/no_action_hurt_study.py` (the FULL-UNIVERSE
   harness — the authoritative scope since 2026-11-19;
   no_action_calibration_study.py stays as the historical record);
   require the whole STRICT GATE: separation at full-universe scale
   in >= 2/3 of measurable years, mean AND hit, at a separating cut
   under the 3% share (WSL conda).
3. Add the rule class + registry entry + config bars + a
   `no_action_reason` label row in ALL THREE mirrors (the SQL seed
   under `database/sql/static/label_dim/`, `_common.labels`, the TS
   `label-codes.ts` — per `db-smallint-type-codes`).
4. Rebuild the family (`--sec-type index --families <one>
   --force`), check the A/B isolation md5 + the run summary's
   share, verify the UI decodes the reason.
5. Retiring = flip the config bar off (or remove the registry
   entry) + record the honest negative in this skill's table.

## What the UI shows

No-action rows UNION into the trading-signals history view with
their reason decoded via `static.label_dim`; negative confidence
renders through the EXISTING conventions — the NO BUY / NO SELL
chip (magnitude-ramped on |confidence|) and the ✕-crossed triangle
marker — plus the reason word in the history panel's row/expansion.
Reject rules rows render "gate REJECTED" in the rules panel
expansion (`rule_params.gate.reject_bar_pct`).

## Singleton strategy use (2026-11-18 — the Part-16 mirror)

The singleton strategy CAN USE the marks: the reject tier's
integration pattern, verbatim (skill `singleton-strategy-tuning`
Part 17):

- The strategy fetch carries the marks as EVIDENCE columns
  (`na_action` / `na_confidence`, own DISTINCT-ON LEFT JOIN from
  `no_action_signals` in `analysis_source.py`) — the positive pickup
  is untouched, the marks are never replayed as trades.
- `strategy.factors_and_algos._comb.no_action.NoActionOffset`
  (pipeline stage 0b, config `CombParams.no_action`, DEFAULT OFF):
  a mark zeroes the family's OWN same-side signal on the marked bar —
  the suppression cancels the marked fired event itself (the tier's
  claim, directly). Same-side only, family-local only (cross-family
  structurally absent — the Part-16 transfer finding), nothing
  invented.
- The TRAINER explores it on evidence: `set_c_comb_grid(
  has_no_action_rows=...)` adds na-ON twin cells when the fetched
  frames carry marks; an ON winner must beat its OFF twin through the
  same OOS/Calmar/official-risk funnel. A trained row persists
  `params.comb.no_action`.
- Enable per run: `--comb-json '{"no_action": {"enabled": true}}'`;
  per config: the `no_action` group in the algo_configs row.
