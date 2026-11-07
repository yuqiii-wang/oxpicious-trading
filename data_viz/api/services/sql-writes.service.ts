/**
 * SQL write actions — the parameterized write SQL the API executes on
 * UI actions, read from the repo's database/sql_writes/ tree:
 *
 *   database/sql_writes/<app>/<layer>/<table>/<action>.sql
 *
 * (e.g. data_viz/api/secret_config/insert.sql — the Config → Secrets
 * tab's create; the app/layer prefix mirrors the executing code's dir
 * under data_viz/, so a path names its usage, not just its table).
 * The files carry $n node-postgres placeholders and document every
 * parameter in a header comment; they are the repo's canonical record
 * of WHAT the UI writes (the SQL-first convention applied to UI
 * actions), while the services own the parameters (validation,
 * encryption) and the routing (queryRows → primary pool for writes).
 *
 * Files are read fresh on every call — a handful per request at config
 * traffic rates, and an edited action file applies without an API
 * restart (same dev contract as the static-config trees).
 */
import fs from "fs";
import path from "path";

const SQL_WRITES_DIR = path.resolve(
  import.meta.dirname ?? __dirname,
  "..",
  "..",
  "..",
  "database",
  "sql_writes",
);

/** One dotted path segment of the sql_writes tree (app / layer /
 * table / action). Letters, digits and underscore only — a traversal
 * or odd segment never reaches the filesystem. */
const SEGMENT_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;

/**
 * Load one write action's SQL text. Throws (ENOENT) when the action
 * file does not exist — a missing action is a wiring bug, not an empty
 * result.
 */
export function loadSqlWrite(
  app: string, layer: string, table: string, action: string,
): string {
  for (const [name, seg] of [
    ["app", app],
    ["layer", layer],
    ["table", table],
    ["action", action],
  ] as const) {
    if (!SEGMENT_RE.test(seg)) {
      throw new Error(`sql_writes ${name} segment invalid: '${seg}'`);
    }
  }
  const file = path.join(SQL_WRITES_DIR, app, layer, table, `${action}.sql`);
  return fs.readFileSync(file, "utf-8");
}
