import { closePool, queryRows } from "./api/services/db.service.js";
const iso = (d: any) => new Date(d).toISOString().slice(0, 10);

// 1. which stats tables carry the golden-week dates for 000001?
for (const [tbl, sel] of [
  ["index_identity", "SELECT date FROM stats.index_identity WHERE code='000001'"],
  ["index_basic_stats", "SELECT date, close, trading_amount FROM stats.index_basic_stats WHERE code='000001'"],
  ["index_tech_stats", "SELECT date, ma5 FROM stats.index_tech_stats WHERE code='000001'"],
] as const) {
  const rows = await queryRows<any>(`${sel} AND date BETWEEN '2025-09-29' AND '2025-10-10' ORDER BY date`);
  console.log(tbl + ":", rows.map((r) => iso(r.date) + (r.close != null ? ` close=${r.close} amt=${r.trading_amount}` : "") + (r.ma5 != null ? ` ma5=${r.ma5}` : "")).join(" | ") || "(no rows)");
}

// 2. how many non-trading rows total? compare row counts between two windows
const cnt = await queryRows<any>(`
  SELECT count(*)::int AS n FROM stats.index_identity
  WHERE code='000001' AND date BETWEEN '2025-01-01' AND '2025-12-31'`);
console.log("index_identity rows in 2025 (trading days expected ~242):", cnt[0].n);
await closePool();
