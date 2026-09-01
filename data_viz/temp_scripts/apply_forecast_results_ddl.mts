/** Apply 01_forecast_results.sql (table + partitions) via the API's pool. */
import fs from "fs";
import path from "path";
import { queryRows, getClient, closePool } from "../api/lib/db.js";

async function main() {
  const ddl = fs.readFileSync(
    path.join("..", "database", "sql", "analysis", "analysis_forecasts", "01_forecast_results.sql"),
    "utf-8",
  );
  const client = await getClient();
  try {
    await client.query(ddl);
    console.log("01_forecast_results.sql applied");
  } finally {
    client.release();
  }
  const t = await queryRows<{ t: string }>(
    "SELECT table_name AS t FROM information_schema.tables "
    + "WHERE table_schema = 'analysis_forecasts' AND table_name LIKE 'forecast%' ORDER BY 1 LIMIT 3",
  );
  console.log("now forecast tables:", t.map((x) => x.t));

  // Park the identity sequence beyond the max forecast_id already stored
  // in the mov tables (their rows will be rewritten by the next --force
  // backfill, but keep the sequence safe for any incremental writes).
  const mx = await queryRows<{ m: string | null }>(
    "SELECT greatest("
    + "(SELECT max(forecast_id) FROM analysis_forecasts.mov_rsi), "
    + "(SELECT max(forecast_id) FROM analysis_forecasts.mov_std))::text AS m",
  );
  console.log("max existing forecast_id:", mx[0]?.m);
  if (mx[0]?.m) {
    await queryRows(
      "SELECT setval('analysis_forecasts.forecast_results_forecast_id_seq', $1::bigint)",
      [Number(mx[0].m)],
    );
    console.log("sequence parked at", mx[0].m);
  }
  await closePool();
}

main().catch(async (e) => {
  console.error("FAILED:", e);
  process.exit(1);
});
