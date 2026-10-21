/**
 * Barrel re-export for the Industry Sentiments analysis service — the
 * split of the former monolithic industry-sentiments.ts into a directory
 * of smaller files:
 *
 *   meta.ts    cached per-index classification rows (composition-only)
 *   themes.ts  the two ThemeSelector trees
 *   chart.ts   per-industry / per-code close series + aggregation + benchmarks
 */
export {
  listIndustrySentimentsThemes,
  listIndustrySentimentsStrategyThemes,
} from "./themes.js";
export {
  getIndustrySentimentsChart,
  getIndustrySentimentsChartByCode,
} from "./chart.js";
