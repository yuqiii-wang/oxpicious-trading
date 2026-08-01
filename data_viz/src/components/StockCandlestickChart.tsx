/**
 * StockCandlestickChart — closeable daily OHLC expansion for a single
 * stock, rendered below the composition pie chart when the user clicks a
 * stock slice in Layer 2.
 *
 * Fetches OHLC + PE from /api/stock-baseline (stats.v_stock_baseline) and
 * renders an OHLC + MA20/MA60 + PE (twin axis, when available) chart.
 * Mirrors IndexPanel's daily chart style on a compact card.
 *
 * The close (×) button calls `onClose`; the parent CompositionPieChart also
 * toggles the slice off when the same stock is clicked again.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Card,
  CardContent,
  CardHeader,
  CircularProgress,
  IconButton,
} from "@mui/material";
import { Close } from "@mui/icons-material";
import EChart from "@/components/EChart";
import OhlcModeToggle from "@/components/OhlcModeToggle";
import { fetchStockBaseline } from "@/lib/api-client";
import { useStore } from "@/store/filters";
import { breakArraysAtGaps, fmtNum, safeMa } from "@/lib/series";
import {
  ohlcSeries,
  rebasePriceArrays,
  formatPriceValue,
  type OhlcMode,
} from "@/lib/ohlc";
import {
  MA20_COLOR,
  MA60_COLOR,
  PE_COLOR,
  axisColors,
  commonLegend,
  commonGrid,
} from "@/theme/chart-palette";
import type { StockBaselineResponse } from "../../shared/types";
import type { EChartsOption } from "echarts";

interface Props {
  /** Stock code — suffixed ("000001.SZ") or bare ("000001"). */
  code: string;
  /** Display name (from the pie chart's stock_name). */
  name: string;
  /** Weight % the stock held in the parent ETF/index (for the subtitle). */
  weightPct?: number;
  onClose: () => void;
}

export default function StockCandlestickChart({
  code,
  name,
  weightPct,
  onClose,
}: Props) {
  const themeMode = useStore((s) => s.themeMode);
  const [data, setData] = useState<StockBaselineResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // OHLC display mode — "percentage" (default) rebases OHLC + MAs to % change
  // from the first valid close; "absolute" shows raw prices.
  const [ohlcMode, setOhlcMode] = useState<OhlcMode>("percentage");

  // Fetch daily OHLC whenever the stock code changes.
  useEffect(() => {
    if (!code) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchStockBaseline(code)
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [code]);

  const hasPe = useMemo(() => {
    if (!data) return false;
    return data.rows.some((r) => r.pe != null);
  }, [data]);

  const option = useMemo<EChartsOption>(() => {
    const c = axisColors(themeMode);
    const rows = data?.rows ?? [];
    const dates = rows.map((r) => r.date);
    const open = rows.map((r) => r.open);
    const high = rows.map((r) => r.high);
    const low = rows.map((r) => r.low);
    const close = rows.map((r) => r.close);
    const pe = rows.map((r) => r.pe);
    const isPeEstimatedNum = rows.map((r) => (r.is_pe_estimated ? 1 : 0));
    const ma20 = safeMa(close, 20);
    const ma60 = safeMa(close, 60);

    // Rebase price-derived arrays (OHLC + MAs) to % change in percentage mode.
    // pe and isPeEstimatedNum are NOT price-derived — kept in absolute units.
    const { rebased } = rebasePriceArrays(
      { open, high, low, close, ma20, ma60 },
      ohlcMode,
    );

    const broken = breakArraysAtGaps(dates, [
      rebased.open, rebased.high, rebased.low, rebased.close,
      rebased.ma20, rebased.ma60, pe, isPeEstimatedNum,
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
      ohlcSeries(candleData, { name: "OHLC", yAxisIndex: 0, z: 5 }),
      {
        type: "line",
        name: "MA20",
        yAxisIndex: 0,
        data: broken.arrays[4],
        smooth: false,
        symbol: "none",
        lineStyle: { color: MA20_COLOR, width: 0.9 },
        z: 4,
      },
      {
        type: "line",
        name: "MA60",
        yAxisIndex: 0,
        data: broken.arrays[5],
        smooth: false,
        symbol: "none",
        lineStyle: { color: MA60_COLOR, width: 0.8, type: "dashed" },
        z: 4,
      },
    ];
    if (hasPe) {
      // Separate PE into actual (solid) and estimated (dashed) series
      const peActual = broken.arrays[6].map((val, i) =>
        broken.arrays[7][i] === 1 ? null : val
      );
      const peEstimated = broken.arrays[6].map((val, i) =>
        broken.arrays[7][i] === 1 ? val : null
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
      grid: commonGrid({ left: 50, right: hasPe ? 60 : 20, bottom: 28 }),
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
          const isPriceSeries = (name: string) =>
            name === "OHLC" || name === "Close" || name.startsWith("MA");
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
              const vstr = isPriceSeries(name)
                ? formatPriceValue(v, ohlcMode)
                : name === "PE" || name === "PE (est)" ? fmtNum(v, 2) : fmtNum(v);
              html += `<div>${p.marker ?? ""} ${name}: <b>${vstr}</b></div>`;
            }
          }
          return html;
        },
      },
      legend: commonLegend(themeMode, { type: "scroll" }),
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
  }, [data, themeMode, hasPe, ohlcMode]);

  const rowCount = data?.rows.length ?? 0;
  const subtitle = data
    ? `${rowCount} bars${data.dates.length > 0 ? ` · ${data.dates[0]} → ${data.dates[data.dates.length - 1]}` : ""}${
        weightPct != null ? ` · ${weightPct.toFixed(2)}% holding` : ""
      }`
    : "Loading…";

  return (
    <Card sx={{ mt: 1 }}>
      <CardHeader
        title={
          <span style={{ fontSize: "0.9rem", fontWeight: 600 }}>
            {code} · {name || data?.name || "—"}
          </span>
        }
        subheader={
          <span style={{ fontSize: "0.7rem", color: "var(--chart-subtitle)" }}>
            {subtitle}
          </span>
        }
        action={
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
            <OhlcModeToggle value={ohlcMode} onChange={setOhlcMode} />
            <IconButton aria-label="close stock chart" onClick={onClose} size="small">
              <Close fontSize="small" />
            </IconButton>
          </Box>
        }
        sx={{ pb: 0.5, "& .MuiCardHeader-content": { overflow: "hidden" } }}
      />
      <CardContent sx={{ pt: 0.5, pb: 1.5, height: 300 }}>
        <Box sx={{ width: "100%" }}>
          {loading && (
            <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
              <CircularProgress size={24} />
            </Box>
          )}
          {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}
          {!loading && !error && data && rowCount === 0 && (
            <Alert severity="info" sx={{ py: 0.5 }}>
              No daily data available for {code}.
            </Alert>
          )}
          {!loading && !error && data && rowCount > 0 && (
            <EChart option={option} height={280} />
          )}
        </Box>
      </CardContent>
    </Card>
  );
}
