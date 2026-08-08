/**
 * Shared classification utilities — used by BOTH the frontend (src/) and the
 * backend API (api/).  Previously these lived in api/lib/classify-etf.ts which
 * made them inaccessible to frontend code; they have been hoisted here so
 * both layers import from a single source of truth.
 *
 * ETF/stock classification (L1 sector + L2 industry) is precomputed by
 * build_classification.py and stored in stats.sec_classification.  These
 * helpers operate on the exchange suffix and exchange-group mapping only —
 * no classification RULES live here.
 */

/**
 * Strip .SS / .SZ / .BJ / .HK suffix from a stock or ETF code.
 * For bare index codes (e.g. "000300") this is a no-op.
 */
export function stripExchangeSuffix(code: string): string {
  const s = String(code || "").trim();
  return s.replace(/\.(SS|SZ|BJ|HK)$/i, "");
}

/**
 * Exchange group mapping — maps the UI filter value to the set of
 * sec_classification.exchange column values that belong to that exchange.
 * SS includes STAR (科创板), SZ includes GEM (创业板) — both are sub-boards
 * of SSE and SZSE respectively.
 */
const EXCHANGE_GROUPS: Record<string, string[]> = {
  SS: ["SS", "STAR"],
  SZ: ["SZ", "GEM"],
  BJ: ["BJ"],
};

/**
 * Check whether a row's exchange matches the given filter.
 * @param rowExchange  The exchange value from sec_classification (may be NULL/empty).
 * @param filter       The UI filter value: 'SS', 'SZ', 'BJ', or null/empty for "All".
 * @returns true if the row matches the filter (or filter is "All").
 */
export function matchesExchange(
  rowExchange: string | null | undefined,
  filter: string | null | undefined,
): boolean {
  if (!filter) return true;
  const group = EXCHANGE_GROUPS[filter];
  if (!group) return false;
  if (!rowExchange) return false;
  return group.includes(rowExchange);
}

/** Exchange filter options for UI dropdowns/chips.
 *  Maps to sec_classification.exchange column. */
export const EXCHANGE_OPTIONS: Array<{ value: string | null; label: string }> = [
  { value: null, label: "All" },
  { value: "SS", label: "SSE" },
  { value: "SZ", label: "SZSE" },
  { value: "BJ", label: "BSE" },
];
