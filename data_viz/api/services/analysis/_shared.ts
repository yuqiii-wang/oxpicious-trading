import { stripExchangeSuffix } from "../../lib/classify-etf.js";

/** Strip the exchange suffix from a DB code ("510050.SS" → "510050").
 *  For index codes (already bare, e.g. "000300") this is a no-op. */
export function stripped(code: string): string {
  return stripExchangeSuffix(code);
}
