/**
 * IntradayPanel — closeable 5-min candlestick expansion for the Index
 * Baseline page. Rendered below an IndexPanel when the user clicks a date
 * that has 5-minute intraday bars (gold-ringed marker on the close line).
 */
import { useMemo } from "react";
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
import RefreshButton from "@/components/RefreshButton";
import { fmtNum } from "@/lib/series";
import { candlestickSeries } from "@/lib/candlestick";
import { axisColors } from "@/theme/chart-palette";
import type { IndexIntraday5minResponse } from "../../../../shared/types";
import type { EChartsOption } from "echarts";

interface Props {
  code: string;
  name: string;
  date: string;
  data: IndexIntraday5minResponse | null;
  themeMode: "light" | "dark";
  loading: boolean;
  error: string | null;
  onClose: () => void;
  /** Plot-level refresh — re-fetches the 5-min bars for this (code, date).
   *  Provided by the parent IndexPanel which owns the fetch effect. */
  onRefresh?: () => void;
}

export default function IntradayPanel({
  code,
  name,
  date,
  data,
  themeMode,
  loading,
  error,
  onClose,
  onRefresh,
}: Props) {
  const option = useMemo<EChartsOption>(() => {
    const c = axisColors(themeMode);
    const bars = data?.bars ?? [];
    const times = bars.map((b) => b.time);
    const ohlc = bars.map((b) => [b.open, b.close, b.low, b.high]);

    return {
      backgroundColor: "transparent",
      animation: false,
      grid: { left: 50, right: 20, top: 16, bottom: 28 },
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
          const time = (arr[0].axisValue as string) || "";
          let html = `<div style="font-weight:600;margin-bottom:4px">${time}</div>`;
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
      xAxis: {
        type: "category",
        data: times,
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 8,
          interval: Math.max(1, Math.floor(times.length / 8)),
        },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value",
        scale: true,
        name: "Price",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: number) => fmtNum(v) },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
      series: [candlestickSeries(ohlc, { name: "5min" })],
    };
  }, [data, themeMode]);

  return (
    <Card sx={{ mt: 1 }}>
      <CardHeader
        title={
          <span style={{ fontSize: "0.9rem", fontWeight: 600 }}>
            {code} · {name} — 5-min intraday · {date}
          </span>
        }
        subheader={
          <span style={{ fontSize: "0.7rem", color: "var(--chart-subtitle)" }}>
            {data ? `${data.bars.length} bars` : "Loading…"}
          </span>
        }
        action={
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.25 }}>
            {onRefresh && (
              <RefreshButton
                onClick={onRefresh}
                loading={loading}
                size="tiny"
                tooltip={`Refresh 5-min bars for ${date}`}
              />
            )}
            <IconButton aria-label="close intraday" onClick={onClose} size="small">
              <Close fontSize="small" />
            </IconButton>
          </Box>
        }
        sx={{ pb: 0.5, "& .MuiCardHeader-content": { overflow: "hidden" } }}
      />
      <CardContent sx={{ pt: 0.5, pb: 1.5, height: 280 }}>
        <Box sx={{ width: "100%" }}>
          {loading && (
            <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
              <CircularProgress size={24} />
            </Box>
          )}
          {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}
          {!loading && !error && data && (
            <EChart option={option} height={260} />
          )}
        </Box>
      </CardContent>
    </Card>
  );
}
