/**
 * StockPanel — single stock chart + slider.
 *
 * Layout (mirrors IndexPanel + StockCandlestickChart but without intraday
 * expansion — 5-min intraday bars are not yet collected for stocks):
 *   • Candlestick OHLC + MA5/MA20/MA60/MA120 (computed client-side from
 *     close) + PE ratio on a twin axis (when available — only SZSE stocks
 *     publish PE via the source endpoint).
 *   • Date range slider (windowing) per panel.
 *
 * MA values are computed client-side because the stock baseline view does not
 * carry precomputed MA columns (v_stock_baseline only exposes OHLC +
 * pct_change + PE from the source CSVs).
 */
import { useEffect, useMemo, useState } from "react";
import { Alert, Box, Chip, Slider, Stack, Typography } from "@mui/material";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import { useStore } from "@/store/filters";
import { breakArraysAtGaps, fmtNum, fmtPct, safeMa } from "@/lib/series";
import { candlestickSeries } from "@/lib/candlestick";
import {
  MA20_COLOR,
  MA60_COLOR,
  MA120_COLOR,
  MUTED_PALETTE,
  PE_COLOR,
  UP_COLOR,
  DOWN_COLOR,
  axisColors,
} from "@/theme/chart-palette";
import type { StockBundle } from "../../../../shared/types";
import type { EChartsOption } from "echarts";

interface Props {
  stock: StockBundle;
  /** Optional default slider window (inclusive date strings). When provided
   *  the slider initializes to the indices covering [defaultStartDate,
   *  defaultEndDate] inside this stock's rows — used to align multiple panels
   *  to the shortest common time range. */
  defaultStartDate?: string;
  defaultEndDate?: string;
}

function retBadge(values: Array<number | null>, idxFromEnd: number): number | null {
  const finiteVals = values.filter((v): v is number => v != null && Number.isFinite(v));
  if (finiteVals.length <= idxFromEnd) return null;
  const vnow = finiteVals[finiteVals.length - 1];
  const vthen = finiteVals[finiteVals.length - 1 - idxFromEnd];
  if (!Number.isFinite(vnow) || !Number.isFinite(vthen) || Math.abs(vthen) < 1e-9) return null;
  return (vnow / vthen - 1) * 100;
}

function ReturnBadges({ stock }: { stock: StockBundle }) {
  const close = stock.rows.map((r) => r.close);
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

export default function StockPanel({ stock, defaultStartDate, defaultEndDate }: Props) {
  const themeMode = useStore((s) => s.themeMode);
  const allRows = stock.rows;
  const maxIdx = allRows.length - 1;
  const [range, setRange] = useState<[number, number]>([0, maxIdx]);

  // Reset slider when data changes (e.g., sector switch or page change).
  // When defaultStartDate/defaultEndDate are provided (aligned to the
  // shortest common time range across sibling panels), the slider
  // initializes to the indices covering that window inside this stock's rows.
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
  }, [stock.code, allRows.length, defaultStartDate, defaultEndDate]);

  // Filter rows to the selected date window
  const filteredRows = useMemo(
    () => allRows.slice(range[0], range[1] + 1),
    [allRows, range],
  );

  // Detect whether OHLC is available — when most rows have all four
  // components, render a candlestick; otherwise fall back to a close line.
  const hasOhlc = useMemo(() => {
    if (filteredRows.length === 0) return false;
    const ohlcCount = filteredRows.filter(
      (r) => r.open != null && r.high != null && r.low != null && r.close != null,
    ).length;
    return ohlcCount > 0 && ohlcCount >= filteredRows.length * 0.5;
  }, [filteredRows]);

  const hasPe = useMemo(() => {
    if (filteredRows.length === 0) return false;
    return filteredRows.some((r) => r.pe != null);
  }, [filteredRows]);

  const option = useMemo<EChartsOption>(() => {
    const c = axisColors(themeMode);
    const rows = filteredRows;
    const dates = rows.map((r) => r.date);
    const open = rows.map((r) => r.open);
    const high = rows.map((r) => r.high);
    const low = rows.map((r) => r.low);
    const close = rows.map((r) => r.close);
    const pe = rows.map((r) => r.pe);
    const isPeEstimatedNum = rows.map((r) => (r.is_pe_estimated ? 1 : 0));
    // Compute MA client-side — the stock baseline view does not carry
    // precomputed MA columns (only OHLC + pct_change + PE).
    const ma5 = safeMa(close, 5);
    const ma20 = safeMa(close, 20);
    const ma60 = safeMa(close, 60);
    const ma120 = safeMa(close, 120);

    const broken = breakArraysAtGaps(dates, [
      open, high, low, close, ma5, ma20, ma60, ma120, pe, isPeEstimatedNum,
    ]);

    const candleData: Array<Array<number | null>> = broken.dates.map((_, i) => [
      broken.arrays[0][i],
      broken.arrays[3][i],
      broken.arrays[2][i],
      broken.arrays[1][i],
    ]);

    const yAxis: EChartsOption["yAxis"] = [
      {
        type: "value",
        scale: true,
        name: "Price",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: number) => fmtNum(v) },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
    ];
    if (hasPe) {
      (yAxis as Array<unknown>).push({
        type: "value",
        scale: true,
        name: "PE",
        nameTextStyle: { color: PE_COLOR, fontSize: 9 },
        axisLine: { lineStyle: { color: PE_COLOR } },
        axisLabel: { color: PE_COLOR, fontSize: 9, formatter: (v: number) => fmtNum(v) },
        splitLine: { show: false },
        offset: 40,
      });
    }

    const series: EChartsOption["series"] = [
      ...(hasOhlc
        ? [candlestickSeries(candleData, {
            name: "OHLC",
            yAxisIndex: 0,
            z: 5,
          })]
        : [{
            type: "line" as const,
            name: "Close",
            yAxisIndex: 0,
            data: broken.arrays[3],
            smooth: false,
            symbol: "none",
            lineStyle: { color: MUTED_PALETTE[0], width: 1.3 },
            z: 5,
          }]),
      {
        type: "line",
        name: "MA5",
        yAxisIndex: 0,
        data: broken.arrays[4],
        smooth: false,
        symbol: "none",
        lineStyle: { color: MUTED_PALETTE[2], width: 0.8 },
        z: 4,
      },
      {
        type: "line",
        name: "MA20",
        yAxisIndex: 0,
        data: broken.arrays[5],
        smooth: false,
        symbol: "none",
        lineStyle: { color: MA20_COLOR, width: 0.9 },
        z: 4,
      },
      {
        type: "line",
        name: "MA60",
        yAxisIndex: 0,
        data: broken.arrays[6],
        smooth: false,
        symbol: "none",
        lineStyle: { color: MA60_COLOR, width: 0.8, type: "dashed" },
        z: 4,
      },
      {
        type: "line",
        name: "MA120",
        yAxisIndex: 0,
        data: broken.arrays[7],
        smooth: false,
        symbol: "none",
        lineStyle: { color: MA120_COLOR, width: 0.7, type: "dotted" },
        z: 4,
      },
    ];
    if (hasPe) {
      // Separate PE into actual (solid) and estimated (dashed) series
      const peActual = broken.arrays[8].map((val, i) =>
        broken.arrays[9][i] === 1 ? null : val
      );
      const peEstimated = broken.arrays[8].map((val, i) =>
        broken.arrays[9][i] === 1 ? val : null
      );
      series.push({
        type: "line",
        name: "PE",
        yAxisIndex: 1,
        data: peActual,
        smooth: false,
        symbol: "none",
        lineStyle: { color: PE_COLOR, width: 1.1, opacity: 0.85 },
        z: 6,
      });
      series.push({
        type: "line",
        name: "PE (est)",
        yAxisIndex: 1,
        data: peEstimated,
        smooth: false,
        symbol: "none",
        lineStyle: { color: PE_COLOR, width: 1.1, opacity: 0.6, type: "dashed" },
        z: 6,
      });
    }

    return {
      backgroundColor: "transparent",
      animation: false,
      grid: { left: 50, right: hasPe ? 60 : 50, top: 16, bottom: 28 },
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
            value?: Array<number | null> | number;
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
              html += `<div>${p.marker ?? ""} ${name}: O=${fmtNum(o)} H=${fmtNum(h)} L=${fmtNum(l)} C=${fmtNum(cl)}</div>`;
            } else {
              const v = p.value as number;
              if (!Number.isFinite(v)) continue;
              const vstr = name === "PE" ? fmtNum(v, 2) : fmtNum(v);
              html += `<div>${p.marker ?? ""} ${name}: <b>${vstr}</b></div>`;
            }
          }
          return html;
        },
      },
      legend: {
        top: 0,
        right: 0,
        textStyle: { color: c.textColor, fontSize: 8 },
        itemWidth: 10,
        itemHeight: 6,
        type: "scroll",
      },
      xAxis: {
        type: "category",
        data: broken.dates,
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 8,
          formatter: (v: string) => v.slice(0, 7),
          interval: Math.max(1, Math.floor(broken.dates.length / 8)),
        },
        splitLine: { show: false },
      },
      yAxis,
      series,
    };
  }, [filteredRows, themeMode, hasOhlc, hasPe]);

  const subtitle = hasOhlc
    ? `${stock.sector_label} / ${stock.industry_label} · Candlestick OHLC + MA5/MA20/MA60/MA120${hasPe ? " · PE" : ""}`
    : `${stock.sector_label} / ${stock.industry_label} · Close + MA5/MA20/MA60/MA120${hasPe ? " · PE" : ""}`;

  const filteredStock: StockBundle = useMemo(
    () => ({ ...stock, rows: filteredRows }),
    [stock, filteredRows],
  );

  return (
    <ChartCard
      title={`${stock.code} · ${stock.name}`}
      subtitle={subtitle}
      action={<ReturnBadges stock={filteredStock} />}
      height={360}
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
        {filteredRows.length < 40 && (
          <Alert severity="info" sx={{ mt: 0.5, py: 0.25 }} icon={false}>
            Insufficient data ({filteredRows.length} rows).
          </Alert>
        )}
      </Box>
    </ChartCard>
  );
}
