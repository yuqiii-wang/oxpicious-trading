/**
 * Debug: render Market Movements top option headlessly (SSR SVG) with REAL
 * API data to check whether the per-industry shade areas actually render.
 * Run: cd data_viz && npx tsx debug-shade.mts
 */
import * as echarts from "echarts";
import fs from "node:fs";
import { buildMarketMovementsTopOption } from "./src/live/features/market-movements/marketMovementsOption";
import type { IntradayMovementsResponse } from "./shared/types";

async function main() {
  const resp: IntradayMovementsResponse = await fetch(
    "http://localhost:5173/api/live-data/intraday-movements?benchmark_code=000300",
  ).then((r) => r.json());

  console.log("date:", resp.date, "latest:", resp.latest_time);
  console.log(
    "bench ticks:", resp.benchmark_series.length,
    "| industry rows:", resp.industry_series.length,
    "| industries:", resp.industries.length,
  );

  const opt = buildMarketMovementsTopOption(resp, resp.latest_time, "light");
  const series = (opt.series ?? []) as Array<Record<string, unknown>>;
  console.log("OPTION series count:", series.length);
  for (const s of series.slice(0, 7)) {
    const data = s.data as Array<number | null>;
    const nonNull = data.filter((v) => v != null).length;
    console.log(
      ` - name=${s.name} type=${s.type} stack=${s.stack} strategy=${s.stackStrategy} z=${s.z} areaStyle=${s.areaStyle ? "yes" : "no"} nonNull=${nonNull}/${data.length} first6=${JSON.stringify(data.slice(0, 6))}`,
    );
  }

  const chart = echarts.init(null as unknown as HTMLElement, undefined, {
    renderer: "svg",
    ssr: true,
    width: 1200,
    height: 460,
  });
  chart.setOption(opt as never);
  const svg = chart.renderToSVGString();
  const shadePaths = (svg.match(/fill-opacity="0.15"/g) ?? []).length;
  console.log(
    "SVG len:", svg.length,
    "| total <path>:", (svg.match(/<path /g) ?? []).length,
    "| shade area paths (fill-opacity=0.15):", shadePaths,
  );
  fs.writeFileSync("debug-shade.svg", svg);
  console.log("wrote debug-shade.svg");
  chart.dispose();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
