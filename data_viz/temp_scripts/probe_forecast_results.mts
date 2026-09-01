/** Probe: does forecast_results exist from the API's connection? */
import { queryRows, closePool } from "../api/lib/db.js";

async function main() {
  const t = await queryRows<{ t: string; kind: string }>(
    "SELECT table_name AS t, table_type AS kind FROM information_schema.tables "
    + "WHERE table_schema = 'analysis_forecasts' AND table_name LIKE 'forecast%' ORDER BY 1",
  );
  console.log("forecast tables:", t);
  try {
    const n = await queryRows<{ n: string }>(
      "SELECT count(*)::text AS n FROM analysis_forecasts.forecast_results",
    );
    console.log("forecast_results rows:", n[0]?.n);
  } catch (e) {
    console.log("count failed:", String(e).slice(0, 200));
  }
  // the exact failing JOIN
  try {
    const r = await queryRows<{ code: string; stat_month: string }>(
      `SELECT m.code, m.stat_month::text
       FROM analysis_forecasts.mov_rsi m
       JOIN analysis_forecasts.forecast_results f ON f.forecast_id = m.forecast_id
       LIMIT 1`,
    );
    console.log("join ok:", r);
  } catch (e) {
    console.log("join failed:", String(e).slice(0, 300));
  }
  await closePool();
}

main().catch(async (e) => {
  console.error("FAILED:", e);
  await closePool();
  process.exit(1);
});
