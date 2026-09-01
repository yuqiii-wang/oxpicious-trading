/** Probe: sequence + _mig traces in the API-side DB. */
import { queryRows, closePool } from "../api/lib/db.js";

async function main() {
  const seqs = await queryRows<{ s: string }>(
    "SELECT sequencename AS s FROM pg_sequences WHERE schemaname = 'analysis_forecasts'",
  );
  console.log("sequences:", seqs);
  const mig = await queryRows<{ t: string }>(
    "SELECT table_name AS t FROM information_schema.tables WHERE table_schema = '_mig' ORDER BY 1",
  );
  console.log("_mig tables:", mig);
  try {
    const rows = await queryRows<Record<string, unknown>>(
      "SELECT * FROM _mig.migrations ORDER BY 1 DESC LIMIT 5",
    );
    console.log("recent migrations:", rows);
  } catch {
    console.log("(no _mig.migrations)");
  }
  const part = await queryRows<{ c: string }>(
    "SELECT count(*)::text AS c FROM pg_class WHERE relname LIKE 'forecast_results%'",
  );
  console.log("relations named forecast_results%:", part);
  const all = await queryRows<{ n: string }>(
    "SELECT relname AS n FROM pg_class WHERE relname LIKE '%forecast%' ORDER BY 1",
  );
  console.log("relations like %forecast%:", all.map((r) => r.n));
  await closePool();
}

main().catch(async (e) => {
  console.error("FAILED:", e);
  await closePool();
  process.exit(1);
});
