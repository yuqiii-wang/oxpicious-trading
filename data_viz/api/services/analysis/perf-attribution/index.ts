/**
 * Barrel re-export for the Performance Attribution analysis service — the
 * split of the former monolithic perf-attribution.ts into a directory of
 * smaller files:
 *
 *   _shared.ts      name/meta tables + per-code source resolution
 *   codes.ts        listPerfAttrCodes
 *   attribution.ts  getPerfAttrAttribution (per-code benchmark rows)
 *   chart.ts        getPerfAttrChart (paired history + linked ETFs)
 *   themes.ts       the two ThemeSelector trees
 */
export { listPerfAttrCodes } from "./codes.js";
export { getPerfAttrAttribution } from "./attribution.js";
export { getPerfAttrChart } from "./chart.js";
export {
  listPerfAttrThemes,
  listPerfAttrStrategyThemes,
} from "./themes.js";
