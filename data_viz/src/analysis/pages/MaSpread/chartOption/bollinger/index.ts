/**
 * Bollinger envelope group (the header dropdown) — envelope computation
 * + series for the pair chart and the Amt Envelope view, plus the
 * tooltip rows.
 */
export {
  computePairBollinger,
  appendBollingerSeries,
  type PairBollinger,
} from "./pairBollinger";
export {
  computeAmtBollinger,
  appendAmtBollingerSeries,
  type AmtBollinger,
} from "./amtBollinger";
export { appendBollingerRows } from "./tooltipRows";
