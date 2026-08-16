/**
 * Thin re-export so existing imports of `@/analysis/pages/FourierFreqsPage`
 * continue to resolve. The actual implementation lives in the
 * `./FourierFreqs/` subdirectory (split into smaller modules, mirroring
 * PeAndDividend/).
 */
export { default } from "./FourierFreqs";
