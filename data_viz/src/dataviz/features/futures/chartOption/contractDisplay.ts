import type { FuturesContractMeta } from "@shared/types";

/**
 * Contract display names — the CFFEX shape every onshore futures code
 * already carries ("IF2607" = product + 2-digit year + 2-digit month).
 *
 * The offshore families encode the month as a letter ("MCA26X" = Nov 2026,
 * "CN26V" = Oct 2026), which is unreadable against the onshore codes in the
 * same UI. Translation rebuilds the name from the contract's
 * contract_year_month ("2026-11" → "MCA2611"), so onshore codes rebuild to
 * themselves unchanged and the whole tooltip/dot label set reads in one
 * convention. Codes without a parseable year-month (e.g. the SGX
 * continuous "CN00Y", year-month "0000-00") stay as-is.
 */
export function translateContractCode(
  code: string,
  contractYearMonth: string,
): string {
  const ym = /^(\d{4})-(\d{2})$/.exec(contractYearMonth);
  const product = /^[A-Z]+/.exec(code)?.[0] ?? "";
  if (!ym || !product) return code;
  const [, year, month] = ym;
  return `${product}${year.slice(2)}${month}`;
}

/** code → display-name map for one product's contract list. */
export function buildContractDisplayNames(
  contracts: FuturesContractMeta[],
): Map<string, string> {
  const map = new Map<string, string>();
  for (const c of contracts) {
    map.set(c.code, translateContractCode(c.code, c.contract_year_month));
  }
  return map;
}
