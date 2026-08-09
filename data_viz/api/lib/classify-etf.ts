/**
 * ETF code utilities — re-exports from the shared module.
 *
 * The actual implementation has been hoisted to src/shared/utils/classify.ts
 * so that BOTH the frontend (src/) and the backend API (api/) import from a
 * single source of truth.  This file remains as a thin re-export shim so
 * existing `import { ... } from "../lib/classify-etf.js"` calls in the API
 * layer continue to work without modification.
 */
export {
  stripExchangeSuffix,
  matchesExchange,
  EXCHANGE_OPTIONS,
  PRIMARY_EXCHANGE_OPTIONS,
  SECONDARY_EXCHANGE_OPTIONS,
} from "../../src/shared/utils/classify.js";
