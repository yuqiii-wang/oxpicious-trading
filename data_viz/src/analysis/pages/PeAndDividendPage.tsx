/**
 * Thin re-export so existing imports of `@/analysis/pages/PeAndDividendPage`
 * continue to resolve. The actual implementation lives in the
 * `./PeAndDividend/` subdirectory (split into smaller modules, mirroring
 * MaSpread/).
 */
export { default } from "./PeAndDividend";
