/**
 * Annual Sentiment panel — 3 vertically-stacked charts.
 *
 *   Panel 1: Put/Call OI Ratio over time + MA5 / MA20 lines + reference 1.0 line
 *   Panel 2: Total OI trend (Call vs Put, in 万张)
 *   Panel 3: ETF OHLC + volume bars (twin axis)
 *
 * Mirrors plot_annual_sentiment() in plot_szse_options.py.
 */
import { useEffect, useMemo, useState } from "react";
import { Alert, Stack } from "@mui/material";
import ChartCard from "@/components/ChartCard";
import DateRangeSlider from "@/components/DateRangeSlider";
import EChart from "@/components/EChart";
import OhlcModeToggle from "@/components/OhlcModeToggle";
import { useStore } from "@/store/filters";
import type {
  EtfOhlcvResponse,
  OptionsRow,
} from "../../../../shared/types";
import {
  ATM_GRAY,
  DOWN_COLOR,
  IV_BLUE,
  MA20_COLOR,
  UP_COLOR,
  axisColors,
  commonLegend,
  commonGrid,
} from "@/theme/chart-palette";
import { breakArraysAtGaps, fmtNum, safeMa } from "@/lib/series";
import {
  ohlcSeries,
  rebasePriceArrays,
  formatPriceValue,
  type OhlcMode,
} from "@/lib/ohlc";
import type { EChartsOption } from "echarts";

/**
 * Shared group name — wiring this on every EChart + calling
 * `connectChartsByGroup()` in main.tsx makes the axis-pointer / tooltip
 * sync across all three panels so hovering one shows tooltips on all.
 */
const CHART_GROUP = "annual-sentiment";

interface Props {
  rows: OptionsRow[];
  ohlcv: EtfOhlcvResponse | null;
}

function buildDailyOi(rows: OptionsRow[]): {
  date: string;
  callOi: number;
  putOi: number;
  pcRatio: number;
}[] {
  const byDate = new Map<string, { callOi: number; putOi: number }>();
  for (const r of rows) {
    if (!byDate.has(r.date)) byDate.set(r.date, { callOi: 0, putOi: 0 });
    const d = byDate.get(r.date)!;
    if (r.option_type === "CALL") d.callOi += r.open_interest;
    else d.putOi += r.open_interest;
  }
  const out = Array.from(byDate.entries())
    .map(([date, v]) => ({
      date,
      callOi: v.callOi,
      putOi: v.putOi,
      pcRatio: v.callOi > 0 ? v.putOi / v.callOi : NaN,
    }))
    .sort((a, b) => a.date.localeCompare(b.date));
  return out;
}

function buildPcRatioOption(dates: string[], pcRatio: number[], themeMode: "light" | "dark"): EChartsOption {
  const c = axisColors(themeMode);
  const ma5 = safeMa(pcRatio, 5);
  const ma20 = safeMa(pcRatio, 20);
  const broken = breakArraysAtGaps(dates, [pcRatio, ma5, ma20]);
  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 50, right: 20, bottom: 40 }),
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross", snap: true },
      backgroundColor: c.tooltipBg,
      borderColor: c.splitLineColor,
      textStyle: { color: c.textColor, fontSize: 11 },
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          axisValue?: string;
          marker?: string;
          seriesName?: string;
          value?: number | Array<number | null>;
        }>;
        if (arr.length === 0) return "";
        const dateStr = (arr[0].axisValue as string) || "";
        let html = `<div style="font-weight:600;margin-bottom:4px">${dateStr}</div>`;
        for (const p of arr) {
          if (p.value == null) continue;
          const v = Array.isArray(p.value) ? p.value[p.value.length - 1] : p.value;
          if (v == null || (typeof v === "number" && !Number.isFinite(v))) continue;
          const vstr = typeof v === "number" ? fmtNum(v) : String(v);
          html += `<div>${p.marker ?? ""} ${p.seriesName ?? ""}: <b>${vstr}</b></div>`;
        }
        return html;
      },
    },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    legend: commonLegend(themeMode),
    xAxis: {
      type: "category",
      data: broken.dates,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 9, rotate: 30 },
    },
    yAxis: {
      type: "value",
      scale: true,
      name: "P/C Ratio",
      nameTextStyle: { color: c.textColor, fontSize: 10 },
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 10, formatter: (v: number) => fmtNum(v) },
      splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
    },
    series: [
      {
        type: "line",
        name: "P/C OI Ratio",
        data: broken.arrays[0],
        smooth: false,
        symbol: "none",
        lineStyle: { color: IV_BLUE, width: 1.2 },
        markLine: {
          symbol: "none",
          silent: true,
          data: [{ yAxis: 1.0 }],
          lineStyle: { color: ATM_GRAY, type: "dotted", width: 0.8, opacity: 0.6 },
        },
      },
      {
        type: "line",
        name: "MA5",
        data: broken.arrays[1],
        smooth: false,
        symbol: "none",
        lineStyle: { color: DOWN_COLOR, width: 1, opacity: 0.7 },
      },
      {
        type: "line",
        name: "MA20",
        data: broken.arrays[2],
        smooth: false,
        symbol: "none",
        lineStyle: { color: MA20_COLOR, width: 1, opacity: 0.7 },
      },
    ],
  };
}

function buildOiTrendOption(dates: string[], callOi: number[], putOi: number[], themeMode: "light" | "dark"): EChartsOption {
  const c = axisColors(themeMode);
  // OI is in raw contracts (张) — convert to mil (1 mil = 1,000,000 contracts)
  const callMil = callOi.map((v) => v / 1e6);
  const putMil = putOi.map((v) => v / 1e6);
  const broken = breakArraysAtGaps(dates, [callMil, putMil]);
  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 56, right: 20, bottom: 40 }),
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross", snap: true },
      backgroundColor: c.tooltipBg,
      borderColor: c.splitLineColor,
      textStyle: { color: c.textColor, fontSize: 11 },
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          axisValue?: string;
          marker?: string;
          seriesName?: string;
          value?: number | Array<number | null>;
        }>;
        if (arr.length === 0) return "";
        const dateStr = (arr[0].axisValue as string) || "";
        let html = `<div style="font-weight:600;margin-bottom:4px">${dateStr}</div>`;
        for (const p of arr) {
          if (p.value == null) continue;
          const v = Array.isArray(p.value) ? p.value[p.value.length - 1] : p.value;
          if (v == null || (typeof v === "number" && !Number.isFinite(v))) continue;
          const vstr = typeof v === "number" ? fmtNum(v) : String(v);
          html += `<div>${p.marker ?? ""} ${p.seriesName ?? ""}: <b>${vstr}</b></div>`;
        }
        return html;
      },
    },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    legend: commonLegend(themeMode),
    xAxis: {
      type: "category",
      data: broken.dates,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 9, rotate: 30 },
    },
    yAxis: {
      type: "value",
      scale: true,
      name: "OI (mil contracts)",
      nameTextStyle: { color: c.textColor, fontSize: 10 },
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 10, formatter: (v: number) => fmtNum(v) + " mil" },
      splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
    },
    series: [
      {
        type: "line",
        name: "Call OI",
        data: broken.arrays[0],
        smooth: false,
        symbol: "none",
        areaStyle: { opacity: 0.12 },
        lineStyle: { color: UP_COLOR, width: 1.2 },
      },
      {
        type: "line",
        name: "Put OI",
        data: broken.arrays[1],
        smooth: false,
        symbol: "none",
        areaStyle: { opacity: 0.12 },
        lineStyle: { color: DOWN_COLOR, width: 1.2 },
      },
    ],
  };
}

function buildOhlcOption(
  ohlcv: EtfOhlcvResponse,
  themeMode: "light" | "dark",
  ohlcMode: OhlcMode,
): EChartsOption {
  const c = axisColors(themeMode);
  const rows = ohlcv.rows;
  const dates = rows.map((r) => r.date);
  const open = rows.map((r) => r.open);
  const close = rows.map((r) => r.close);
  const low = rows.map((r) => r.low);
  const high = rows.map((r) => r.high);

  // Rebase OHLC to % change from first close in percentage mode.
  const { rebased } = rebasePriceArrays(
    { open, close, low, high },
    ohlcMode,
  );
  const candleData = rows.map((_, i) => [
    rebased.open[i],
    rebased.close[i],
    rebased.low[i],
    rebased.high[i],
  ]);
  // ohlcv.volume is in 万 (10k) shares — convert to mil (1 mil = 100 万).
  // Volume bar color uses RAW close vs open (invariant under rebase).
  const volumes = rows.map((r, i) => ({
    value: r.volume / 100,
    itemStyle: {
      color: r.close >= r.open ? UP_COLOR : DOWN_COLOR,
      opacity: 0.4,
    },
    _idx: i,
  }));
  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 56, right: 56, bottom: 40 }),
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross", snap: true },
      backgroundColor: c.tooltipBg,
      borderColor: c.splitLineColor,
      textStyle: { color: c.textColor, fontSize: 11 },
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          axisValue?: string;
          marker?: string;
          seriesName?: string;
          value?: number | Array<number | null>;
        }>;
        if (arr.length === 0) return "";
        const dateStr = (arr[0].axisValue as string) || "";
        let html = `<div style="font-weight:600;margin-bottom:4px">${dateStr}</div>`;
        for (const p of arr) {
          if (p.value == null) continue;
          const name = p.seriesName ?? "";
          if (Array.isArray(p.value)) {
            const [o, cl, l, h] = p.value;
            if (o == null && cl == null && l == null && h == null) continue;
            html += `<div>${p.marker ?? ""} ${name}: O=${formatPriceValue(o, ohlcMode)} H=${formatPriceValue(h, ohlcMode)} L=${formatPriceValue(l, ohlcMode)} C=${formatPriceValue(cl, ohlcMode)}</div>`;
          } else {
            const v = p.value as number;
            if (!Number.isFinite(v)) continue;
            const vstr = name === "Volume" ? fmtNum(v) + " mil" : fmtNum(v);
            html += `<div>${p.marker ?? ""} ${name}: <b>${vstr}</b></div>`;
          }
        }
        return html;
      },
    },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    legend: commonLegend(themeMode),
    xAxis: {
      type: "category",
      data: dates,
      boundaryGap: true,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 9, rotate: 30 },
      splitLine: { show: false },
    },
    yAxis: [
      {
        type: "value",
        scale: true,
        name: ohlcMode === "percentage" ? "%" : "Price (元)",
        nameTextStyle: { color: c.textColor, fontSize: 10 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 10,
          formatter: (v: number) => formatPriceValue(v, ohlcMode),
        },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
      {
        type: "value",
        scale: true,
        name: "Volume (mil)",
        nameTextStyle: { color: c.textColor, fontSize: 10 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: { color: c.textColor, fontSize: 10, formatter: (v: number) => fmtNum(v) + " mil" },
        splitLine: { show: false },
      },
    ],
    series: [
      ohlcSeries(candleData, { name: "OHLC" }),
      {
        type: "bar",
        name: "Volume",
        yAxisIndex: 1,
        data: volumes,
        barWidth: "70%",
        z: 1,
      },
    ],
  };
}

export default function AnnualSentimentPanel({ rows, ohlcv }: Props) {
  const themeMode = useStore((s) => s.themeMode);
  const daily = useMemo(() => buildDailyOi(rows), [rows]);
  const maxIdx = daily.length - 1;
  const [range, setRange] = useState<[number, number]>([0, maxIdx]);
  // OHLC display mode — "percentage" (default) rebases ETF OHLC to % change
  // from the first close; "absolute" shows raw prices.
  const [ohlcMode, setOhlcMode] = useState<OhlcMode>("percentage");

  // Reset slider when underlying changes (new daily data arrives)
  useEffect(() => {
    setRange([0, daily.length - 1]);
  }, [daily]);

  // Slice daily data to the selected slider range
  const filteredDaily = useMemo(
    () => daily.slice(range[0], range[1] + 1),
    [daily, range],
  );

  // Filter ohlcv rows to the selected date window (dates from the daily array)
  const filteredOhlcv = useMemo(() => {
    if (!ohlcv || ohlcv.rows.length === 0) return null;
    const startDate = daily[range[0]]?.date ?? "";
    const endDate = daily[range[1]]?.date ?? "";
    const filteredRows = ohlcv.rows.filter(
      (r) => r.date >= startDate && r.date <= endDate,
    );
    return { ...ohlcv, rows: filteredRows };
  }, [ohlcv, daily, range]);

  if (daily.length === 0) {
    return (
      <Alert severity="warning">
        No options data available for the selected underlying + date range.
      </Alert>
    );
  }

  const dates = filteredDaily.map((d) => d.date);
  const pcRatio = filteredDaily.map((d) => d.pcRatio);
  const callOi = filteredDaily.map((d) => d.callOi);
  const putOi = filteredDaily.map((d) => d.putOi);

  return (
    <Stack spacing={2}>
      <ChartCard
        title="Put/Call OI Ratio (Sentiment)"
        subtitle="Daily P/C ratio + MA5 / MA20 · dotted line at 1.0"
        height={320}
      >
        <EChart option={buildPcRatioOption(dates, pcRatio, themeMode)} height={300} group={CHART_GROUP} />
      </ChartCard>

      <ChartCard
        title="Total Open Interest Trend"
        subtitle="Call OI vs Put OI (mil contracts)"
        height={320}
      >
        <EChart option={buildOiTrendOption(dates, callOi, putOi, themeMode)} height={300} group={CHART_GROUP} />
      </ChartCard>

      <ChartCard
        title="ETF Price & Volume"
        subtitle="OHLC + volume (price-up green / price-down red)"
        height={360}
        action={<OhlcModeToggle value={ohlcMode} onChange={setOhlcMode} />}
      >
        {filteredOhlcv && filteredOhlcv.rows.length > 0 ? (
          <EChart option={buildOhlcOption(filteredOhlcv, themeMode, ohlcMode)} height={340} group={CHART_GROUP} />
        ) : (
          <Alert severity="info">No ETF OHLCV data available for this underlying.</Alert>
        )}
      </ChartCard>
      <DateRangeSlider
        value={range}
        onChange={setRange}
        max={maxIdx}
        dates={daily.map((d) => d.date)}
      />
    </Stack>
  );
}
