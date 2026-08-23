/**
 * Thin re-export so the App route can import
 * `@/analysis/pages/EtfHoldingsPage` like its sibling commons pages. The
 * actual implementation lives in the `./EtfHoldings/` subdirectory
 * (mirroring FourierFreqs/PeAndDividend/MaSpread).
 */
export { default } from "./EtfHoldings";
