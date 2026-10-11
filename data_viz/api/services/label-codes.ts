/**
 * Closed-vocabulary label <-> SMALLINT code maps — the TypeScript mirror
 * of stats.label_dim (database/sql/stats/21_label_dim.sql).
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
  | 'action'
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
  | 'db_replication_state';

/** label -> code, per domain (mirror of stats.label_dim). */
export const LABEL_CODE: Record<LabelDomain, Record<string, number>> = {
  regime: { calm: 0, hot: 1, panic: 2, quiet: 3 },
  side: { top: 0, bottom: 1, flat: 2 },
  action: { buy: 0, sell: 1 },
  // analysis_signals family identifier (converted 2026-10-10)
  signal_type: { mov_rsi: 0, bollinger: 1 },
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
 * TEXT label via stats.label_dim — SELECT-list use, keeps response rows
 * string-typed with zero TS churn. `col` is an internal SQL fragment
 * (column reference), never user input.
 */
export function labelDecodeSql(domain: LabelDomain, col: string): string {
  return `(SELECT d.label FROM stats.label_dim d WHERE d.domain = '${domain}' AND d.code = ${col})`;
}

/**
 * SQL scalar-subquery giving a pool_size column its PRESENTATION order
 * (stats.label_dim.sort_order — the pre-refactor alphabetical TEXT
 * order: all, large, mid, small), preserving API row order at
 * ORDER BY pool_size sites. Code order is semantic (small..all) and
 * would reorder listings.
 */
export const POOL_SIZE_SORT_SQL =
  "(SELECT d.sort_order FROM stats.label_dim d WHERE d.domain = 'pool_size' AND d.code = pool_size)";
