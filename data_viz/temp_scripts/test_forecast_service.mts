/** Direct test of getForecastTable (bypasses the running API server). */
import { getForecastTable } from "../api/services/analysis/analysis-forecasts.js";
import { queryRows, closePool } from "../api/lib/db.js";

async function main() {
  const who = await queryRows<{ db: string; inet: string; port: number }>(
    "SELECT current_database() AS db, inet_server_addr()::text AS inet, inet_server_port() AS port",
  );
  console.log("connected to:", who[0]);
  const tables = await queryRows<{ t: string }>(
    "SELECT table_schema || '.' || table_name AS t FROM information_schema.tables "
    + "WHERE table_schema = 'analysis_forecasts' ORDER BY 1",
  );
  console.log("analysis_forecasts tables:", tables.map((t) => t.t));
  const cnt = await queryRows<{ n: string }>(
    "SELECT count(*)::text AS n FROM analysis_forecasts.mov_rsi",
  );
  console.log("mov_rsi rows:", cnt[0]?.n);

  for (const kind of ["mov_rsi", "mov_std"] as const) {
    const res = await getForecastTable("index", "000300", kind);
    console.log(`kind=${kind} rows=${res.rows.length}`);
    console.log(JSON.stringify(res.rows[0], null, 1));
  }
  try {
    await getForecastTable("index", "000300", "bogus");
    console.log("ERROR: bogus kind accepted");
  } catch (e) {
    console.log("bogus kind rejected:", String(e).slice(0, 80));
  }
  try {
    await getForecastTable("bad", "000300", "mov_rsi");
    console.log("ERROR: bad sec_type accepted");
  } catch (e) {
    console.log("bad sec_type rejected:", String(e).slice(0, 80));
  }
  await closePool();
}

main().catch(async (e) => {
  console.error("FAILED:", e);
  await closePool();
  process.exit(1);
});
