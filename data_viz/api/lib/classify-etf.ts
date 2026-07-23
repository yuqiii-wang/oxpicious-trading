/**
 * ETF code utilities.
 *
 * ETF/stock classification (L1 sector + L2 industry) is now precomputed by
 * build_etf_classification.py (via _classification.classify_etf_full()) and
 * stored in stats.etf_meta.  The TS backend reads the precomputed columns —
 * no classification logic lives here.
 */

/**
 * Strip .SS / .SZ / .BJ / .HK suffix from a stock or ETF code.
 */
export function stripExchangeSuffix(code: string): string {
  const s = String(code || "").trim();
  return s.replace(/\.(SS|SZ|BJ|HK)$/i, "");
}
