/**
 * Backward-compat re-export — the canonical DB service now lives at
 * `api/services/infra/db.service.ts`. New code should import from there directly.
 */
export {
  type DbConfig,
  getDbConfig,
  getPool,
  query,
  queryRows,
  queryReadWithSession,
  getClient,
  closePool,
  toDateParam,
  formatDate,
  toNum,
} from "../services/infra/db.service.js";
