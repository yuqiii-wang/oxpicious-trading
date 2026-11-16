/**
 * Build the ECharts option for the market crowdedness curve chart — the
 * security's daily stats close (left axis) + trading amount bars (right
 * axis; the benchmark-price chart's close + amt layout), with the
 * crowdedness_score curve AND its 5-day moving average
 * (crowdedness_score_ma5 — the ONLINE read; the raw score's day-over-day
 * movement is dominated by single-day pillar noise, so the raw line
 * draws thin/translucent and the ma5 line is the readable curve) on a
 * second, offset further right axis (ECharts does not auto-offset
 * same-side axes — the crowdedness axis carries an explicit `offset`).
 * The tooltip carries the score + ma5, the score's four RAW pillars
 * with their *_pct history percentiles (concentration / flow change /
 * co-movement / valuation — the score is the renormalized
 * 0.30/0.30/0.25/0.15 blend of the percentiles), the
 * turnover-concentration evidence (top 1/5/10% day-amount shares +
 * effective traded-name count) and the date's hot stock names
 * (top_stocks evidence; the hot set ranks by 60-day mean amount).
 *
 * RELOCATED from the MarketCrowdedness page dir (2026-11-16): the
 * crowdedness SIGNAL family's chart (Short-Term Tech's crowdedness
 * pick — the family's markers ride the crowdedness curves, not the
 * plain OHLC code trend) renders the same option plus the shared
 * signal-event scatter layer (signalMarker.ts — buy ▲ / sell ▼ / the
 * no-action diamond anchored on the UNDERLYING INDEX CLOSE curve, the
 * selected day emphasized) via the optional ``opts`` block.
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import type { MarketCrowdednessChartResponse } from "@shared/types";
import {
  PALETTE_HI,
  MUTED_PALETTE,
  NEUTRAL_FILL,
  axisColors,
  commonLegend,
  commonGrid,
  commonDataZoom,
} from "@/theme/chart-palette";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import {
  signalEventScatterSeries,
  type SignalEventPoint,
} from "@/shared/charts/signalMarker";
import { fmtNum } from "@/lib/series";

export interface CrowdednessCurveOpts {
  /** The signal-history events marked on the chart (the crowdedness
   *  family's population — SignalCrowdednessChart maps the
   *  trading-signal rows to points). Anchored per date on the
   *  underlying index's close value (a date without a close row
   *  skips). */
  signalEvents?: readonly SignalEventPoint[];
  /** The SELECTED signal date — its marker(s) draw enlarged with a
   *  glow (the expanded date's emphasis). Null = none. */
  selectedDate?: string | null;
}

export function buildCrowdednessCurveOption(
  data: MarketCrowdednessChartResponse,
  mode: ThemeMode,
  opts?: CrowdednessCurveOpts,
): EChartsOption {
  const c = axisColors(mode);
  const dates = data.rows.map((r) => r.date);
  const closes = data.rows.map((r) => r.close);
  const amts = data.rows.map((r) => r.trading_amount);
  const metricValues = data.rows.map((r) => r.crowdedness_score);
  const ma5Values = data.rows.map((r) => r.crowdedness_score_ma5);
  const concValues = data.rows.map((r) => r.concentration);
  const flowValues = data.rows.map((r) => r.flow_change);
  const cmvValues = data.rows.map((r) => r.comovement);
  const valValues = data.rows.map((r) => r.valuation);
  const concPctValues = data.rows.map((r) => r.concentration_pct);
  const flowPctValues = data.rows.map((r) => r.flow_pct);
  const cmvPctValues = data.rows.map((r) => r.comovement_pct);
  const valPctValues = data.rows.map((r) => r.valuation_pct);
  const top1Values = data.rows.map((r) => r.top1_share);
  const top5Values = data.rows.map((r) => r.top5_share);
  const top10Values = data.rows.map((r) => r.top10_share);
  const effNValues = data.rows.map((r) => r.effective_n);
  const hotNames = data.rows.map((r) => r.top_stocks);
  // Out-of-scope codes / pre-2020 history have no metric rows — omit the
  // metric series/axis until a non-null value shows up.
  const hasMetric = metricValues.some((v) => v != null && Number.isFinite(v));

  const series: EChartsOption["series"] = [];

  // 1. Trading amount bars (background anchor, right axis — the
  //    benchmark-price chart's layout; lines draw on top).
  series.push({
    name: "Trading Amt",
    type: "bar",
    yAxisIndex: 1,
    data: amts,
    barCategoryGap: "20%",
    itemStyle: { color: NEUTRAL_FILL, opacity: 0.18 },
    z: 1,
    tooltip: { show: false },
  });

  // 2. Close line (left axis).
  series.push({
    name: "Close",
    type: "line",
    data: closes,
    showSymbol: false,
    symbol: "circle",
    symbolSize: 5,
    lineStyle: { color: PALETTE_HI, width: 1.5 },
    itemStyle: { color: PALETTE_HI },
    z: 10,
  });

  // 3. Crowdedness lines (second right axis, offset further right —
  //    only when data exists): the raw score thin/translucent
  //    (evidence — its day-over-day movement is pillar noise) and the
  //    ma5 as the readable ONLINE curve on top.
  if (hasMetric) {
    series.push({
      name: "Crowdedness",
      type: "line",
      yAxisIndex: 2,
      data: metricValues,
      showSymbol: false,
      symbol: "circle",
      symbolSize: 5,
      lineStyle: { color: MUTED_PALETTE[0], width: 1.2, opacity: 0.55 },
      itemStyle: { color: MUTED_PALETTE[0] },
      z: 10,
    });
    series.push({
      name: "Crowdedness MA5",
      type: "line",
      yAxisIndex: 2,
      data: ma5Values,
      showSymbol: false,
      symbol: "circle",
      symbolSize: 5,
      lineStyle: { color: MUTED_PALETTE[1], width: 1.4, opacity: 0.75 },
      itemStyle: { color: MUTED_PALETTE[1] },
      z: 11,
    });
  }

  // 4. The crowdedness FAMILY's signal-event markers (the shared
  //    action-marker layer — signalMarker.ts): buy ▲ / sell ▼ anchored
  //    on the UNDERLYING INDEX CLOSE curve that day (user-directed —
  //    the action reads on the traded curve, the crowdedness curves
  //    stay the trigger evidence), the gate-declined records the grey
  //    diamond, the selected day's markers grown + glowing.
  let markerNames: string[] = [];
  const events = opts?.signalEvents ?? [];
  if (events.length > 0) {
    const yByDate = new Map<string, number | null | undefined>();
    data.rows.forEach((r, i) => {
      yByDate.set(r.date, closes[i]);
    });
    const markerSeries = signalEventScatterSeries(events, yByDate, {
      selectedDate: opts?.selectedDate ?? null,
      axisIndices: { xAxisIndex: 0, yAxisIndex: 0 },
      z: 12,
    });
    series.push(...markerSeries);
    markerNames = markerSeries.map((s) => String(s.name));
  }

  return baseChartOption(mode, {
    // top: 44 (vs the old 32) separates the legend row from the two
    // right-axis names — with two right axes ("Amt (亿)" + "Crowdedness")
    // the 32px band draws the legend and both names on top of each other
    // (the hot-tech card's setup).
    grid: commonGrid({ bottom: 50, top: 44, right: hasMetric ? 96 : 56 }),
    dataZoom: commonDataZoom(),
    tooltip: commonTooltip(mode, {
      axisPointer: { type: "cross" },
      // The hot-name line (even capped at 20) is wider than the chart —
      // keep the box inside the container and wrap it instead.
      confine: true,
      extraCssText: "max-width: 560px; white-space: normal;",
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          dataIndex?: number;
        }>;
        const idx = arr[0]?.dataIndex ?? 0;
        const close = closes[idx];
        const amt = amts[idx];
        const children = [
          `${data.name || data.code} (${data.code})`,
          dates[idx] ?? "—",
          `Close: ${close == null || !Number.isFinite(close) ? "—" : fmtNum(close, 2)}`,
          `Trading Amt: ${amt == null || !Number.isFinite(amt) ? "—" : fmtNum(amt / 1e8, 2) + "亿"}`,
        ];
        if (hasMetric) {
          const v = metricValues[idx];
          const m5 = ma5Values[idx];
          if (v == null || !Number.isFinite(v)) {
            children.push("Crowdedness: —");
          } else {
            children.push(
              `Crowdedness: ${fmtNum(v, 4)} (vs pool 1y hist)`,
              ...(m5 != null && Number.isFinite(m5)
                ? [`Crowdedness MA5: ${fmtNum(m5, 4)}`] : []),
            );
            // raw pillar value + its percentile vs the pool's own
            // trailing-year history (the score's actual inputs)
            const pillar = (
              label: string,
              raw: number | null,
              pct: number | null,
            ): string | null => {
              if (raw == null || !Number.isFinite(raw)) return null;
              const pctTxt =
                pct != null && Number.isFinite(pct)
                  ? ` · ${fmtNum(pct * 100, 0)}%ile`
                  : "";
              return `  · ${label}: ${fmtNum(raw, 4)}${pctTxt}`;
            };
            const lines = [
              pillar("Conc", concValues[idx], concPctValues[idx]),
              pillar("Flow", flowValues[idx], flowPctValues[idx]),
              pillar("Co-move", cmvValues[idx], cmvPctValues[idx]),
              pillar("Val", valValues[idx], valPctValues[idx]),
            ].filter((x): x is string => x != null);
            // turnover-concentration evidence: day-amount top cuts +
            // effective traded-name count (not score inputs)
            const t1 = top1Values[idx];
            const t5 = top5Values[idx];
            const t10 = top10Values[idx];
            const effN = effNValues[idx];
            const fmtShare = (v: number | null) =>
              v == null || !Number.isFinite(v)
                ? "—" : fmtNum(v, 3);
            const turnLine = (t1 != null || t10 != null)
              ? `  · Top1/5/10% day-amt: ${fmtShare(t1 ?? null)} / ` +
                `${fmtShare(t5 ?? null)} / ${fmtShare(t10 ?? null)}` +
                (effN != null && Number.isFinite(effN)
                  ? ` · Eff-N ${fmtNum(effN, 1)}` : "")
              : null;
            children.push(
              ...(lines.length ? [lines.join("")] : []),
              ...(turnLine ? [turnLine] : []),
            );
          }
          const hot = hotNames[idx];
          if (hot) {
            // Large pools carry 10% of hundreds of names — cap the
            // tooltip list at the first 20 entries, ellipsized (the
            // API/DB row keeps the full evidence list).
            const parts = hot.split(", ");
            const shown = parts.length > 20
              ? parts.slice(0, 20).join(", ") + ", ..."
              : hot;
            children.push(`Hot: ${shown}`);
          }
        }
        return children.join("<br/>");
      },
    }),
    legend: commonLegend(mode, {
      data: hasMetric
        ? ["Close", "Trading Amt", "Crowdedness", "Crowdedness MA5",
           ...markerNames]
        : ["Close", "Trading Amt"],
    }),
    xAxis: {
      type: "category",
      data: dates,
      boundaryGap: true, // bars need a gap so they center on ticks
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 9 },
      splitLine: { show: false },
    },
    yAxis: [
      {
        type: "value",
        scale: true,
        name: "Close",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: number) => Math.round(v).toLocaleString("en-US") },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
      {
        type: "value",
        scale: true,
        name: "Amt (亿)",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: number) => Math.round(v / 1e8).toLocaleString("en-US") },
        splitLine: { show: false },
      },
      // Crowdedness axis (offset further right — ECharts does not
      // auto-offset same-side axes) — present only with data.
      ...(hasMetric
        ? [{
            type: "value" as const,
            scale: true,
            name: "Crowdedness",
            nameTextStyle: { color: c.textColor, fontSize: 9 },
            axisLine: { lineStyle: { color: c.axisLineColor } },
            axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: number) => fmtNum(v, 2) },
            splitLine: { show: false },
            // Cleared of the amt axis name ("Amt (亿)" spans ~±22px from
            // the grid edge) and of the amt labels.
            offset: 56,
          }]
        : []),
    ],
    series,
  });
}
