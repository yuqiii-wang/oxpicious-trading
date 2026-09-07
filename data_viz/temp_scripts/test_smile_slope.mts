/**
 * Smoke test for smileSlopeCompute.computeSmileSlope (run: npx tsx).
 *
 * Synthetic chains with KNOWN smile tilt:
 *   • put-heavy smile (IV falls with strike): tilt must be clearly NEGATIVE
 *   • flat smile: tilt ≈ 0
 *   • call-heavy smile: tilt clearly POSITIVE
 * plus a 6-day series to exercise the expanding corr + per-expiry groups.
 */
import type { OptionsRow } from "../shared/types";

let contractSeq = 0;

function mkRow(opts: {
  date: string;
  expiryDate: string;
  strike: number; // raw 厘 (yuan × 1000)
  type: "CALL" | "PUT";
  iv: number;
  oi: number;
  spot: number; // raw 厘
}): OptionsRow {
  contractSeq += 1;
  return {
    date: opts.date,
    contract_code: `C${contractSeq}`,
    contract_name: `test-${contractSeq}`,
    underlying_code: "10005683",
    underlying_name: "test ETF",
    option_type: opts.type,
    expiry_month: opts.expiryDate.slice(0, 7),
    expiry_date: opts.expiryDate,
    days_to_expiry: 30,
    strike_price: opts.strike,
    settle: 0.05,
    underlying_close: opts.spot,
    moneyness_ratio: opts.strike / opts.spot,
    open_interest: opts.oi,
    volume: 0,
    implied_vol: opts.iv,
    delta: null,
    theta: null,
    gamma: null,
    vega: null,
    rho: null,
  };
}

/** One expiry group's chain: strikes ±25% around spot in 5% steps. */
function mkChain(opts: {
  date: string;
  expiryDate: string;
  spot: number;
  /** IV at m=0 (ATM), vol pts /100 as fraction. */
  atmIv: number;
  /** IV change per unit log-moneyness (fraction). */
  slope: number;
  oi: number;
}): OptionsRow[] {
  const rows: OptionsRow[] = [];
  for (let pct = -25; pct <= 25; pct += 5) {
    const m = Math.log(1 + pct / 100);
    const iv = opts.atmIv + opts.slope * m;
    rows.push(
      mkRow({
        date: opts.date,
        expiryDate: opts.expiryDate,
        strike: Math.round(opts.spot * (1 + pct / 100)),
        type: pct < 0 ? "PUT" : pct > 0 ? "CALL" : "CALL",
        iv,
        oi: opts.oi,
        spot: opts.spot,
      }),
    );
  }
  return rows;
}

const SPOT = 4.5 * 1000; // 4.5 yuan in 厘

// --- Test 1: sign + magnitude on a single day -------------------------
// slope = −0.25 (IV 25 vol pts higher at m=−1... per unit ln-m): typical
// equity put skew. Expected tilt = slope × (ln1.1 − ln0.9) × 100 vol pts
// = −0.25 × 0.19062 × 100 ≈ −4.77 vol pts.
const putSkew = mkChain({
  date: "2026-09-01",
  expiryDate: "2026-09-23",
  spot: SPOT,
  atmIv: 0.25,
  slope: -0.25,
  oi: 500,
});
const flat = mkChain({
  date: "2026-09-01",
  expiryDate: "2026-10-28",
  spot: SPOT,
  atmIv: 0.25,
  slope: 0,
  oi: 300,
});
const callSkew = mkChain({
  date: "2026-09-01",
  expiryDate: "2026-12-23",
  spot: SPOT,
  atmIv: 0.25,
  slope: 0.10,
  oi: 200,
});

const res = computeSmileSlope([...putSkew, ...flat, ...callSkew]);
const day1 = res.spec.points[0];
console.log("== single day, 3 expiry groups ==");
console.log("mean tilt (rawSkew):", day1.rawSkew?.toFixed(3), "vol pts");
console.log("expected mean tilt:", ((-0.25 + 0 + 0.10) * (Math.log(1.1) - Math.log(0.9)) * 100).toFixed(3));
console.log("skewPct (gap vs spot %):", day1.skewPct?.toFixed(3));
console.log("skewPrice vs spot:", day1.skewPrice?.toFixed(2), "vs", day1.spot.toFixed(2));
for (const pe of day1.perExpiry) {
  console.log(`  ${pe.expiry}: rawSkew=${pe.rawSkew?.toFixed(3)} skewPct=${pe.skewPct?.toFixed(3)}`);
}

// --- Test 2: 6-day series, tilt tracking a rising spot ----------------
const rows2: OptionsRow[] = [];
for (let d = 0; d < 6; d++) {
  const date = `2026-09-0${d + 1}`;
  const spot = SPOT * (1 + d * 0.005); // spot drifts up
  // Put skew eases (slope toward 0) as spot rises: corr(tilt, spot) > 0.
  const slope = -0.30 + d * 0.02;
  rows2.push(
    ...mkChain({ date, expiryDate: "2026-09-23", spot, atmIv: 0.25, slope, oi: 400 }),
    ...mkChain({ date, expiryDate: "2026-10-28", spot, atmIv: 0.25, slope: slope + 0.05, oi: 400 }),
  );
}
const res2 = computeSmileSlope(rows2);
console.log("\n== 6-day series ==");
for (const p of res2.spec.points) {
  console.log(`${p.date} tilt=${p.rawSkew?.toFixed(3)} spot=${p.spot.toFixed(2)}`);
}
console.log("corr rows:", res2.corrRows.length, "(expect 12 = 6 days × 2 groups)");
const sep = res2.corrRows.filter((r) => r.expiry_month === "2026-09");
for (const r of sep) {
  console.log(
    `  2026-09 ${r.date}: corr5=${r.corr_skewness_ma5_vs_spot_ma5?.toFixed(3) ?? "—"} ` +
      `corr20=${r.corr_skewness_ma20_vs_spot_ma20 ?? "—"} corr60=${r.corr_skewness_ma60_vs_spot_ma60 ?? "—"}`,
  );
}

// expected tilt day-by-day for expiry 1: slope −0.30..−0.20 →
// tilt = slope × 0.19062 × 100
console.log("\nexpected tilts exp1:", [-0.3, -0.28, -0.26, -0.24, -0.22, -0.2]
  .map((s) => (s * (Math.log(1.1) - Math.log(0.9)) * 100).toFixed(3))
  .join(", "));

import { computeSmileSlope } from "../src/dataviz/features/szse-options/skew-shared/smileSlopeCompute";
