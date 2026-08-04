/**
 * Single-ETF panel — rebased close % + MA20/MA60/MA120 + RZ/RQ margin fills +
 * volume bars. Mirrors draw_etf_panel() in plot_szse_sse_etf_and_margin.py.
 *
 * Layout:
 *   • Primary axis (left): rebased close % (start = 0%) + MA20/MA60/MA120
 *     For bond ETFs: line + neutral-gray fill to 0%.
 *     For equity ETFs: OHLC bars of rebased OHLC %.
 *   • Twin axis (hidden): RZ green fill UP from middle (always ≥0) +
 *     RQ red fill DOWN from middle (always ≤0).
 *   • Twin axis (right, visible): trading-turnover bars colored by price-up/down
 *     (source: stats.etf_liquidity_margin.trading_amount = 成交金额(yuan) from
 *      build_szse_sse_etf_and_margin.py; displayed in 亿元, /1e8).
 *
 * Return badges shown in the panel header.
 */
import { useEffect, useMemo, useState } from "react";
import { Alert, Box, Chip, Slider, Stack, Typography } from "@mui/material";
import ChartCard from "@/components/ChartCard";
import CompositionPieChart from "@/components/CompositionPieChart";
import EChart from "@/components/EChart";
import OhlcModeToggle from "@/components/OhlcModeToggle";
import { useStore } from "@/store/filters";
import type { EtfBundle } from "../../../../shared/types";
import {
  DOWN_COLOR,
  DIVIDEND_COLOR,
  MA120_COLOR,
  MA20_COLOR,
  MA60_COLOR,
  NEUTRAL_FILL,
  PALETTE_HI,
  SPLIT_COLOR,
  UP_COLOR,
  axisColors,
  commonLegend,
  commonGrid,
} from "@/theme/chart-palette";
import { breakArraysAtGaps, fmtNum, fmtPct, fmtMil, safeMa } from "@/lib/series";
import {
  ohlcSeries,
  rebasePriceArrays,
  formatPriceValue,
  type OhlcMode,
} from "@/lib/ohlc";
import { computeMarginScores } from "@/lib/margin-score";
import type { EChartsOption } from "echarts";

interface Props {
  etf: EtfBundle;
  /** Optional default slider window (inclusive date strings). When provided
   *  the slider initializes to the indices covering [defaultStartDate,
   *  defaultEndDate] inside this ETF's rows — used to align multiple panels
   *  to the shortest common time range. */
  defaultStartDate?: string;
  defaultEndDate?: string;
}

function retBadge(values: number[], idxFromEnd: number): number | null {
  if (values.length <= idxFromEnd) return null;
  const vnow = values[values.length - 1];
  const vthen = values[values.length - 1 - idxFromEnd];
  if (!Number.isFinite(vnow) || !Number.isFinite(vthen) || Math.abs(vthen) < 1e-9) return null;
  return (vnow / vthen - 1) * 100;
}

function buildOption(
  etf: EtfBundle,
  themeMode: "light" | "dark",
  ohlcMode: OhlcMode,
): EChartsOption {
  const c = axisColors(themeMode);
  const rows = etf.rows;
  const dates = rows.map((r) => r.date);
  // Use ADJUSTED OHLC (adj_*) so the curve stays continuous across dividends
  // and splits — raw OHLC shows a fake gap on corp-action days (e.g. a 3:1
  // split looks like a -67% crash). Fall back to raw when adj_* is missing.
  const close = rows.map((r) => r.adj_close ?? r.close);
  // Trading turnover (成交金额) in yuan — sourced from stats.etf_liquidity_margin
  // .trading_amount (loaded by build_szse_sse_etf_and_margin.py from the CSV's
  // 成交金额 column, stored in yuan). Displayed in 亿元 (1 亿 = 1e8 yuan).
  const tradingAmount = rows.map((r) => r.trading_amount);
  const isBond = etf.is_bond;

  // Rebase close to % change in percentage mode; raw in absolute mode.
  const { rebased: rebasedClose } = rebasePriceArrays({ close }, ohlcMode);
  const closePct = rebasedClose.close;
  const ma20 = safeMa(closePct, 20);
  const ma60 = safeMa(closePct, 60);
  const ma120 = safeMa(closePct, 120);

  // SZSE ETF trend CSVs report prices in 0.001元 (milliyuan); divide stored
  // price-derived amounts by 1000 to obtain yuan.
  const PRICE_SCALE = 1000;

  // Corporate-action event markers (dividends / splits) — markers only, no
  // text labels (full detail is shown in the axis tooltip). Gold diamond =
  // dividend, teal diamond = split/conversion. coord uses the date category.
  const markPointData: Array<{
    name: string;
    coord: [string, number];
    itemStyle: { color: string };
    symbol: string;
    symbolSize: number;
  }> = [];
  // date → human-readable corp-action detail for the axis tooltip.
  const corpActionByDate = new Map<string, { type: string; text: string }>();
  let prevCumFactor: number | null = null;
  for (let i = 0; i < rows.length; i++) {
    const r = rows[i];
    const at = (r.action_type ?? "").trim();
    const curCum = r.cum_split_factor ?? 1;
    if (at) {
      const y = closePct[i];
      if (y != null && Number.isFinite(y)) {
        if (at === "dividend") {
          const divStored = Math.abs(r.implied_dividend_per_share ?? 0);
          const perShare = divStored / PRICE_SCALE;
          markPointData.push({
            name: "Dividend",
            coord: [r.date, y],
            itemStyle: { color: DIVIDEND_COLOR },
            symbol: "diamond",
            symbolSize: 12,
          });
          corpActionByDate.set(r.date, {
            type: at,
            text: `Dividend · ${fmtNum(perShare, 3)} yuan per share`,
          });
        } else {
          // split ratio = cum_factor[t] / cum_factor[t-1]
          const ratio = prevCumFactor && prevCumFactor > 0 ? curCum / prevCumFactor : null;
          markPointData.push({
            name: "Split",
            coord: [r.date, y],
            itemStyle: { color: SPLIT_COLOR },
            symbol: "diamond",
            symbolSize: 12,
          });
          corpActionByDate.set(r.date, {
            type: at,
            text: ratio != null && Number.isFinite(ratio) && ratio > 0
              ? `Split · 1 share split to ${fmtNum(ratio, 2)} shares`
              : "Split/Conversion",
          });
        }
      }
    }
    prevCumFactor = curCum;
  }
  const corpMarkPoint = markPointData.length
    ? { data: markPointData, label: { show: false } }
    : undefined;

  // Margin scores
  const marginRows = rows.map((r) => ({
    date: r.date,
    rz_balance: r.rz_balance,
    rq_balance_amt: r.rq_balance_amt,
  }));
  const marginScores = computeMarginScores(marginRows);
  const rzScore = marginScores.map((m) => m.rz_score);
  const rqScore = marginScores.map((m) => m.rq_score);

  // Date → raw balance (yuan) lookups for tooltip display. The plotted series
  // values are shifted/clipped scores (not raw yuan), so the tooltip must
  // re-resolve the actual balance by date to show a meaningful "mil" figure.
  const rzByDate = new Map<string, number | null>(
    rows.map((r) => [r.date, r.rz_balance]),
  );
  const rqByDate = new Map<string, number | null>(
    rows.map((r) => [r.date, r.rq_balance_amt]),
  );

  // Dynamic axis limits for hidden margin axis — matches Python's
  // ax_rzrq.set_ylim(-max_of * 1.15, max_of * 1.15)
  const marginVals = [...rzScore, ...rqScore].filter(
    (v): v is number => v != null && Number.isFinite(v),
  );
  const maxAbs = marginVals.length > 0 ? Math.max(...marginVals.map(Math.abs)) : 0;
  const marginAxisRange = Math.max(1e-6, maxAbs) * 1.15;

  // Trading-turnover bar colors (price-up green / price-down red)
  // Compare intraday close vs open — not close vs previous day's close.
  // trading_amount is stored in yuan — convert to 亿元 (/1e8) for display.
  const open = rows.map((r) => r.adj_open ?? r.open);
  const amtData = tradingAmount.map((v, i) => {
    const up = close[i] >= open[i];
    return {
      value: v / 1e8,
      itemStyle: { color: up ? UP_COLOR : DOWN_COLOR, opacity: 0.4 },
    };
  });

  const series: EChartsOption["series"] = [];

  // Break arrays at gaps so lines don't interpolate across weekends/holidays
  if (isBond) {
    const broken = breakArraysAtGaps(dates, [closePct, ma20, ma60, ma120]);
    series.push({
      type: "line",
      name: ohlcMode === "percentage" ? "Rebased %" : "Close",
      yAxisIndex: 0,
      data: broken.arrays[0],
      smooth: false,
      symbol: "none",
      showSymbol: false,
      lineStyle: { color: PALETTE_HI, width: 1.35 },
      areaStyle: { color: NEUTRAL_FILL, opacity: 0.08 },
      z: 5,
      markPoint: corpMarkPoint,
    });
    series.push({
      type: "line",
      name: "MA20",
      yAxisIndex: 0,
      data: broken.arrays[1],
      smooth: false,
      symbol: "none",
      lineStyle: { color: MA20_COLOR, width: 0.9 },
      z: 4,
    });
    series.push({
      type: "line",
      name: "MA60",
      yAxisIndex: 0,
      data: broken.arrays[2],
      smooth: false,
      symbol: "none",
      lineStyle: { color: MA60_COLOR, width: 0.8, type: "dashed" },
      z: 4,
    });
    series.push({
      type: "line",
      name: "MA120",
      yAxisIndex: 0,
      data: broken.arrays[3],
      smooth: false,
      symbol: "none",
      lineStyle: { color: MA120_COLOR, width: 0.7, type: "dotted" },
      z: 4,
    });
  } else {
    // OHLC bars — rebased to % in percentage mode, raw prices in absolute mode.
    // Uses ADJUSTED OHLC so dividends/splits don't create artificial gaps.
    // All four components are scaled by the same base (first valid close) so
    // the relative shape of each candle is preserved (close-vs-open coloring
    // is invariant under uniform scaling).
    const opens = rows.map((r) => r.adj_open ?? r.open);
    const highs = rows.map((r) => r.adj_high ?? r.high);
    const lows = rows.map((r) => r.adj_low ?? r.low);
    const { rebased: rebasedOhlc } = rebasePriceArrays(
      { open: opens, high: highs, low: lows, close },
      ohlcMode,
    );
    const broken = breakArraysAtGaps(dates, [
      rebasedOhlc.open, rebasedOhlc.high, rebasedOhlc.low, closePct,
      ma20, ma60, ma120,
    ]);
    const candleData = broken.dates.map((_, i) => [
      broken.arrays[0][i],
      broken.arrays[3][i],
      broken.arrays[2][i],
      broken.arrays[1][i],
    ]);
    series.push(
      ohlcSeries(candleData, {
        name: ohlcMode === "percentage" ? "OHLC %" : "OHLC",
        yAxisIndex: 0,
        z: 3,
      }),
    );
    // Corp-action markers (dividend/split) ride on the MA20 line — the custom
    // OHLC series cannot host markPoint, so a standard line series carries it.
    series.push({
      type: "line",
      name: "MA20",
      yAxisIndex: 0,
      data: broken.arrays[4],
      smooth: false,
      symbol: "none",
      lineStyle: { color: MA20_COLOR, width: 0.9 },
      z: 6,
      markPoint: corpMarkPoint,
    });
    series.push({
      type: "line",
      name: "MA60",
      yAxisIndex: 0,
      data: broken.arrays[5],
      smooth: false,
      symbol: "none",
      lineStyle: { color: MA60_COLOR, width: 0.8, type: "dashed" },
      z: 6,
    });
    series.push({
      type: "line",
      name: "MA120",
      yAxisIndex: 0,
      data: broken.arrays[6],
      smooth: false,
      symbol: "none",
      lineStyle: { color: MA120_COLOR, width: 0.7, type: "dotted" },
      z: 6,
    });
  }

  // RZ green fill UP from middle (always ≥0) on hidden twin axis
  const brokenM = breakArraysAtGaps(dates, [rzScore, rqScore]);
  series.push({
    type: "line",
    name: "cash borrow balance",
    yAxisIndex: 1,
    data: brokenM.arrays[0],
    smooth: false,
    symbol: "none",
    lineStyle: { color: UP_COLOR, width: 0.6, opacity: 0.7 },
    areaStyle: { color: UP_COLOR, opacity: 0.36 },
    z: 3,
  });
  series.push({
    type: "line",
    name: "sec borrow balance",
    yAxisIndex: 1,
    data: brokenM.arrays[1],
    smooth: false,
    symbol: "none",
    lineStyle: { color: DOWN_COLOR, width: 0.6, opacity: 0.7 },
    areaStyle: { color: DOWN_COLOR, opacity: 0.36 },
    z: 3,
  });

  // Trading-turnover bars (visible right axis)
  series.push({
    type: "bar",
    name: "Amount",
    yAxisIndex: 2,
    data: amtData,
    barWidth: "90%",
    z: 1,
  });

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 50, right: 50, bottom: 28 }),
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
        // Corporate-action event (dividend / split) on the hovered day
        const corp = corpActionByDate.get(dateStr);
        if (corp) {
          const color = corp.type === "dividend" ? DIVIDEND_COLOR : SPLIT_COLOR;
          html += `<div style="margin-bottom:4px"><span style="color:${color}">●</span> <b style="color:${color}">${corp.text}</b></div>`;
        }
        for (const p of arr) {
          if (p.value == null) continue;
          const name = p.seriesName ?? "";
          if (Array.isArray(p.value)) {
            // OHLC: [open, close, low, high]
            const [o, cl, l, h] = p.value;
            if (o == null && cl == null && l == null && h == null) continue;
            html += `<div>${p.marker ?? ""} ${name}: O=${formatPriceValue(o, ohlcMode)} H=${formatPriceValue(h, ohlcMode)} L=${formatPriceValue(l, ohlcMode)} C=${formatPriceValue(cl, ohlcMode)}</div>`;
          } else {
            const v = p.value as number;
            if (!Number.isFinite(v)) continue;
            let vstr: string;
            if (name === "Amount") {
              vstr = fmtNum(v) + " 亿";
            } else if (name === "cash borrow balance") {
              vstr = fmtMil(rzByDate.get(dateStr) ?? null);
            } else if (name === "sec borrow balance") {
              vstr = fmtMil(rqByDate.get(dateStr) ?? null);
            } else if (name.includes("remained")) {
              vstr = fmtMil(v);
            } else if (name.includes("RZ") || name.includes("RQ")) {
              vstr = fmtNum(v);
            } else {
              vstr = formatPriceValue(v, ohlcMode);
            }
            html += `<div>${p.marker ?? ""} ${name}: <b>${vstr}</b></div>`;
          }
        }
        return html;
      },
    },
    legend: commonLegend(themeMode, { type: "scroll" }),
    xAxis: {
      type: "category",
      data: dates,
      boundaryGap: true,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 8,
        rotate: 0,
        formatter: (v: string) => v.slice(0, 7), // YYYY-MM only
        interval: Math.max(1, Math.floor(dates.length / 6)),
      },
      splitLine: { show: false },
    },
    yAxis: [
      {
        type: "value",
        scale: true,
        name: ohlcMode === "percentage" ? "%" : "Price",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => formatPriceValue(v, ohlcMode),
        },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
      {
        type: "value",
        scale: true,
        show: false,
        min: -marginAxisRange,
        max: marginAxisRange,
      },
      {
        type: "value",
        scale: true,
        name: "Amt (亿)",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => fmtNum(v) + " 亿",
        },
        splitLine: { show: false },
      },
    ],
    series,
  };
}

function ReturnBadges({ etf }: { etf: EtfBundle }) {
  // Use adjusted close so return badges reflect true total return (dividends
  // and splits folded in) rather than the raw price gap on corp-action days.
  const close = etf.rows.map((r) => r.adj_close ?? r.close);
  const r1m = retBadge(close, Math.min(21, close.length - 1));
  const r3m = retBadge(close, Math.min(63, close.length - 1));
  const r6m = retBadge(close, Math.min(126, close.length - 1));
  const rtot = retBadge(close, close.length - 1);

  const fmt = (v: number | null, label: string) => {
    if (v == null) return null;
    const color = v >= 0 ? UP_COLOR : DOWN_COLOR;
    return (
      <Chip
        key={label}
        label={`${label} ${v >= 0 ? "+" : ""}${fmtPct(v)}`}
        size="small"
        variant="outlined"
        sx={{
          fontSize: "0.65rem",
          height: 18,
          borderColor: color,
          color,
          fontWeight: 600,
        }}
      />
    );
  };

  return (
    <Stack direction="row" spacing={0.5} alignItems="center" flexWrap="wrap" useFlexGap>
      {fmt(r1m, "1M")}
      {fmt(r3m, "3M")}
      {fmt(r6m, "6M")}
      {fmt(rtot, "Tot")}
    </Stack>
  );
}

export default function EtfMarginPanel({ etf, defaultStartDate, defaultEndDate }: Props) {
  const themeMode = useStore((s) => s.themeMode);
  const allRows = etf.rows;
  const maxIdx = allRows.length - 1;
  const [range, setRange] = useState<[number, number]>([0, maxIdx]);
  // OHLC display mode — "percentage" (default) rebases OHLC + MAs to % change
  // from the first valid close; "absolute" shows raw prices.
  const [ohlcMode, setOhlcMode] = useState<OhlcMode>("percentage");
  // Lifted composition panel open state — controls ChartCard height so the
  // pie chart stays inside the parent box when expanded.
  const [compositionOpen, setCompositionOpen] = useState(false);
  // Per-stock OHLC expansion open state — when true the card grows
  // further to fit the stock chart below the pie charts.
  const [stockOhlcOpen, setStockOhlcOpen] = useState(false);

  // Reset slider when data changes (e.g., theme switch or page change).
  // When defaultStartDate/defaultEndDate are provided (aligned to the
  // shortest common time range across sibling panels), the slider
  // initializes to the indices covering that window inside this ETF's rows.
  useEffect(() => {
    let startIdx = 0;
    let endIdx = allRows.length - 1;
    if (defaultStartDate) {
      const idx = allRows.findIndex((r) => r.date >= defaultStartDate);
      if (idx >= 0) startIdx = idx;
    }
    if (defaultEndDate) {
      for (let i = allRows.length - 1; i >= 0; i--) {
        if (allRows[i].date <= defaultEndDate) {
          endIdx = i;
          break;
        }
      }
    }
    if (startIdx > endIdx) {
      startIdx = 0;
      endIdx = allRows.length - 1;
    }
    setRange([startIdx, endIdx]);
    setCompositionOpen(false);
    setStockOhlcOpen(false);
  }, [etf.code, allRows.length, defaultStartDate, defaultEndDate]);

  // Filter rows to the selected date window
  const filteredRows = useMemo(
    () => allRows.slice(range[0], range[1] + 1),
    [allRows, range],
  );
  const filteredEtf: EtfBundle = useMemo(
    () => ({ ...etf, rows: filteredRows }),
    [etf, filteredRows],
  );
  const option = useMemo(
    () => buildOption(filteredEtf, themeMode, ohlcMode),
    [filteredEtf, themeMode, ohlcMode],
  );

  // Dynamic card height: expand when composition panel is open so the pie
  // chart fits inside the parent box; expand further when the per-stock
  // OHLC expansion is open.
  const cardHeight = compositionOpen
    ? (stockOhlcOpen ? 1060 : 720)
    : 360;

  return (
    <ChartCard
      title={`${etf.code} · ${etf.name}`}
      subtitle={`${etf.sector_label} / ${etf.industry_label}${etf.is_bond ? " · Bond ETF" : " · Equity ETF"}${etf.index_code ? ` · → ${etf.index_code} ${etf.index_name}` : ""}`}
      action={
        <Stack direction="row" spacing={0.5} alignItems="center" flexWrap="wrap" useFlexGap>
          <OhlcModeToggle value={ohlcMode} onChange={setOhlcMode} />
          <ReturnBadges etf={filteredEtf} />
        </Stack>
      }
      height={cardHeight}
    >
      <Box sx={{ width: "100%" }}>
        <EChart option={option} height={250} />
        {maxIdx > 0 && (
          <Box sx={{ px: 1, mt: 0.25 }}>
            <Slider
              value={range}
              onChange={(_, v) => setRange(v as [number, number])}
              min={0}
              max={maxIdx}
              size="small"
              valueLabelDisplay="auto"
              valueLabelFormat={(idx) => allRows[idx]?.date ?? ""}
              sx={{ mt: 0.5, "& .MuiSlider-valueLabel": { fontSize: "0.7rem" } }}
            />
            <Stack direction="row" justifyContent="space-between" sx={{ mt: -0.5 }}>
              <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.65rem" }}>
                {allRows[range[0]]?.date ?? "—"}
              </Typography>
              <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.65rem" }}>
                {allRows[range[1]]?.date ?? "—"}
              </Typography>
            </Stack>
          </Box>
        )}
        <CompositionPieChart
          code={etf.code}
          open={compositionOpen}
          onToggle={() => setCompositionOpen(!compositionOpen)}
          onStockOhlcOpenChange={setStockOhlcOpen}
        />
        {filteredRows.length < 40 && (
          <Alert severity="info" sx={{ mt: 0.5, py: 0.25 }} icon={false}>
            Insufficient data ({filteredRows.length} rows).
          </Alert>
        )}
      </Box>
    </ChartCard>
  );
}
