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
  | 'roof_floor_method'
  | 'rf_rebound_outcome';

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
  signal_type: { mov_rsi: 0, bollinger: 1, ma_cross: 2, options_wall: 3, options_vix: 4, momentum: 5, rf_break: 6 },
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
    rsi10_1pct: 300,
    rsi10_2pct: 301,
    rsi10_3pct: 302,
    rsi14_1pct: 400,
    rsi14_2pct: 401,
    rsi14_3pct: 402,
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
    // method (roof BUY / floor SELL; deepest breached window claims
    // the day). Plain = donchian (window extremes), _med_ = the
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
    // ovix_atr_break (2026-10-16): the volatility-expansion breakout
    // (spot day-return in ATR units beyond k while vol slope5 fires)
    ovix_atr_break: 4306,
    // ma_cross breadth additions (2026-10-16): the market-level
    // breadth rules — the Zweig thrust (BUY) + the 9:1 up/down days
    ma_breadth_thrust: 5700,
    ma_9to1_up: 5701,
    ma_9to1_dn: 5702,
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
  sec_type: { etf: 0, index: 1, stock: 2 },
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
  // quantitative roof/floor level estimate methods
  // (analysis.mov_ave_spreads_roof_floor_estimates.method; median = the
  // cross-method consensus row)
  roof_floor_method: { trendline: 0, donchian: 1, quantile: 2, channel: 3, boll: 4, median: 5 },
  // rebound-audit outcome of a recorded extreme
  // (analysis.mov_ave_spreads_roof_floor_rebounds.outcome)
  rf_rebound_outcome: { no_rebound: 0, never_retested: 1, held: 2, broken: 3, unresolved: 4 },
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
