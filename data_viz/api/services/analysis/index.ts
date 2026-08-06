/**
 * Barrel re-export for the analysis service modules.
 * Extracted from the former analysis.service.ts.
 */
export { listMovAveSpreadCodes, getMovAveSpreadChart, listMovAveSpreadThemes } from "./mov-ave-spreads.js";
export { listPerfAttrCodes, getPerfAttrAttribution, listPerfAttrThemes, getPerfAttrChart } from "./perf-attribution.js";
export { listIndustrySentimentsThemes, getIndustrySentimentsChart } from "./industry-sentiments.js";
export { getIndustryCorrelations } from "./industry-correlations.js";
export { getBenchmarkPriceChart, getIndustryAttributionPriceSeries } from "./benchmark-price-chart.js";
export { getAllIndustriesAttribution, getMemberIndexAttribution } from "./industry-attribution-bars.js";
export { getIndustryBenchmarkAttribution, listIndustryAttributionBenchmarks } from "./industry-benchmark-attribution.js";
export { getIndustryEtfPriceSeries, getIndustryEtfContributionBars } from "./industry-etf-contribution.js";
