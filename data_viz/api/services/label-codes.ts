/**
 * Closed-vocabulary label <-> SMALLINT code maps — the TypeScript mirror
 * of static.label_dim (database/sql/static/01_label_dim.sql).
 *
 * CONVERTED columns (regime / side / action / weighting / rank_side /
 * pool_size / px_speed / vol_state / board / sec_owner_type /
 * contract_type / option_type / sec_type in the analysis schema's
 * converted tables + stats.options_terms + monitor.src_data_audit) store their
 * SMALLINT code in the DB; this module maps
 * request params to codes for WHERE clauses and provides the SQL-side
 * decode for SELECT lists (rows stay string-typed — the UI contract is
 * unchanged). PENDING domains (skew_type / attribution_type /
 * trade_side) are vocabulary only — their columns
 * are still TEXT, so no decode/encode on them until converted.
 *
 * temp_scripts/check_label_dim.py asserts this mirror stays identical
 * to the DB table and the Python mirror (_common/labels.py).
 */

export type LabelDomain =
  | 'regime'
  | 'side'
  | 'signal_type'
  | 'signal_sub_type'
  | 'action'
  | 'trend'
  | 'weighting'
  | 'rank_side'
  | 'pool_size'
  | 'pair_grain'
  | 'px_speed'
  | 'vol_state'
  | 'board'
  | 'sec_owner_type'
  | 'option_type'
  | 'skew_type'
  | 'contract_type'
  | 'sec_type'
  | 'attribution_type'
  | 'trade_side'
  | 'ext_resource'
  | 'ext_http_source'
  | 'ext_llm_provider'
  | 'db_instance'
  | 'db_replication_state'
  | 'analysis_cache_kind'
  | 'portfolio_type'
  | 'portfolio_sub_type'
  | 'roof_floor_method'
  | 'rf_rebound_outcome'
  | 'research_logic';

/** label -> code, per domain (mirror of static.label_dim). */
export const LABEL_CODE: Record<LabelDomain, Record<string, number>> = {
  regime: { calm: 0, hot: 1, panic: 2, quiet: 3, all: 4 },
  side: { top: 0, bottom: 1, flat: 2 },
  action: { buy: 0, sell: 1, wait: 2 }, // 2 wait (renamed from no_action 2026-10-16)
  // the code's own trend classification in force for a signal_rules
  // cell (the PK member that makes rule_params per-PK; 2026-10-14;
  // rise/fall redefined 2026-10-15 as the RSI re-entry episodes —
  // 5 unknown the unclassified bucket, 4 'all' the POOLED stamp of
  // the sparse non-trend-split families, never a day state)
  trend: { top: 0, bottom: 1, rise: 2, fall: 3, all: 4, unknown: 5 },
  // analysis_signals family identifier (converted 2026-10-10)
  // hp_rsi (8, 2026-10-18 two-layer merge): the hedge_pairs family's
  // RSI-sourced orchestration layer — the components' existing rsi
  // signal rules (type 0) are the source, orchestrated into
  // hp_entry_legs_rsi / hp_exit_mkt_high / hp_catchup_rsi_hi
  signal_type: { mov_rsi: 0, bollinger: 1, ma_cross: 2, options_wall: 3, options_vix: 4, momentum: 5, rf_break: 6, hedge_pairs: 7, hp_rsi: 8,
    // rebound (9, 2026-10-26): the drop→rebound→resolution family —
    // the ma-spread rebound study's graduates as rule cells, each in a
    // plain and a PE-conditioned variant (the is_pe_boosted PK axis)
    rebound: 9,
    // macd (10, 2026-10-26): the classic MACD crossover family — the
    // typical-EMA study's product (classic 12/26/9 spans; ema12/ema26
    // precomputed in tech_stats since the same day)
    macd: 10 },
  // the family config grid (converted 2026-10-11): SERIES codes
  // (curves) 0.., THRESHOLD codes (thresholds/history)
  // (series + 1) * 100 + width ordinal; the SINGLE-WIDTH retest
  // kinds use their series code for BOTH roles (label_dim is
  // UNIQUE(domain, label) — one label, one code; join: code < 100
  // ? code : code / 100 - 1)
  signal_sub_type: {
    rsi3: 0,
    rsi6: 1,
    rsi10: 2,
    rsi14: 3,
    rsi20: 4,
    rsi60: 5,
    boll5: 6,
    boll20: 7,
    boll60: 8,
    ma20_cross: 9,
    ma60_cross: 10,
    ma20_retest: 11,
    ma60_retest: 12,
    ma120_deep: 13,
    ma255_deep: 14,
    ma20_ecross: 15,
    ma60_ecross: 16,
    ma20_eretest: 17,
    ma60_eretest: 18,
    ma120_edeep: 19,
    ma255_edeep: 20,
    ma20_gap: 21,
    ma60_gap: 22,
    ma120_gap: 23,
    ma255_gap: 24,
    ma20_egap: 25,
    ma60_egap: 26,
    ma120_egap: 27,
    ma255_egap: 28,
    rsi3_1pct: 100,
    rsi3_2pct: 101,
    rsi3_3pct: 102,
    rsi6_1pct: 200,
    rsi6_2pct: 201,
    rsi6_3pct: 202,
    rsi6_5pct: 204,
    rsi6_10pct: 209,
    rsi10_1pct: 300,
    rsi10_2pct: 301,
    rsi10_3pct: 302,
    rsi10_5pct: 304,
    rsi10_10pct: 309,
    rsi14_1pct: 400,
    rsi14_2pct: 401,
    rsi14_3pct: 402,
    rsi14_5pct: 404,
    rsi14_10pct: 409,
    rsi20_1pct: 500,
    rsi20_2pct: 501,
    rsi20_3pct: 502,
    rsi60_1pct: 600,
    rsi60_2pct: 601,
    rsi60_3pct: 602,
    boll5_2std: 700,
    boll5_2p5std: 701,
    boll5_3std: 702,
    boll20_2std: 800,
    boll20_2p5std: 801,
    boll20_3std: 802,
    boll60_2std: 900,
    boll60_2p5std: 901,
    boll60_3std: 902,
    ma20_s11: 1000,
    ma20_s21: 1001,
    ma20_s42: 1002,
    ma60_s11: 1100,
    ma60_s21: 1101,
    ma60_s42: 1102,
    ma120_d8pct: 1400,
    ma120_d12pct: 1401,
    ma255_d8pct: 1500,
    ma255_d12pct: 1501,
    ma20_es11: 1600,
    ma20_es21: 1601,
    ma20_es42: 1602,
    ma60_es11: 1700,
    ma60_es21: 1701,
    ma60_es42: 1702,
    ma20_x1pct: 3000,
    ma20_x2pct: 3001,
    ma20_x3pct: 3002,
    ma60_x1pct: 3100,
    ma60_x2pct: 3101,
    ma60_x3pct: 3102,
    ma20_ex1pct: 3200,
    ma20_ex2pct: 3201,
    ma20_ex3pct: 3202,
    ma60_ex1pct: 3300,
    ma60_ex2pct: 3301,
    ma60_ex3pct: 3302,
    ma120_ed8pct: 2000,
    ma120_ed12pct: 2001,
    ma255_ed8pct: 2100,
    ma20_g3pct: 2200,
    ma20_g5pct: 2201,
    ma20_g8pct: 2202,
    ma60_g3pct: 2300,
    ma60_g5pct: 2301,
    ma60_g8pct: 2302,
    ma120_g3pct: 2400,
    ma120_g5pct: 2401,
    ma120_g8pct: 2402,
    ma255_g3pct: 2500,
    ma255_g5pct: 2501,
    ma255_g8pct: 2502,
    ma20_eg3pct: 2600,
    ma20_eg5pct: 2601,
    ma20_eg8pct: 2602,
    ma60_eg3pct: 2700,
    ma60_eg5pct: 2701,
    ma60_eg8pct: 2702,
    ma120_eg3pct: 2800,
    ma120_eg5pct: 2801,
    ma120_eg8pct: 2802,
    ma255_eg3pct: 2900,
    ma255_eg5pct: 2901,
    ma255_eg8pct: 2902,
    // options_wall (2026-10-12): the OI-wall breach bars — ow_put /
    // ow_call the SERIES identities (thresholds only, no curves),
    // ow_put_025pct BUY VRP-gated / ow_call_025pct + ow_call_100pct
    // SELL (same-day wall basis). The 2026-10-13 hit-and-bounce
    // additions: ow_put_center_vrp BUY (the close within 1% above the
    // put zone CENTER, VRP-gated; _rej the VRP-fail twin) /
    // ow_call_approach_rally SELL (the close within 1.5% below the
    // call cap on an up-day)
    ow_put: 40,
    ow_call: 41,
    ow_put_025pct: 4100,
    ow_put_025pct_rej: 4101,
    ow_put_center_vrp: 4102,
    ow_put_center_vrp_rej: 4103,
    ow_call_025pct: 4200,
    ow_call_100pct: 4201,
    ow_call_approach_rally: 4202,
    // options_vix (2026-10-13): the vol-index fear-gauge configs over
    // the corr_20d value space — ovix_buy / ovix_sell the SERIES
    // identities (thresholds only, no curves): ovix_spike BUY (corr <
    // -0.8 & 5d vol slope > +10%), ovix_spike_hivol BUY (spike & vol
    // z252 > 1), ovix_rollover BUY (corr < -0.8 & the vol peak
    // confirmed rolling over), ovix_fear_unwind SELL (corr crosses up
    // through -0.5 after a deep-fear episode — the exit companion);
    // the 2026-10-14 populate additions — ovix_deep_hivol BUY (corr <
    // -0.8 & vol z252 > 0.5, no spike — the established-fear-regime
    // set) and ovix_spike_wide BUY (the panic spike, fear bar widened
    // to corr < -0.75)
    ovix_buy: 42,
    ovix_sell: 43,
    ovix_spike: 4300,
    ovix_spike_hivol: 4301,
    ovix_rollover: 4302,
    ovix_deep_hivol: 4303,
    ovix_spike_wide: 4304,
    // ovix_panic_peak BUY (2026-10-15, the vol30 value space): vol
    // escalates >= 5% past its prior 120-session max while still
    // spiking (slope5 > +10%) AND spot below its 5-sessions-ago close
    // — the panic-PEAK escalation leg (fires at the crisis's new
    // extreme, not its onset; the downside leg separates fear from
    // upside euphoria)
    ovix_panic_peak: 4305,
    ovix_fear_unwind: 4400,
    ma255_ed12pct: 2101,
    // momentum (2026-10-15): the log-return momentum family — mom5 /
    // mom20 the FIXED 5/20d anchor SERIES, momshort / mommid / momlong
    // the VOL-ADAPTIVE lookback SERIES of analysis.mov_ave_momentum
    // (thresholds only, no curves): the nested 1/2/3% tail carve of
    // the axis's own trailing-10y log-return ln(1+momentum) quantile
    mom5: 50,
    mom20: 51,
    momshort: 52,
    mommid: 53,
    momlong: 54,
    mom5_1pct: 5100,
    mom5_2pct: 5101,
    mom5_3pct: 5102,
    mom20_1pct: 5200,
    mom20_2pct: 5201,
    mom20_3pct: 5202,
    momshort_1pct: 5300,
    momshort_2pct: 5301,
    momshort_3pct: 5302,
    mommid_1pct: 5400,
    mommid_2pct: 5401,
    mommid_3pct: 5402,
    momlong_1pct: 5500,
    momlong_2pct: 5501,
    momlong_3pct: 5502,
    // rf_break (2026-10-16): the roof/floor breakout family — the
    // close beyond the PRIOR day's roof/floor level per window x
    // method. RETIRED 2026-10-26 (the legacy cleanup): the catch-all
    // roof-buy / floor-sell entries 5600-5633 are decode-only
    // vocabulary — the family trades the deep tiers + floor bounce,
    // ALL-BUY. Plain = donchian (window extremes), _med_ = the
    // median consensus of the five estimate methods
    rf60_roof: 5600,
    rf120_roof: 5601,
    rf255_roof: 5602,
    rf500_roof: 5603,
    rf60_floor: 5610,
    rf120_floor: 5611,
    rf255_floor: 5612,
    rf500_floor: 5613,
    rf60_med_roof: 5620,
    rf120_med_roof: 5621,
    rf255_med_roof: 5622,
    rf500_med_roof: 5623,
    rf60_med_floor: 5630,
    rf120_med_floor: 5631,
    rf255_med_floor: 5632,
    rf500_med_floor: 5633,
    // rf_break deep tiers (2026-10-26, the depth-split study's
    // graduates): roof_deep BUY (depth-gated continuation beyond the
    // prior day's roof) and floor_deep BUY (the capitulation V-bottom
    // below the prior day's floor) — bars self-adaptive per cell
    rf60_roof_deep: 5640,
    rf120_roof_deep: 5641,
    rf255_roof_deep: 5642,
    rf500_roof_deep: 5643,
    rf60_floor_deep: 5650,
    rf120_floor_deep: 5651,
    rf255_floor_deep: 5652,
    rf500_floor_deep: 5653,
    rf60_med_roof_deep: 5660,
    rf120_med_roof_deep: 5661,
    rf255_med_roof_deep: 5662,
    rf500_med_roof_deep: 5663,
    rf60_med_floor_deep: 5670,
    rf120_med_floor_deep: 5671,
    rf255_med_floor_deep: 5672,
    rf500_med_floor_deep: 5673,
    // rf_break floor bounce (2026-10-26, the two-method gap study's
    // graduate): the close breaks the prior day's MEDIAN consensus
    // floor while the DONCHIAN wick floor still holds — the
    // consensus-broken / wick-intact bounce BUY (cross-method, one
    // population per window; t 3.0-4.5 at all four windows)
    rf60_floor_bounce: 5680,
    rf120_floor_bounce: 5681,
    rf255_floor_bounce: 5682,
    rf500_floor_bounce: 5683,
    // ovix_atr_break (2026-10-16): the volatility-expansion breakout
    // (spot day-return in ATR units beyond k while vol slope5 fires)
    ovix_atr_break: 4306,
    // ovix_euphoria_peak SELL (2026-10-19): the panic-peak MIRROR —
    // exit timing on the blow-off top (vol > prior 120-session max x
    // 1.05 & slope5 > +10% & spot ABOVE its 5-sessions-ago close; the
    // upside-euphoria leg the buy legs exclude, 2024-10-08 the
    // canonical catch)
    ovix_euphoria_peak: 4307,
    // ma_cross breadth additions (2026-10-16): the market-level
    // breadth rules — the Zweig thrust (BUY) + the 9:1 up/down days
    ma_breadth_thrust: 5700,
    ma_9to1_up: 5701,
    ma_9to1_dn: 5702,
    // hedge_pairs (2026-10-18, block 5800+): the discoveries of the
    // long–long anti-correlated pair study — entry = broad-market
    // drawdown gate x both legs' sigma-depth; harvest = the staged
    // volatility-harvest exits on the pair ratio z (sell the
    // relatively-high leg, then the laggard at its own high); stops
    // = the beta stop + the hedge-regime break; regime_flip = the
    // observational hedgeable turn-on
    hp_entry_dd5_z1: 5800,
    hp_entry_dd5_z2: 5801,
    hp_entry_dd10_z1: 5802,
    hp_entry_dd10_z2: 5803,
    hp_harvest_z15: 5804,
    hp_harvest_z20: 5805,
    hp_catchup_z1: 5806,
    hp_catchup_z2: 5807,
    hp_stop_mkt_dd20: 5808,
    hp_stop_mkt_dd30: 5809,
    hp_stop_regime: 5810,
    hp_regime_flip: 5811,
    // the 2026-10-18 market-decoupling rework: both legs' own
    // Short-Term-Tech RSI tail signals paired within ~5 days (entry),
    // the laggard's own RSI high-tail (catch-up) — the market-drawdown
    // entries / MA20-z catch-ups / beta stops above are RETIRED
    hp_entry_legs_rsi: 5812,
    hp_catchup_rsi_hi: 5813,
    hp_exit_mkt_high: 5814,
    // the 2026-10-19 more-triggers graduates: the rf washout entries
    // (both legs' Donchian-floor witnesses within 5d; the _mkt twin
    // adds the market's RSI 3pct capitulation witness), the deep
    // hedgeable turn-on (offset rho <= -0.50), and the catch-up entry
    // (the weak leg re-crossing its own MA60); the per-window trio
    // 5815-5817 is RETIRED (2026-10-21 dedup — the window is a
    // run_cells candidate, not an identity)
    hp_entry_wash60: 5815,
    hp_entry_wash120: 5816,
    hp_entry_wash255: 5817,
    hp_entry_wash120_mkt: 5818,
    hp_entry_rho50: 5819,
    hp_entry_catchup_reclaim60: 5820,
    // the 2026-10-20 spread-rotation study's momentum tilt: the
    // harvest's opposite pair — hp_mom_strong BUYs the strong leg,
    // hp_mom_lag SELLs the laggard (the k grid {1.5, 2.0, 2.5} is
    // the run_cells candidate set)
    hp_mom_strong: 5821,
    hp_mom_lag: 5822,
    // the 2026-10-21 dedup's merged harvest (the z15/z20 pair's one
    // most-semantic survivor — the |ratio z| bar is a run_cells
    // candidate in bar_def.ratio_z); hp_regime_flip (5811) retired —
    // hp_entry_rho50 is the hedgeable turn-on's one record
    hp_harvest: 5823,
    // the 2026-10-21 dedup's merged washout entry (the per-window
    // trio's one most-semantic survivor — the W window is a
    // run_cells candidate in bar_def.window)
    hp_entry_wash: 5824,
    // the 2026-10-23 more-triggers graduates II (docs/
    // portfolio_more_triggers_study_2026-10-23.md): the coordination
    // widening subs — every member a sourced cell on >= 2 codes:
    // hp_entry_rsi_legs = both legs' RSI capitulation, market-free;
    // hp_entry_wash_rsi = one leg's floor breakdown + the other leg's
    // RSI-3pct witness; hp_entry_wash_mkt1 = one leg's floor
    // breakdown + the market's RSI-3pct witness. The coordinated
    // SELL side measured OUT (tops continue).
    hp_entry_rsi_legs: 5825,
    hp_entry_wash_rsi: 5826,
    hp_entry_wash_mkt1: 5827,
    // the fresh typical-solution trio (2026-10-24, user-directed):
    // hp_entry_trio = all three curves (both offset-opposite legs +
    // the broad market 000300) in their own RSI capitulation tails the
    // same day — the co-wash BUY of the inverse-vol long-long pair,
    // the ONE shipped sub type; hp_exit_trio = the blow-off mirror,
    // the engine's MEASURED asymmetry check (thin — tops continue;
    // rows discarded, registered for decode only)
    hp_entry_trio: 5828,
    hp_exit_trio: 5829,
    // the simplified portfolio's per-curve witness record — payload
    // vocabulary only, no rule rows carry it. 2026-10-25: the per-ROLE
    // labels supersede 5830 for new rows (market / leg A / leg B each
    // their own sub type); 5830 stays for old-history decode
    hp_rsi_witness: 5830,
    hp_rsi_witness_mkt: 5831,
    hp_rsi_witness_leg_a: 5832,
    hp_rsi_witness_leg_b: 5833,
    // the SPREAD one-buy-one-sell sub (2026-10-25, user-directed "one
    // buy one sell by the spread between two negative corr curves,
    // disregarding the benchmark curve"): hp_entry_spread = the pair's
    // long-short SPREAD curve (w_a*r_a - w_b*r_b compounded, the
    // fetch's own lagged inverse-vol weights, NO market curve) in its
    // own Wilder-RSI(14/10) bottom tail vs its own adaptive percentile
    // bar — the classic pairs CONVERGENCE trade: BUY the laggard leg
    // A, SELL the leader leg B (response = the spread value's forward
    // blend). hp_exit_spread = the TOP-TAIL EXIT, shipped 2026-10-25
    // evening (the spread at its RSI exhaustion tail: SELL the leader
    // leg A / BUY the laggard leg B — exit timing, never pooled-gated);
    // hp_spread_buy_leg / hp_spread_sell_leg = the event's per-ROLE
    // witness records (payload vocabulary, no rule rows behind them)
    hp_entry_spread: 5834,
    hp_spread_buy_leg: 5835,
    hp_spread_sell_leg: 5836,
    hp_exit_spread: 5837,
    // the REBOUND family (block 5900+, 2026-10-26): the
    // drop→rebound→resolution sub types per anchor window —
    // capit = the capitulation trough-break BUY, fail = the failed
    // reclaim SELL (the stall at 0.90–1.0 recovery), reclaim = the
    // full-reclaim continuation BUY; each emits plain +
    // PE-conditioned cells (is_pe_boosted)
    rebound_capit_60: 5900,
    rebound_capit_255: 5901,
    rebound_fail_60: 5902,
    rebound_fail_255: 5903,
    rebound_reclaim_60: 5904,
    rebound_reclaim_255: 5905,
    // the MACD family (block 6000+, 2026-10-26): the classic
    // signal-line crossover sub types over the classic 12/26/9 spans —
    // macd_cross_up BUY (the momentum turn) / macd_cross_dn SELL
    // (exit timing)
    macd_cross_up: 6000,
    macd_cross_dn: 6001,
  },
  weighting: { equal: 0, amt: 1 },
  rank_side: { HYPE: 0, DRAIN: 1 },
  pool_size: { small: 0, mid: 1, large: 2, all: 3 },
  px_speed: { sharp_dn: 0, slow_dn: 1, flat: 2, slow_up: 3, sharp_up: 4 },
  vol_state: { shrink: 0, normal: 1, heavy: 2 },
  board: { MAIN: 0, STAR: 1, GEM: 2, BSE: 3 },
  sec_owner_type: {
    fund_manager: 0,
    broker: 1,
    asset_manager: 2,
    insurance: 3,
    bank: 4,
    company: 5,
    other: 6,
  },
  option_type: { CALL: 0, PUT: 1 },
  skew_type: {
    oi_moneyness: 0,
    iv_smile: 1,
    greek_delta: 2,
    greek_gamma: 3,
    greek_vega: 4,
  },
  contract_type: { index: 0, bond: 1 },
  sec_type: { etf: 0, index: 1, stock: 2, industry: 3 },
  pair_grain: { index: 0, industry: 1 },
  attribution_type: { equal: 0, trading_amt: 1 },
  trade_side: { BUY: 0, SELL: 1 },
  // monitor.external_resource_audit (sub_type's domain is selected by
  // resource_type: http -> ext_http_source, llm -> ext_llm_provider)
  ext_resource: { http: 0, llm: 1 },
  ext_http_source: {
    sse: 0,
    szse: 1,
    bse: 2,
    cffex: 3,
    csindex: 4,
    cnindex: 5,
    chinabond: 6,
    shibor: 7,
    huatai: 8,
    eastmoney: 9,
    cninfo: 10,
    gov: 11,
    pboc: 12,
    zhihu: 13,
    sina: 14,
    other: 99,
  },
  ext_llm_provider: { zhipu: 0, other: 99 },
  // monitor.db_health (replication_state is the WAL shipping state as seen
  // from the SAMPLED instance; none = no standby attached / receiver absent)
  db_instance: { primary: 0, replica: 1 },
  db_replication_state: {
    streaming: 0,
    catchup: 1,
    startup: 2,
    backup: 3,
    stopping: 4,
    connecting: 5,
    stopped: 6,
    none: 99,
  },
  // analysis_signals.analysis_cache.kind — the generic
  // intermediate-result cache's tenant (which analysis owns the row)
  analysis_cache_kind: { boost_derivation: 0 },
  // the kind of PORTFOLIO an analysis_signals.portfolio_signal_rules
  // row groups (the portfolio tier's OWN vocabulary — what the code IS
  // as a portfolio, not the signal family): 0 = the two-leg long–long
  // negatively-correlated hedge pair ('A~B' composite code)
  portfolio_type: { neg_corr_hedge_pair: 0 },
  // WHICH PORTFOLIO RULE a portfolio_signal_rules row is within its
  // portfolio_type (2026-10-25 split — one row per code x type x sub;
  // the registered mov_rsi cells map INTO these rows through
  // signal_rule_ids only): 0 = the trio co-wash (hp_entry_trio),
  // 1 = the long-short spread entry/exit (hp_entry_/hp_exit_spread)
  portfolio_sub_type: { trio_wash: 0, spread: 1 },
  // quantitative roof/floor level estimate methods
  // (analysis.mov_ave_spreads_roof_floor_estimates.method; median = the
  // cross-method consensus row)
  roof_floor_method: { trendline: 0, donchian: 1, quantile: 2, channel: 3, boll: 4, median: 5 },
  // rebound-audit outcome of a recorded extreme
  // (analysis.mov_ave_spreads_roof_floor_rebounds.outcome)
  rf_rebound_outcome: { no_rebound: 0, never_retested: 1, held: 2, broken: 3, unresolved: 4 },
  // text.llm_qa_by_research.logic_label — the dominant investing logic a
  // research-logic Q&A row classifies; 99 other = the fallback (did not
  // fit / unclassifiable). First text.* LLM-layer enum converted
  // (2026-10-27)
  research_logic: {
    growth: 0,
    value: 1,
    dividend: 2,
    commodity: 3,
    cyclical: 4,
    defensive: 5,
    quality: 6,
    theme: 7,
    other: 99,
  },
};

/** code -> label, per domain. */
export const LABEL_DECODE: Record<LabelDomain, Record<number, string>> =
  Object.fromEntries(
    (Object.keys(LABEL_CODE) as LabelDomain[]).map((domain) => [
      domain,
      Object.fromEntries(
        Object.entries(LABEL_CODE[domain]).map(([label, code]) => [
          code,
          label,
        ]),
      ),
    ]),
  ) as Record<LabelDomain, Record<number, string>>;

/** Decode a coded value from a DB row to its label (response boundary). */
export function decodeLabel(
  domain: LabelDomain,
  code: number | null | undefined,
): string | null {
  if (code === null || code === undefined) return null;
  return LABEL_DECODE[domain][code] ?? null;
}

/** Map a request param to its code, or null when not a valid label. */
export function encodeLabelParam(domain: LabelDomain, value: string): number | null {
  const code = LABEL_CODE[domain][value];
  return code === undefined ? null : code;
}

/**
 * SQL scalar-subquery that decodes a SMALLINT label-code column to its
 * TEXT label via static.label_dim — SELECT-list use, keeps response rows
 * string-typed with zero TS churn. `col` is an internal SQL fragment
 * (column reference), never user input.
 */
export function labelDecodeSql(domain: LabelDomain, col: string): string {
  return `(SELECT d.label FROM static.label_dim d WHERE d.domain = '${domain}' AND d.code = ${col})`;
}

/**
 * SQL scalar-subquery giving a pool_size column its PRESENTATION order
 * (static.label_dim.sort_order — the pre-refactor alphabetical TEXT
 * order: all, large, mid, small), preserving API row order at
 * ORDER BY pool_size sites. Code order is semantic (small..all) and
 * would reorder listings.
 */
export const POOL_SIZE_SORT_SQL =
  "(SELECT d.sort_order FROM static.label_dim d WHERE d.domain = 'pool_size' AND d.code = pool_size)";
