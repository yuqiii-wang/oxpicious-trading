"""Closed-vocabulary label <-> SMALLINT code maps (the label-code contract).

static.label_dim (database/sql/static/01_label_dim.sql, seed rows in the
sibling 10..60 purpose files) is the DB-side
source of truth for the label-code domains; this module is the Python
mirror read/written by the pipelines. temp_scripts/check_label_dim.py
asserts the two stay identical and prints the TypeScript mirror for
data_viz/api (services/label-codes.ts).

The codes exist so label columns NEVER ride cudf.pandas frames as
strings (the cudaErrorIllegalAddress class of 26.08: string host
transfer after sort/merge/concat). Inside a frame a converted label is
an int16 code end-to-end; decode to strings ONLY at the API boundary
(TS decodes) or for display/logging — never mid-compute.

CONVERTED domains (target columns SMALLINT, 0-based; codes live in the
DB — nullable exceptions noted):
  regime      calm=0 hot=1 panic=2 quiet=3
              (== builds.market_regimes.config.REGIMES tuple order, which
              is also lexicographic — ORDER BY code == ORDER BY label)
  side        top=0 bottom=1 flat=2 (flat: the price-vs-amt registry only)
  action      buy=0 sell=1
  weighting   equal=0 amt=1
  rank_side   HYPE=0 DRAIN=1
  pool_size   small=0 mid=1 large=2 all=3
              (semantic order — differs from the old alphabetical TEXT
              order; API sites order via decode, not ORDER BY code)
  px_speed    sharp_dn=0 slow_dn=1 flat=2 slow_up=3 sharp_up=4
              (signed semantic order)
  vol_state   shrink=0 normal=1 heavy=2
  board       MAIN=0 STAR=1 GEM=2 BSE=3
              (NULLable in stats.stock_identity — the source row may
              carry no board)
  sec_owner_type  fund_manager=0 broker=1 asset_manager=2 insurance=3
                  bank=4 company=5 other=6 (NULLable in stats.sec_owners)
  contract_type   index=0 bond=1 (futures)
  sec_type   etf=0 index=1 stock=2
             (converted across the analysis schema — the mov_ave* family
             AND pe / pe_codes / pe_pct / pe_pct_streaks / dividends /
             dividend_est / dividend_deliveries / margin_tech_stats /
             margin_changes, monitor.src_data_audit, and
             stats.sec_importance_ranks. OTHER sec_type columns — stats.*,
             analysis_signals.*, live.* — KEEP TEXT
             (API-facing identity + filter columns); there the dict is
             the frame-only internal code, re-materialized at those write
             boundaries.)
  option_type    CALL=0 PUT=1 (stats.options_terms, analysis.options_* —
                 expiry_identity / skewness_stats / oi_stats /
                 iv_skew_stats / walls)
  ext_resource   http=0 llm=1 (monitor.external_resource_audit.
                 resource_type — the external request audit's family)
  ext_http_source  sse=0 szse=1 bse=2 cffex=3 csindex=4 cnindex=5
                 chinabond=6 shibor=7 huatai=8 eastmoney=9 cninfo=10
                 gov=11 pboc=12 zhihu=13 other=99
                 (external_resource_audit.sub_type when resource_type=0 —
                 the request host family, suffix-matched from the URL)
  ext_llm_provider zhipu=0 other=99 (external_resource_audit.sub_type
                 when resource_type=1 — the provider name)
  analysis_cache_kind boost_derivation=0 (analysis_signals.
                 analysis_cache.kind — the generic intermediate-result
                 cache's tenant discriminator)
  roof_floor_method trendline=0 donchian=1 quantile=2 channel=3 boll=4
                 median=5 (analysis.mov_ave_spreads_roof_floor_estimates.
                 method — how a roof/floor level is quantitatively
                 estimated; median = the cross-method consensus row)
  rf_rebound_outcome no_rebound=0 never_retested=1 held=2 broken=3
                 unresolved=4 (analysis.mov_ave_spreads_roof_floor_
                 rebounds.outcome — the rebound-audit funnel of a
                 recorded extreme)
  research_logic growth=0 value=1 dividend=2 commodity=3 cyclical=4
                 defensive=5 quality=6 theme=7 other=99
                 (text.llm_qa_by_research.logic_label — the dominant
                 investing logic a research-logic Q&A row classifies;
                 99 other = the fallback; first text.* LLM-layer enum
                 converted, 2026-10-27)

PENDING domains (vocabulary registered in label_dim and the mirrors, but
the target columns are STILL TEXT and the writers still emit strings —
do NOT use these codes in frames/SQL until the table + writer are
converted):
  skew_type   oi_moneyness=0 iv_smile=1 greek_delta=2 greek_gamma=3
              greek_vega=4 (options skew; live.options_intraday too)
  attribution_type equal=0 trading_amt=1 (industry attributions)
  trade_side  BUY=0 SELL=1 (strategy.trade_decision)

NOT converted (deliberate — see static.label_dim's header): industry_id /
sector_id / exchange (API-facing identity and filter columns; exchange
additionally blocked by a partition-key table), signal_type /
signal_sub_type (open family identifiers), scale_method
(reserved vocabulary), and the REMAINING text.* LLM-layer enums
(research_logic converted 2026-10-27 — the module mirror below).

industry_id STAYS TEXT in the DB (API-facing identity and filter
column). For frame-only traffic use the INTERNAL codes below: attach
once as an int16 column, compute, then re-materialize the string at the
write boundary from the value you attached it from (a loop constant —
no per-row decode ever).
"""
from __future__ import annotations

import numpy as np

# ---------------------------------------------------------------------------
#  Label-code domains — mirror of static.label_dim (check_label_dim.py gates).
#  CONVERTED columns store these codes in the DB; the PENDING three
#  (skew_type / attribution_type / trade_side) are vocabulary
#  only — their columns are still TEXT until table + writers are converted.
# ---------------------------------------------------------------------------

REGIME_CODE: dict[str, int] = {"calm": 0, "hot": 1, "panic": 2, "quiet": 3,
                         "all": 4}  # 4 = the POOLED population (curves; and every ma_cross row since 2026-10-12 — the family is regime-free)
SIDE_CODE: dict[str, int] = {"top": 0, "bottom": 1, "flat": 2}
ACTION_CODE: dict[str, int] = {"buy": 0, "sell": 1, "wait": 2}  # 2 wait (renamed from no_action 2026-10-16) = a rule not trading yet — signal_rules' observational outcomes (the VRP-declined twin / the sample-starved cells)
# the code's own trend classification in force for a signal_rules cell
# (the PK member that makes rule_params per-PK — the same sub signal
# type measures asymmetric populations in different trends). Since
# 2026-10-15 classified off the RSI RE-ENTRY episodes (top/bottom stay
# the MA255-gap extremes): rise/fall = a Wilder-RSI10 zone episode
# alive through <= K out-of-band rows under the Kaufman net-change
# efficiency guard, with the DEEP-ANCHOR TURN (a
# deep RSI <=30/>=70 touch opens a 40-row window where the
# midline-50 re-cross with slope flips the trend); 5 unknown = neither
# (RSI mid-band / warmup). 4 = the POOLED stamp of the sparse
# non-trend-split families' rule rows (the regime 'all' precedent) —
# never a day state (the retired 'flat' day state went with the
# price-vs-MA60 rise/fall it belonged to)
TREND_CODE: dict[str, int] = {"top": 0, "bottom": 1, "rise": 2, "fall": 3,
                              "all": 4, "unknown": 5}
# the detection family (analysis_signals.signal_thresholds /
# history_signals / signal_curves — converted 2026-10-10; registry
# order = pipeline order)
SIGNAL_TYPE_CODE: dict[str, int] = {
    "mov_rsi": 0, "bollinger": 1, "ma_cross": 2,
    "options_wall": 3, "options_vix": 4, "momentum": 5,
    "rf_break": 6, "hedge_pairs": 7,
    # hp_rsi (2026-10-18, the two-layer merge): the hedge_pairs
    # family's RSI-SOURCED ORCHESTRATION layer — the components'
    # existing Short-Term-Tech rsi signal rules (REAL mov_rsi rows,
    # type 0, at the market + leg codes) are the SOURCE, and the
    # hedge pairs layer orchestrates them into the portfolio rules
    # (hp_entry_legs_rsi / hp_exit_mkt_high / hp_catchup_rsi_hi)
    # stamped under THIS type at the pair codes; the pair-native
    # rules (harvest / stop / flip) stay under hedge_pairs 7
    "hp_rsi": 8,
    # rebound (2026-10-26, user-directed): the drop→rebound→resolution
    # family — the ma-spread rebound study's graduates as rule cells
    # (capitulation trough-break BUY, failed-reclaim exit-timing SELL,
    # full-reclaim continuation BUY), each in a PLAIN and a
    # PE-conditioned variant (the is_pe_boosted PK axis)
    "rebound": 9,
    # macd (2026-10-26): the classic MACD crossover family — the
    # typical EMA study's product (the classic 12/26/9 spans, ema12/
    # ema26 precomputed in tech_stats since the same day). MACD line
    # = ema12 - ema26 (computed in the family's fetch from the base
    # closes), signal line = its own EMA9, trigger = the signal-line
    # crossover (the classic MACD signal): cross-up BUY / cross-down
    # SELL (exit timing — the equity asymmetry).
    "macd": 10,
}
# the family config grid (analysis_signals.signal_thresholds /
# history_signals / signal_curves — converted 2026-10-11).
# ALLOCATION: SERIES codes 0.. identify the CURVE cell (family +
# window + kind for ma_cross) — signal_curves.signal_sub_type;
# THRESHOLD codes = (series + 1) * 100 + width ordinal identify the
# emitted bar — signal_thresholds / history_signals. The two tables
# join on code // 100 - 1 == series. Labels are the exact sub_type
# strings the writers previously stored; the SINGLE-WIDTH retest
# kinds (one bar) USE THE SERIES CODE ITSELF as their threshold
# code (label_dim is UNIQUE(domain, label) — the label cannot
# carry two codes; join: code < 100 ? code : code // 100 - 1).
SIGNAL_SUB_TYPE_SERIES: dict[str, int] = {
    "rsi3": 0,
    "rsi6": 1,
    "rsi10": 2,
    "rsi14": 3,
    "rsi20": 4,
    "rsi60": 5,
    "boll5": 6,
    "boll20": 7,
    "boll60": 8,
    "ma20_cross": 9,
    "ma60_cross": 10,
    "ma20_retest": 11,
    "ma60_retest": 12,
    "ma120_deep": 13,
    "ma255_deep": 14,
    "ma20_ecross": 15,
    "ma60_ecross": 16,
    "ma20_eretest": 17,
    "ma60_eretest": 18,
    "ma120_edeep": 19,
    "ma255_edeep": 20,
    "ma20_gap": 21,
    "ma60_gap": 22,
    "ma120_gap": 23,
    "ma255_gap": 24,
    "ma20_egap": 25,
    "ma60_egap": 26,
    "ma120_egap": 27,
    "ma255_egap": 28,
    # options_wall (2026-10-12): ow_put the put-wall BUY series /
    # ow_call the call-wall SELL series (thresholds only, no curve
    # rows — the per-code breach cells sit far under the curve emit
    # floor; the 40+ block leaves the implicit x-kind series codes
    # 29..33 clear)
    "ow_put": 40,
    "ow_call": 41,
    # options_vix (2026-10-13): ovix_buy the fear-gauge BUY series /
    # ovix_sell the SELL/exit series (thresholds only, no curve rows)
    "ovix_buy": 42,
    "ovix_sell": 43,
    # momentum (2026-10-15): the five lookback axes of the log-return
    # momentum family — mom5/mom20 the FIXED 5/20d anchors,
    # momshort/mommid/momlong the VOL-ADAPTIVE lookbacks of
    # analysis.mov_ave_momentum (thresholds only, no curve rows)
    "mom5": 50,
    "mom20": 51,
    "momshort": 52,
    "mommid": 53,
    "momlong": 54,
}
SIGNAL_SUB_TYPE_THRESHOLD: dict[str, int] = {
    "rsi3_1pct": 100,
    "rsi3_2pct": 101,
    "rsi3_3pct": 102,
    "rsi6_1pct": 200,
    "rsi6_2pct": 201,
    "rsi6_3pct": 202,
    "rsi6_5pct": 204,
    "rsi6_10pct": 209,
    "rsi10_1pct": 300,
    "rsi10_2pct": 301,
    "rsi10_3pct": 302,
    "rsi10_5pct": 304,
    "rsi10_10pct": 309,
    "rsi14_1pct": 400,
    "rsi14_2pct": 401,
    "rsi14_3pct": 402,
    "rsi14_5pct": 404,
    "rsi14_10pct": 409,
    "rsi20_1pct": 500,
    "rsi20_2pct": 501,
    "rsi20_3pct": 502,
    "rsi60_1pct": 600,
    "rsi60_2pct": 601,
    "rsi60_3pct": 602,
    "boll5_2std": 700,
    "boll5_2p5std": 701,
    "boll5_3std": 702,
    "boll20_2std": 800,
    "boll20_2p5std": 801,
    "boll20_3std": 802,
    "boll60_2std": 900,
    "boll60_2p5std": 901,
    "boll60_3std": 902,
    "ma20_s11": 1000,
    "ma20_s21": 1001,
    "ma20_s42": 1002,
    "ma60_s11": 1100,
    "ma60_s21": 1101,
    "ma60_s42": 1102,
    "ma20_retest": 11,
    "ma60_retest": 12,
    "ma120_d8pct": 1400,
    "ma120_d12pct": 1401,
    "ma255_d8pct": 1500,
    "ma255_d12pct": 1501,
    "ma20_es11": 1600,
    "ma20_es21": 1601,
    "ma20_es42": 1602,
    "ma60_es11": 1700,
    "ma60_es21": 1701,
    "ma60_es42": 1702,
    "ma20_x1pct": 3000,
    "ma20_x2pct": 3001,
    "ma20_x3pct": 3002,
    "ma60_x1pct": 3100,
    "ma60_x2pct": 3101,
    "ma60_x3pct": 3102,
    "ma20_ex1pct": 3200,
    "ma20_ex2pct": 3201,
    "ma20_ex3pct": 3202,
    "ma60_ex1pct": 3300,
    "ma60_ex2pct": 3301,
    "ma60_ex3pct": 3302,
    "ma20_eretest": 17,
    "ma60_eretest": 18,
    "ma120_ed8pct": 2000,
    "ma120_ed12pct": 2001,
    "ma255_ed8pct": 2100,
    "ma255_ed12pct": 2101,
    "ma20_g3pct": 2200,
    "ma20_g5pct": 2201,
    "ma20_g8pct": 2202,
    "ma60_g3pct": 2300,
    "ma60_g5pct": 2301,
    "ma60_g8pct": 2302,
    "ma120_g3pct": 2400,
    "ma120_g5pct": 2401,
    "ma120_g8pct": 2402,
    "ma255_g3pct": 2500,
    "ma255_g5pct": 2501,
    "ma255_g8pct": 2502,
    "ma20_eg3pct": 2600,
    "ma20_eg5pct": 2601,
    "ma20_eg8pct": 2602,
    "ma60_eg3pct": 2700,
    "ma60_eg5pct": 2701,
    "ma60_eg8pct": 2702,
    "ma120_eg3pct": 2800,
    "ma120_eg5pct": 2801,
    "ma120_eg8pct": 2802,
    "ma255_eg3pct": 2900,
    "ma255_eg5pct": 2901,
    "ma255_eg8pct": 2902,
    # options_wall (2026-10-12): the OI-wall breach bars — the close
    # beyond the chain-dominant zone wall edge by the pct width
    # (ow_put_025pct BUY VRP-gated / ow_call_025pct + ow_call_100pct
    # SELL ungated, same-day wall basis). The 2026-10-13 hit-and-bounce
    # additions: ow_put_center_vrp BUY (the close within 1% above the
    # put zone CENTER, VRP-gated; _rej the VRP-fail twin) /
    # ow_call_approach_rally SELL (the close within 1.5% below the
    # call cap on an up-day).
    "ow_put_025pct": 4100,
    "ow_put_025pct_rej": 4101,
    "ow_put_center_vrp": 4102,
    "ow_put_center_vrp_rej": 4103,
    "ow_call_025pct": 4200,
    "ow_call_100pct": 4201,
    "ow_call_approach_rally": 4202,
    # options_vix (2026-10-13): the vol-index fear-gauge configs over
    # the corr_20d value space — ovix_spike BUY (corr < -0.8 & 5d vol
    # slope > +10%), ovix_spike_hivol BUY (spike & vol z252 > 1),
    # ovix_rollover BUY (corr < -0.8 & the vol peak confirmed rolling
    # over), ovix_fear_unwind SELL (corr crosses up through -0.5 after
    # a deep-fear episode — the study's exit companion); the
    # 2026-10-14 populate additions — ovix_deep_hivol BUY (corr < -0.8
    # & vol z252 > 0.5, no spike — the established-fear-regime set) and
    # ovix_spike_wide BUY (the panic spike, fear bar widened to
    # corr < -0.75)
    "ovix_spike": 4300,
    "ovix_spike_hivol": 4301,
    "ovix_rollover": 4302,
    "ovix_deep_hivol": 4303,
    "ovix_spike_wide": 4304,
    # ovix_panic_peak BUY (2026-10-15, the vol30 value space): vol
    # escalates >= 5% past its prior 120-session max while still
    # spiking (slope5 > +10%) AND spot below its 5-sessions-ago close
    # — the PANIC-PEAK escalation leg (fires at the crisis's new
    # extreme, not its onset; the downside leg separates fear from
    # upside euphoria — the corr axis alone stays moderate in V-spike
    # crises like 000852 2024-02)
    "ovix_panic_peak": 4305,
    "ovix_fear_unwind": 4400,
    # momentum (2026-10-15): the log-return momentum family — THRESHOLD
    # codes (series + 1) * 100 + tail ordinal at 5100..5502 — the
    # nested 1/2/3% tail carve of the axis's own trailing-10y
    # log-return ln(1+momentum) quantile
    "mom5_1pct": 5100,
    "mom5_2pct": 5101,
    "mom5_3pct": 5102,
    "mom20_1pct": 5200,
    "mom20_2pct": 5201,
    "mom20_3pct": 5202,
    "momshort_1pct": 5300,
    "momshort_2pct": 5301,
    "momshort_3pct": 5302,
    "mommid_1pct": 5400,
    "mommid_2pct": 5401,
    "mommid_3pct": 5402,
    "momlong_1pct": 5500,
    "momlong_2pct": 5501,
    "momlong_3pct": 5502,
    # rf_break (2026-10-16): the roof/floor breakout family — the close
    # beyond the PRIOR day's roof/floor level
    # (analysis.mov_ave_spreads_roof_floor_estimates) per window x
    # method; roof BUY (the significant-high continuation), floor SELL
    # (the breakdown exit); each day claims at its deepest breached
    # window. Plain labels = the donchian method (window extremes,
    # method 1); _med_ = the median consensus of the five estimate
    # methods (method 5)
    "rf60_roof": 5600,
    "rf120_roof": 5601,
    "rf255_roof": 5602,
    "rf500_roof": 5603,
    "rf60_floor": 5610,
    "rf120_floor": 5611,
    "rf255_floor": 5612,
    "rf500_floor": 5613,
    "rf60_med_roof": 5620,
    "rf120_med_roof": 5621,
    "rf255_med_roof": 5622,
    "rf500_med_roof": 5623,
    "rf60_med_floor": 5630,
    "rf120_med_floor": 5631,
    "rf255_med_floor": 5632,
    "rf500_med_floor": 5633,
    # rf_break deep tiers (2026-10-26, the depth-split study's
    # graduates): the DEEP breach legs as their own sub types —
    # roof_deep BUY (the depth-gated continuation: > the k bar beyond
    # the prior day's roof, ~2x the catch-all's forward blend) and
    # floor_deep BUY (the capitulation V-bottom: > the k bar below the
    # prior day's floor — the falling knife REVERSES at depth, the
    # study's reversed leg). Bars self-adaptive per cell from the
    # config grids (the ma_cross gdepth precedent)
    "rf60_roof_deep": 5640,
    "rf120_roof_deep": 5641,
    "rf255_roof_deep": 5642,
    "rf500_roof_deep": 5643,
    "rf60_floor_deep": 5650,
    "rf120_floor_deep": 5651,
    "rf255_floor_deep": 5652,
    "rf500_floor_deep": 5653,
    "rf60_med_roof_deep": 5660,
    "rf120_med_roof_deep": 5661,
    "rf255_med_roof_deep": 5662,
    "rf500_med_roof_deep": 5663,
    "rf60_med_floor_deep": 5670,
    "rf120_med_floor_deep": 5671,
    "rf255_med_floor_deep": 5672,
    "rf500_med_floor_deep": 5673,
    # rf_break floor bounce (2026-10-26, the gap study's graduate):
    # the close breaks the prior day's MEDIAN consensus floor while
    # the DONCHIAN wick floor still holds below it — the
    # consensus-broken / wick-intact bounce BUY, cross-method (one
    # population per window, t 3.0-4.5 at all four windows). The same
    # day's legacy cleanup RETIRED the catch-all roof-buy / floor-sell
    # sub types 5600-5633 above (decode-only vocabulary now)
    "rf60_floor_bounce": 5680,
    "rf120_floor_bounce": 5681,
    "rf255_floor_bounce": 5682,
    "rf500_floor_bounce": 5683,
    # ovix_atr_break (2026-10-16): the volatility-expansion breakout —
    # the spot day-return in close-to-close ATR units beyond the k bar
    # while vol slope5 fires (the 2026-10-15 low-base repricing class)
    "ovix_atr_break": 4306,
    # ovix_euphoria_peak (2026-10-19): the panic-peak MIRROR — the SELL
    # exit on the blow-off top (vol > prior 120-session max x 1.05 &
    # slope5 > +10% & spot ABOVE its 5-sessions-ago close; the
    # upside-euphoria leg the buy legs exclude, 2024-10-08 the
    # canonical catch)
    "ovix_euphoria_peak": 4307,
    # ma_cross breadth additions (2026-10-16): the market-level breadth
    # rules — the Zweig 10d-EMA thrust (BUY) + the 9:1 up/down days
    "ma_breadth_thrust": 5700,
    "ma_9to1_up": 5701,
    "ma_9to1_dn": 5702,
    # hedge_pairs (2026-10-18, block 5800+): the discoveries of the
    # long–long anti-correlated pair study — entry = broad-market
    # drawdown gate x both legs' σ-depth (dd{5,10}% x z{1,2}, the
    # nested deepest-claim carving); harvest = the staged volatility-
    # harvest exits (|ratio z| >= 1.5/2 sells the relatively-high leg;
    # the laggard reaching its own z >= 1/2 sells the second leg);
    # stops = the beta stop (market >= 20/30% below its 255d high) +
    # the hedge-regime break (offset corr >= 0 within 60d of a
    # <= -0.30 reading); regime_flip = the observational hedgeable
    # turn-on (offset corr <= -0.30 episode first day, ungated buy).
    # The 2026-10-21 dedup (same-semantics subs collapse to the one
    # most semantic): 5800-5809 + hp_regime_flip (5811) retired here,
    # and hp_harvest_z15/z20 (5804/5805) merged into hp_harvest
    # (5823) — the z bar is a run_cells candidate, not an identity
    # (labels stay registered)
    "hp_entry_dd5_z1": 5800,
    "hp_entry_dd5_z2": 5801,
    "hp_entry_dd10_z1": 5802,
    "hp_entry_dd10_z2": 5803,
    "hp_harvest_z15": 5804,
    "hp_harvest_z20": 5805,
    "hp_catchup_z1": 5806,
    "hp_catchup_z2": 5807,
    "hp_stop_mkt_dd20": 5808,
    "hp_stop_mkt_dd30": 5809,
    "hp_stop_regime": 5810,
    "hp_regime_flip": 5811,
    # the 2026-10-18 market-decoupling rework: hp_entry_legs_rsi
    # replaces the four dd x z entries (the market-drawdown gate made
    # every pair fire the SAME action on the SAME crash dates) — both
    # legs' OWN Short-Term-Tech capitulation signal (Wilder RSI14
    # below its per-leg adaptive percentile tail) paired within ~5
    # traded days, roughly nearby never exact-date; hp_catchup_rsi_hi
    # replaces the MA20-z catch-ups — the laggard's own RSI high-tail
    # breach. The dd entries (5800-5803), MA20-z catch-ups (5806/07)
    # and beta stops (5808/09) are RETIRED (labels stay registered)
    "hp_entry_legs_rsi": 5812,
    "hp_catchup_rsi_hi": 5813,
    # the market-exhaustion composite SELL (the vocabulary-split pass,
    # 2026-10-18: the broad market's own short-term-tech HIGH-tail
    # signal coordinated at the PORTFOLIO level — the highs
    # counterpart of the entry's market-lows coordination; the
    # per-code component rules are REAL mov_rsi rows, signal_type 0)
    "hp_exit_mkt_high": 5814,
    # the 2026-10-19 more-triggers graduates (docs/
    # hedge_pairs_more_triggers_study_2026-10-19.md): the rf washout
    # entries (both legs' W-day Donchian-floor breakdown witnesses
    # alive within 5 pair-days — each window its OWN population, the
    # rf_break no-carve precedent; the _mkt twin adds the market's
    # RSI 3pct capitulation witness, the RSI trio's structural
    # cousin), the deep hedgeable turn-on (offset corr's first day
    # <= -0.50 — regime_flip's deep trading anchor; the grid measured
    # -0.40 flat / -0.50 the bar / -0.60 weaker), and the catch-up
    # ENTRY (the weak leg closing back above its own MA60 — the
    # reclaim mirror of the catch-up sell; MA20 measured worse on
    # both lenses). The 2026-10-21 dedup retired the per-window trio
    # hp_entry_wash{60,120,255} (5815-5817) — the W window is a
    # run_cells candidate, not an identity (labels stay registered;
    # rows purged by the next --force run)
    "hp_entry_wash60": 5815,
    "hp_entry_wash120": 5816,
    "hp_entry_wash255": 5817,
    "hp_entry_wash120_mkt": 5818,
    "hp_entry_rho50": 5819,
    "hp_entry_catchup_reclaim60": 5820,
    # the 2026-10-20 spread-rotation study's portfolio-first graduates
    # (docs/hedge_pairs_spread_rotation_study_2026-10-20.md): the
    # MOMENTUM TILT — the harvest's opposite buy/sell pair. The study
    # measured divergence (|ratio z| >= k) as CONTINUATION, not
    # reversion: the strong leg keeps outperforming (+0.37..+0.55%
    # fwd across the k grid) and shifting the book toward it is the
    # only tilt that improved BOTH lenses (drawdown +0.16..+0.90pp on
    # 62-67% of events). The proposed mean-reversion rotation (sell
    # rich + buy cheap) measured NEGATIVE under every criterion
    # (per-open -1.24% t=-3.96; no stop rule rescued it) — these two
    # carry the direction the evidence endorsed. Two labels, one per
    # action leg (the portfolio fragments' per-sub merge keys):
    # hp_mom_strong BUYs the strong leg (response px_strong),
    # hp_mom_lag SELLs the laggard (response px_lag, the exit-timing
    # bar); the k grid {1.5, 2.0, 2.5} is the run_cells candidate set.
    "hp_mom_strong": 5821,
    "hp_mom_lag": 5822,
    # the 2026-10-21 dedup's merged harvest (the z15/z20 pair's ONE
    # most-semantic survivor — the |ratio z| candidate grid rides
    # bar_def.ratio_z, per-cell winner picks the bar)
    "hp_harvest": 5823,
    # the 2026-10-21 dedup's merged washout entry (the per-window
    # trio's ONE most-semantic survivor — the W window candidate grid
    # rides bar_def.window, per-cell winner picks it; the recorded
    # value is the fetch's combined washdepth gauge, the deepest
    # alive witness's window)
    "hp_entry_wash": 5824,
    # the 2026-10-23 more-triggers graduates II (docs/
    # portfolio_more_triggers_study_2026-10-23.md): the coordination
    # WIDENING subs — every member a SOURCED cell on >= 2 codes (the
    # member-action invariant holds at two; all pooled t >= 6.8,
    # year-majority positive): hp_entry_rsi_legs = both legs' RSI
    # capitulation, MARKET-FREE (the trio without its rarest
    # conjunct); hp_entry_wash_rsi = ONE leg's Donchian-floor
    # breakdown + the OTHER leg's RSI-3pct witness;
    # hp_entry_wash_mkt1 = ONE leg's floor breakdown + the market's
    # RSI-3pct witness. The tail/window grids ride bar_def (run_cells
    # candidates); the coordinated SELL side measured OUT (both legs'
    # RSI exhaustion is sign-aligned NEGATIVE — tops continue).
    "hp_entry_rsi_legs": 5825,
    "hp_entry_wash_rsi": 5826,
    "hp_entry_wash_mkt1": 5827,
    # the fresh typical-solution trio (2026-10-24, user-directed "most
    # typical negative correlation two curve and one broadmarket,
    # typical and simple"): hp_entry_trio = ALL THREE curves (both
    # offset-opposite legs + the broad market 000300) in their own RSI
    # capitulation tails the same day — the co-wash BUY of the
    # inverse-vol long-long pair (the ONE shipped sub type); the
    # blow-off mirror is the MEASURED asymmetry check in the engine
    # (measured thin — coordinated tops CONTINUE; rows discarded), so
    # hp_exit_trio stays the check's registered vocabulary with no
    # writer. The tail grid rides bar_def (the run_cells candidate
    # convention).
    "hp_entry_trio": 5828,
    "hp_exit_trio": 5829,
    # the SIMPLIFIED portfolio's witness vocabulary (2026-10-24,
    # user-directed: the triggered portfolio signal SETS its cell-level
    # member records itself — no sourcing from the mov_rsi tier):
    # hp_rsi_witness was the per-curve witness record's label inside
    # portfolio_history_signals.member_signals — PAYLOAD VOCABULARY
    # ONLY, no rule rows carry it (each witness = a curve's deepest
    # alive RSI-tail breach run start, at its own day). 2026-10-25
    # (user-directed "diff legs or roles should have diff sub types"):
    # the per-ROLE labels supersede it for new rows — market / leg A /
    # leg B each their own sub type; 5830 stays for old-history decode
    "hp_rsi_witness": 5830,
    "hp_rsi_witness_mkt": 5831,
    "hp_rsi_witness_leg_a": 5832,
    "hp_rsi_witness_leg_b": 5833,
    # the SPREAD one-buy-one-sell sub (2026-10-25, user-directed "one
    # buy one sell by the spread between two negative corr curves,
    # disregarding the benchmark curve"; study temp_scripts/
    # spread_pair_study.py): hp_entry_spread = the pair's long-short
    # SPREAD curve (w_a*r_a - w_b*r_b compounded, the fetch's own
    # lagged inverse-vol weights, NO market curve) in its own Wilder-
    # RSI(14/10) bottom tail vs its own adaptive percentile bar — the
    # classic pairs CONVERGENCE trade: BUY the laggard leg A, SELL the
    # leader leg B (response axis = the spread value's forward blend).
    # The study's measured record: the RAW population is negative
    # (pooled -0.08..-0.17% every window x tail — the 2026-10-20
    # ratio-z negative reproduces on the RSI axis), the edge lives in
    # the per-pair winners (25/48 pairs, pooled +0.55% t=4.70 hit 61%
    # 5/6 yrs — the rules-era self-adaptive cell mode verbatim); the
    # top-tail CONTINUATION (buy the leader, +0.46% t=6.15 6/6 yrs) is
    # the honest positive recorded for a future sub. hp_exit_spread =
    # the TOP-TAIL EXIT, SHIPPED 2026-10-25 evening (user-directed
    # "2024-04-18 can also be a good event date to pick" — the showcase
    # pair's rotation blow-off): the spread at its RSI EXHAUSTION tail,
    # SELL the leader leg A / BUY the laggard leg B (exit timing,
    # never pooled-gated); per-year floor policy: 44 winners / 850
    # events, pooled +0.20% t=3.41 6/6 yrs (study spread_peryear_
    # study.py).
    "hp_entry_spread": 5834,
    "hp_exit_spread": 5837,
    # the spread event's per-ROLE witness records (the 2026-10-25
    # per-role precedent): the bought leg (leg A, the laggard) and the
    # sold leg (leg B, the leader) each carry their own payload label —
    # each leg's own RSI vs its own tail bar at the event day, its
    # role-direction depth linking the portfolio confidence. Payload
    # vocabulary only, no rule rows behind them.
    "hp_spread_buy_leg": 5835,
    "hp_spread_sell_leg": 5836,
    # the REBOUND family (2026-10-26, user-directed — the ma-spread
    # rebound study's graduates, temp_scripts/ma_spread_rebound_study.py
    # + docs/ma_spread_rebound_study_2026-10-26.md): the causal
    # drop→rebound→resolution episodes per the anchor window W. Each
    # sub type emits PLAIN and PE-conditioned cells (the is_pe_boosted
    # PK axis; the gate = the code's own trailing PE-percentile
    # condition riding rule_params.bar_def.pe_gate).
    #   capit  BUY  the CAPITULATION trough-break (the further-drop
    #          resolution; the study's strongest fwd20 population after
    #          big drops — PE barbell: cheap strongest +6.6% fwd20,
    #          rich delayed-but-real by 60d, middle dead);
    #   fail   SELL the FAILED RECLAIM (rollover with the recovery
    #          ratio in [0.90, 1.0) — the stall just below the last
    #          high, the refined further-drop population, exit timing
    #          for longs; works only outside cheap PE);
    #   reclaim BUY the FULL RECLAIM (close above the prior high — the
    #          next rebound measures the strongest, the study's H2
    #          graduate; a mid/rich-PE phenomenon).
    "rebound_capit_60": 5900,
    "rebound_capit_255": 5901,
    "rebound_fail_60": 5902,
    "rebound_fail_255": 5903,
    "rebound_reclaim_60": 5904,
    "rebound_reclaim_255": 5905,
    # the MACD family (2026-10-26, the typical-EMA study's product):
    # the classic signal-line crossover sub types over the classic
    # 12/26/9 spans — macd_cross_up BUY (the bullish crossover, the
    # momentum turn) / macd_cross_dn SELL (the bearish crossover, the
    # exit-timing mirror — the equity asymmetry)
    "macd_cross_up": 6000,
    "macd_cross_dn": 6001,
}
SIGNAL_SUB_TYPE_CODE: dict[str, int] = {
    **SIGNAL_SUB_TYPE_SERIES, **SIGNAL_SUB_TYPE_THRESHOLD,
}
WEIGHTING_CODE: dict[str, int] = {"equal": 0, "amt": 1}
PAIR_GRAIN_CODE: dict[str, int] = {"index": 0, "industry": 1}
RANK_SIDE_CODE: dict[str, int] = {"HYPE": 0, "DRAIN": 1}
POOL_SIZE_CODE: dict[str, int] = {"small": 0, "mid": 1, "large": 2, "all": 3}
PX_SPEED_CODE: dict[str, int] = {
    "sharp_dn": 0, "slow_dn": 1, "flat": 2, "slow_up": 3, "sharp_up": 4,
}
VOL_STATE_CODE: dict[str, int] = {"shrink": 0, "normal": 1, "heavy": 2}
# The ohlc_gaps pattern vocabulary — the day's classified OHLC shape.
# The DB column (analysis.mov_ave_spreads_detail_ohlc_gaps.pattern) is
# deliberately TEXT, NOT a label_dim domain (the label-map-only TEXT
# store class), so this is a PLAIN python-side vocabulary — mapped to
# int8 codes at the fetch boundary; never registered in LABEL_MAPS.
PATTERN_CODE: dict[str, int] = {
    "spike_fade": 0,        # 冲高回落 (top-decile upper wick)
    "flush_recover": 1,     # 探底回升 (top-decile lower wick)
    "gap_up_advance": 2,    # 高开高走
    "gap_up_decline": 3,    # 高开低走
    "gap_down_advance": 4,  # 低开高走
    "gap_down_decline": 5,  # 低开低走
    "neutral": 6,           # |gap_open| inside the deadband
}
# The frame encoding for "the axis store has no row for this day" —
# never a valid code (the code vocabularies are 0-based dense); the
# boost reads it as the NEUTRAL 0 edge.
AXIS_MISSING = -1
BOARD_CODE: dict[str, int] = {"MAIN": 0, "STAR": 1, "GEM": 2, "BSE": 3}
SEC_OWNER_TYPE_CODE: dict[str, int] = {
    "fund_manager": 0, "broker": 1, "asset_manager": 2, "insurance": 3,
    "bank": 4, "company": 5, "other": 6,
}
OPTION_TYPE_CODE: dict[str, int] = {"CALL": 0, "PUT": 1}
SKEW_TYPE_CODE: dict[str, int] = {
    "oi_moneyness": 0, "iv_smile": 1, "greek_delta": 2, "greek_gamma": 3,
    "greek_vega": 4,
}
CONTRACT_TYPE_CODE: dict[str, int] = {"index": 0, "bond": 1}
# 3 = industry: composite industry entity (code = industry_id, e.g. BANKS)
# — the stats.industry_basic_stats pool-'all' slice as an analysis universe
# (mov_ave_spread + pe_and_dividends read it via stats.industry_identity)
SEC_TYPE_CODE: dict[str, int] = {"etf": 0, "index": 1, "stock": 2, "industry": 3}
ATTRIBUTION_TYPE_CODE: dict[str, int] = {"equal": 0, "trading_amt": 1}
TRADE_SIDE_CODE: dict[str, int] = {"BUY": 0, "SELL": 1}
# external request audit (monitor.external_resource_audit) — sub_type's
# domain is selected by resource_type (http -> ext_http_source,
# llm -> ext_llm_provider); 'other'=99 is the unrecognized fallback.
EXT_RESOURCE_CODE: dict[str, int] = {"http": 0, "llm": 1}
EXT_HTTP_SOURCE_CODE: dict[str, int] = {
    "sse": 0, "szse": 1, "bse": 2, "cffex": 3, "csindex": 4,
    "cnindex": 5, "chinabond": 6, "shibor": 7, "huatai": 8,
    "eastmoney": 9, "cninfo": 10, "gov": 11, "pboc": 12, "zhihu": 13,
    "sina": 14,
    "other": 99,
}
EXT_LLM_PROVIDER_CODE: dict[str, int] = {"zhipu": 0, "other": 99}
# DB health sampler (monitor.db_health) — replication_state is the WAL
# shipping state as seen from the SAMPLED instance (primary: the standby's
# pg_stat_replication.state; replica: pg_stat_wal_receiver.status);
# 'none'=99 = no standby attached / receiver absent.
DB_INSTANCE_CODE: dict[str, int] = {"primary": 0, "replica": 1}
DB_REPLICATION_STATE_CODE: dict[str, int] = {
    "streaming": 0, "catchup": 1, "startup": 2, "backup": 3,
    "stopping": 4, "connecting": 5, "stopped": 6, "none": 99,
}
# the generic analysis intermediate-result cache
# (analysis_signals.analysis_cache.kind) — which analysis owns the row's
# cache jsonb
ANALYSIS_CACHE_KIND_CODE: dict[str, int] = {"boost_derivation": 0}
# the kind of PORTFOLIO an analysis_signals.portfolio_signal_rules row
# groups (the portfolio tier's OWN vocabulary — what the code IS as a
# portfolio, not the signal family that measured it): the two-leg
# long–long negatively-correlated hedge pair ('A~B' composite code —
# the hedge_pairs family's universe)
PORTFOLIO_TYPE_CODE: dict[str, int] = {"neg_corr_hedge_pair": 0}
# WHICH PORTFOLIO RULE a portfolio_signal_rules row is within its
# portfolio_type (2026-10-25 split: one row per code x type x sub —
# the merged row per pair carried both rules' subs under one
# rule_params.subs before). Selected by the row's own orchestrating
# signal_sub_type (hedge_pairs: hp_entry_trio -> trio_wash;
# hp_entry_spread / hp_exit_spread -> spread). The registered mov_rsi
# cells (the is_in_portfolio TRUE tier) map INTO these rows through
# signal_rule_ids only — never portfolio rows themselves.
PORTFOLIO_SUB_TYPE_CODE: dict[str, int] = {"trio_wash": 0, "spread": 1}
# quantitative roof/floor (resistance/support) level estimate methods
# (analysis.mov_ave_spreads_roof_floor_estimates.method) — registry order =
# the roof_floor_estimate/methods registry; 'median' is the cross-method
# consensus row (NaN-skipping median of the five methods above it)
ROOF_FLOOR_METHOD_CODE: dict[str, int] = {
    "trendline": 0, "donchian": 1, "quantile": 2,
    "channel": 3, "boll": 4, "median": 5,
}
# rebound-audit outcome of a recorded extreme
# (analysis.mov_ave_spreads_roof_floor_rebounds.outcome) — the audit's
# funnel order: no rebound -> never retested -> held / broken / unresolved
RF_REBOUND_OUTCOME_CODE: dict[str, int] = {
    "no_rebound": 0, "never_retested": 1, "held": 2,
    "broken": 3, "unresolved": 4,
}
# the dominant investing logic of a text.llm_qa_by_research row's
# underlying industry/security (logic_label) — 99 other = the fallback
# (the logic did not fit / the model could not classify); first text.*
# LLM-layer enum converted to the label-code contract (2026-10-27)
RESEARCH_LOGIC_CODE: dict[str, int] = {
    "growth": 0, "value": 1, "dividend": 2, "commodity": 3,
    "cyclical": 4, "defensive": 5, "quality": 6, "theme": 7,
    "other": 99,
}

LABEL_MAPS: dict[str, dict[str, int]] = {
    "regime": REGIME_CODE,
    "side": SIDE_CODE,
    "action": ACTION_CODE,
    "trend": TREND_CODE,
    "signal_type": SIGNAL_TYPE_CODE,
    "signal_sub_type": SIGNAL_SUB_TYPE_CODE,
    "weighting": WEIGHTING_CODE,
    "rank_side": RANK_SIDE_CODE,
    "pool_size": POOL_SIZE_CODE,
    "pair_grain": PAIR_GRAIN_CODE,
    "px_speed": PX_SPEED_CODE,
    "vol_state": VOL_STATE_CODE,
    "board": BOARD_CODE,
    "sec_owner_type": SEC_OWNER_TYPE_CODE,
    "option_type": OPTION_TYPE_CODE,
    "skew_type": SKEW_TYPE_CODE,
    "contract_type": CONTRACT_TYPE_CODE,
    "sec_type": SEC_TYPE_CODE,
    "attribution_type": ATTRIBUTION_TYPE_CODE,
    "trade_side": TRADE_SIDE_CODE,
    "ext_resource": EXT_RESOURCE_CODE,
    "ext_http_source": EXT_HTTP_SOURCE_CODE,
    "ext_llm_provider": EXT_LLM_PROVIDER_CODE,
    "db_instance": DB_INSTANCE_CODE,
    "db_replication_state": DB_REPLICATION_STATE_CODE,
    "analysis_cache_kind": ANALYSIS_CACHE_KIND_CODE,
    "portfolio_type": PORTFOLIO_TYPE_CODE,
    "portfolio_sub_type": PORTFOLIO_SUB_TYPE_CODE,
    "roof_floor_method": ROOF_FLOOR_METHOD_CODE,
    "rf_rebound_outcome": RF_REBOUND_OUTCOME_CODE,
    "research_logic": RESEARCH_LOGIC_CODE,
}

# code -> label per domain (int keys)
CODE_MAPS: dict[str, dict[int, str]] = {
    domain: {code: label for label, code in mapping.items()}
    for domain, mapping in LABEL_MAPS.items()
}

REGIME_LABEL: dict[int, str] = CODE_MAPS["regime"]
SIDE_LABEL: dict[int, str] = CODE_MAPS["side"]
ACTION_LABEL: dict[int, str] = CODE_MAPS["action"]
TREND_LABEL: dict[int, str] = CODE_MAPS["trend"]
SIGNAL_TYPE_LABEL: dict[int, str] = CODE_MAPS["signal_type"]
SIGNAL_SUB_TYPE_LABEL: dict[int, str] = CODE_MAPS["signal_sub_type"]
WEIGHTING_LABEL: dict[int, str] = CODE_MAPS["weighting"]
RANK_SIDE_LABEL: dict[int, str] = CODE_MAPS["rank_side"]
POOL_SIZE_LABEL: dict[int, str] = CODE_MAPS["pool_size"]
PX_SPEED_LABEL: dict[int, str] = CODE_MAPS["px_speed"]
VOL_STATE_LABEL: dict[int, str] = CODE_MAPS["vol_state"]
BOARD_LABEL: dict[int, str] = CODE_MAPS["board"]
SEC_OWNER_TYPE_LABEL: dict[int, str] = CODE_MAPS["sec_owner_type"]
OPTION_TYPE_LABEL: dict[int, str] = CODE_MAPS["option_type"]
SKEW_TYPE_LABEL: dict[int, str] = CODE_MAPS["skew_type"]
CONTRACT_TYPE_LABEL: dict[int, str] = CODE_MAPS["contract_type"]
SEC_TYPE_LABEL: dict[int, str] = CODE_MAPS["sec_type"]
ATTRIBUTION_TYPE_LABEL: dict[int, str] = CODE_MAPS["attribution_type"]
TRADE_SIDE_LABEL: dict[int, str] = CODE_MAPS["trade_side"]
EXT_RESOURCE_LABEL: dict[int, str] = CODE_MAPS["ext_resource"]
EXT_HTTP_SOURCE_LABEL: dict[int, str] = CODE_MAPS["ext_http_source"]
EXT_LLM_PROVIDER_LABEL: dict[int, str] = CODE_MAPS["ext_llm_provider"]
DB_INSTANCE_LABEL: dict[int, str] = CODE_MAPS["db_instance"]
DB_REPLICATION_STATE_LABEL: dict[int, str] = CODE_MAPS["db_replication_state"]
ANALYSIS_CACHE_KIND_LABEL: dict[int, str] = CODE_MAPS["analysis_cache_kind"]
PORTFOLIO_TYPE_LABEL: dict[int, str] = CODE_MAPS["portfolio_type"]
PORTFOLIO_SUB_TYPE_LABEL: dict[int, str] = CODE_MAPS["portfolio_sub_type"]
ROOF_FLOOR_METHOD_LABEL: dict[int, str] = CODE_MAPS["roof_floor_method"]
RF_REBOUND_OUTCOME_LABEL: dict[int, str] = CODE_MAPS["rf_rebound_outcome"]
RESEARCH_LOGIC_LABEL: dict[int, str] = CODE_MAPS["research_logic"]

# Display order for responses that list a domain's vocabulary —
# pool_size deliberately NOT in code order here: API/UI lists keep the
# historical TEXT presentation order (alphabetical) so the contract the
# UI already renders is unchanged.
LABEL_ORDER: dict[str, tuple[str, ...]] = {
    "regime": tuple(REGIME_CODE),
    "side": tuple(SIDE_CODE),
    "action": tuple(ACTION_CODE),
    "trend": tuple(TREND_CODE),
    "signal_type": tuple(SIGNAL_TYPE_CODE),
    "signal_sub_type": tuple(SIGNAL_SUB_TYPE_CODE),
    "weighting": tuple(WEIGHTING_CODE),
    "rank_side": tuple(RANK_SIDE_CODE),
    "pool_size": ("all", "large", "mid", "small"),
    "pair_grain": ("index", "industry"),
    "px_speed": ("sharp_dn", "slow_dn", "flat", "slow_up", "sharp_up"),
    "vol_state": ("shrink", "normal", "heavy"),
    "board": ("MAIN", "STAR", "GEM", "BSE"),
    "sec_owner_type": tuple(SEC_OWNER_TYPE_CODE),
    "option_type": ("CALL", "PUT"),
    "skew_type": ("oi_moneyness", "iv_smile", "greek_delta",
                  "greek_gamma", "greek_vega"),
    "contract_type": ("index", "bond"),
    "sec_type": ("etf", "index", "stock"),
    "attribution_type": ("equal", "trading_amt"),
    "trade_side": ("BUY", "SELL"),
    "ext_resource": tuple(EXT_RESOURCE_CODE),
    "ext_http_source": tuple(EXT_HTTP_SOURCE_CODE),
    "ext_llm_provider": tuple(EXT_LLM_PROVIDER_CODE),
    "db_instance": tuple(DB_INSTANCE_CODE),
    "db_replication_state": tuple(DB_REPLICATION_STATE_CODE),
    "analysis_cache_kind": tuple(ANALYSIS_CACHE_KIND_CODE),
    "portfolio_type": tuple(PORTFOLIO_TYPE_CODE),
    "portfolio_sub_type": tuple(PORTFOLIO_SUB_TYPE_CODE),
    "roof_floor_method": tuple(ROOF_FLOOR_METHOD_CODE),
    "rf_rebound_outcome": tuple(RF_REBOUND_OUTCOME_CODE),
    "research_logic": tuple(RESEARCH_LOGIC_CODE),
}


def code_labels(domain: str) -> np.ndarray:
    """Labels of ``domain`` as an object ndarray INDEXED BY CODE.

    The decode table for :func:`decode_codes` — one allocation per call
    is fine (2-4 labels per domain).
    """
    mapping = LABEL_MAPS[domain]
    return np.asarray(mapping, dtype=object)[
        np.argsort(np.fromiter(mapping.values(), dtype=np.int64))
    ]


def decode_codes(codes, domain: str) -> np.ndarray:
    """Host int-code array -> object ndarray of labels (host numpy only).

    The write/display boundary decode. ``codes`` must already be a RAW
    host ndarray (unwrap proxies via ``host_array`` first — the repo
    boundary rule); an out-of-range code raises IndexError (fail fast —
    the only valid codes are 0..len-1, no missing sentinel exists for
    these NOT NULL columns).
    """
    return code_labels(domain)[np.asarray(codes)]

