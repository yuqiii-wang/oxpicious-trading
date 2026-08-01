/**
 * ETF code utilities.
 *
 * ETF/stock classification (L1 sector + L2 industry) is now precomputed by
 * build_classification.py and stored in stats.sec_classification.
 * The TS backend reads the precomputed columns — no classification logic lives here.
 */

/**
 * Strip .SS / .SZ / .BJ / .HK suffix from a stock or ETF code.
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

