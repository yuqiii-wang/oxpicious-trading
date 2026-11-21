/**
 * Volatility service — the codes list + one security's daily
 * volatility series behind the Commons Volatility page
 * (/analysis/commons/volatility). Reads analysis.volatility (built by
 * analysis.volatility; structured for all security sec_types, run
 * scope = index only for now) + the display names from
 * stats.index_identity. All SQL in the sql_views templates under
 * database/sql_views/data_viz/api/services/analysis/volatility/.
 */
export {
  listVolatilityCodes,
  getVolatilitySeries,
} from "./chart.js";
