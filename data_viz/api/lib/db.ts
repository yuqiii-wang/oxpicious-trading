/**
 * Backward-compat re-export — the canonical DB service now lives at
 * `api/services/db.service.ts`. New code should import from there directly.
 */
export {
  type DbConfig,
  getDbConfig,
  getPool,
  query,
  queryRows,
  getClient,
  closePool,
  toDateParam,
  formatDate,
  toNum,
} from "../services/db.service.js";
