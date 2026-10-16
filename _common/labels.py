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

PENDING domains (vocabulary registered in label_dim and the mirrors, but
the target columns are STILL TEXT and the writers still emit strings —
do NOT use these codes in frames/SQL until the table + writer are
converted):
  skew_type   oi_moneyness=0 iv_smile=1 greek_delta=2 greek_gamma=3
              greek_vega=4 (options skew; live.options_intraday too)
  attribution_type equal=0 trading_amt=1 (industry attributions)
  trade_side  BUY=0 SELL=1 (strategy.trade_decision)

NOT converted (deliberate — see static.label_dim's header): industry_id /
sector_id / exchange (API-facing identity + filter columns; exchange
additionally blocked by a partition-key table), signal_type /
signal_sub_type (open family identifiers), scale_method
(reserved vocabulary), and the text.* LLM-layer enums.

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
    "rf_break": 6,
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
    "rsi10_1pct": 300,
    "rsi10_2pct": 301,
    "rsi10_3pct": 302,
    "rsi14_1pct": 400,
    "rsi14_2pct": 401,
    "rsi14_3pct": 402,
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
    # ovix_atr_break (2026-10-16): the volatility-expansion breakout —
    # the spot day-return in close-to-close ATR units beyond the k bar
    # while vol slope5 fires (the 2026-10-15 low-base repricing class)
    "ovix_atr_break": 4306,
    # ma_cross breadth additions (2026-10-16): the market-level breadth
    # rules — the Zweig 10d-EMA thrust (BUY) + the 9:1 up/down days
    "ma_breadth_thrust": 5700,
    "ma_9to1_up": 5701,
    "ma_9to1_dn": 5702,
}
SIGNAL_SUB_TYPE_CODE: dict[str, int] = {
    **SIGNAL_SUB_TYPE_SERIES, **SIGNAL_SUB_TYPE_THRESHOLD,
}
WEIGHTING_CODE: dict[str, int] = {"equal": 0, "amt": 1}
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
SEC_TYPE_CODE: dict[str, int] = {"etf": 0, "index": 1, "stock": 2}
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
    "roof_floor_method": ROOF_FLOOR_METHOD_CODE,
    "rf_rebound_outcome": RF_REBOUND_OUTCOME_CODE,
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
ROOF_FLOOR_METHOD_LABEL: dict[int, str] = CODE_MAPS["roof_floor_method"]
RF_REBOUND_OUTCOME_LABEL: dict[int, str] = CODE_MAPS["rf_rebound_outcome"]

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
    "roof_floor_method": tuple(ROOF_FLOOR_METHOD_CODE),
    "rf_rebound_outcome": tuple(RF_REBOUND_OUTCOME_CODE),
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

