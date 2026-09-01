/** Probe: column shape of mov_rsi in the API-side DB. */
import { queryRows, closePool } from "../api/lib/db.js";

async function main() {
  const cols = await queryRows<{ c: string }>(
    "SELECT column_name AS c FROM information_schema.columns "
    + "WHERE table_schema = 'analysis_forecasts' AND table_name = 'mov_rsi' ORDER BY ordinal_position",
  );
  console.log("mov_rsi cols:", cols.map((x) => x.c).join(", "));
  const months = await queryRows<{ m: string; n: string }>(
    "SELECT stat_month::text AS m, count(*)::text AS n FROM analysis_forecasts.mov_rsi "
    + "GROUP BY 1 ORDER BY 1 DESC LIMIT 3",
  );
  console.log("latest months:", months);
  await closePool();
}

main().catch(async (e) => {
  console.error("FAILED:", e);
  await closePool();
  process.exit(1);
});
