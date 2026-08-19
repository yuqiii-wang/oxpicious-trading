/**
 * IntradayPanel — closeable 5-min OHLC expansion for the Index
 * Baseline page. Rendered below an IndexPanel when the user clicks a date
 * that has 5-minute intraday bars (gold-ringed marker on the close line).
 */
import React, { useMemo, useState } from "react";
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
import RefreshButton from "@/components/RefreshButton";
import {
  ohlcSeries,
  rebasePriceArrays,
  formatPriceValue,
  type OhlcMode,
} from "@/lib/ohlc";
import { renderReactElement, tooltipComponents } from "@/lib/react-tooltip-renderer";
import { axisColors } from "@/theme/chart-palette";
import type { IndexIntraday5minResponse } from "@shared/types";
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
  // OHLC display mode — "percentage" (default) rebases intraday OHLC to %
  // change from the first bar's close; "absolute" shows raw prices.
  const [ohlcMode, setOhlcMode] = useState<OhlcMode>("percentage");

  const option = useMemo<EChartsOption>(() => {
    const c = axisColors(themeMode);
    const bars = data?.bars ?? [];
    const times = bars.map((b) => b.time);
    const open = bars.map((b) => b.open);
    const close = bars.map((b) => b.close);
    const low = bars.map((b) => b.low);
    const high = bars.map((b) => b.high);

    // Rebase OHLC to % change from first close in percentage mode.
    const { rebased } = rebasePriceArrays(
      { open, close, low, high },
      ohlcMode,
    );
    const ohlc = times.map((_, i) => [
      rebased.open[i],
      rebased.close[i],
      rebased.low[i],
      rebased.high[i],
    ]);

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

          const makeHeader = (text: string) =>
            React.createElement(tooltipComponents.Header, null, text);
          const makeRow = (children: React.ReactNode, style?: React.CSSProperties) =>
            React.createElement(tooltipComponents.Row, { style }, children);
          const makeTextRow = (marker: string, name: string, text: React.ReactNode) =>
            makeRow([marker, " ", name, ": ", text]);
          const makeBoldRow = (marker: string, name: string, vstr: string) =>
            makeTextRow(marker, name, React.createElement(tooltipComponents.Bold, null, vstr));

          const children: React.ReactNode[] = [];
          children.push(makeHeader(time));

          for (const p of arr) {
            if (p.value == null) continue;
            const name = p.seriesName ?? "";
            if (Array.isArray(p.value)) {
              const [o, cl, l, h] = p.value;
              if (o == null && cl == null && l == null && h == null) continue;
              children.push(makeTextRow(p.marker ?? "", name,
                `O=${formatPriceValue(o, ohlcMode)} H=${formatPriceValue(h, ohlcMode)} L=${formatPriceValue(l, ohlcMode)} C=${formatPriceValue(cl, ohlcMode)}`));
            } else {
              const v = p.value as number;
              if (!Number.isFinite(v)) continue;
              children.push(makeBoldRow(p.marker ?? "", name, formatPriceValue(v, ohlcMode)));
            }
          }

          return renderReactElement(React.createElement(React.Fragment, null, children));
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
      series: [ohlcSeries(ohlc, { name: "5min" })],
    };
  }, [data, themeMode, ohlcMode]);

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
            <OhlcModeToggle value={ohlcMode} onChange={setOhlcMode} />
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
