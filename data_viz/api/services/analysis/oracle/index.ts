/**
 * Oracle service — the codes list + one security's hindsight decision
 * history behind the Signals → Oracle page (/analysis/signals/oracle).
 * Reads analysis.oracle_signals (built by analysis.oracle_signals —
 * the hindsight ORACLE tier, DECOUPLED from the analysis_signals rule
 * tier; run scope = index only for now, the 10 study indices). All SQL
 * in the sql_views templates under
 * database/sql_views/data_viz/api/services/analysis/oracle/.
 */
export {
  listOracleCodes,
  getOracleSeries,
} from "./chart.js";
