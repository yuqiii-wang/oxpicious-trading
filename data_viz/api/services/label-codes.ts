/**
 * Closed-vocabulary label <-> SMALLINT code maps — the TypeScript mirror
 * of stats.label_dim (database/sql/stats/21_label_dim.sql).
 *
 * The converted label columns (regime / side / action / weighting /
 * rank_side / pool_size) store their SMALLINT code in the DB; this
 * module maps request params to codes for WHERE clauses and provides
 * the SQL-side decode for SELECT lists (rows stay string-typed — the
 * UI contract is unchanged).
 *
 * temp_scripts/check_label_dim.py asserts this mirror stays identical
 * to the DB table and the Python mirror (_common/labels.py).
 */

export type LabelDomain =
  | 'regime'
  | 'side'
  | 'action'
  | 'weighting'
  | 'rank_side'
  | 'pool_size';

/** label -> code, per domain (mirror of stats.label_dim). */
export const LABEL_CODE: Record<LabelDomain, Record<string, number>> = {
  regime: { calm: 0, hot: 1, panic: 2, quiet: 3 },
  side: { top: 0, bottom: 1 },
  action: { buy: 0, sell: 1 },
  weighting: { equal: 0, amt: 1 },
  rank_side: { HYPE: 0, DRAIN: 1 },
  pool_size: { small: 0, mid: 1, large: 2, all: 3 },
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
