/**
 * Index Baseline page — shows daily OHLCV + MA + PE for CSI indices.
 *
 * Layout:
 *   • Index selector (Autocomplete)
 *   • Chart 1 (main): Close price + MA5/MA20/MA60/MA120 + volume bars + PE ratio
 *     (PE is merged into the main CardContent on its own right-offset axis;
 *     constituent count is no longer rendered.)
 *     Clicking a date point that has 5-min intraday bars (gold-ringed marker on
 *     the close line) expands a closeable intraday candlestick chart below.
 *   • Date range slider (windowing)
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Autocomplete,
  Box,
  Card,
  CardContent,
  CardHeader,
  CircularProgress,
  IconButton,
  Slider,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { Close } from "@mui/icons-material";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import { fetchIndexList, fetchIndexBaseline, fetchIndexIntraday5min } from "@/lib/api-client";
import { useStore } from "@/store/filters";
import type {
  IndexInfo,
  IndexBaselineResponse,
  IndexIntraday5minResponse,
} from "../../shared/types";
import { breakArraysAtGaps, fmtNum } from "@/lib/series";
import { candlestickSeries } from "@/lib/candlestick";
import {
  MA20_COLOR,
  MA60_COLOR,
  MA120_COLOR,
  MUTED_PALETTE,
  PALETTE_HI,
  PE_COLOR,
  UP_COLOR,
  DOWN_COLOR,
  axisColors,
} from "@/theme/chart-palette";
import type { EChartsOption } from "echarts";
import type * as echarts from "echarts";

function PricePanel({
  data,
  themeMode,
  onDateClick,
}: {
  data: IndexBaselineResponse;
  themeMode: "light" | "dark";
  onDateClick: (date: string) => void;
}) {
  // Keep the latest data + click callback in refs so the zrender handlers
  // (registered once on mount) always see current values.
  const datesRef = useRef<string[]>([]);
  const intradaySetRef = useRef<Set<string>>(new Set());
  const clickCbRef = useRef<(date: string) => void>(() => {});
  datesRef.current = data.rows.map((r) => r.date);
  intradaySetRef.current = new Set(
    data.rows.filter((r) => r.has_intraday_5mins).map((r) => r.date),
  );
  clickCbRef.current = onDateClick;

  // Per-date cursor + click via zrender (the raw rendering layer) so the
  // behaviour follows the whole date column rather than just series points:
  //   • over a date WITH 5-min intraday bars → pointer (clickable)
  //   • over any other date                → crosshair
  // Clicking an intraday date expands the intraday panel below.
  const handleReady = useCallback((chart: echarts.ECharts) => {
    const zr = chart.getZr();
    // Map a canvas x-offset to the nearest valid date index, clamped to the
    // plot area so hover/click anywhere over the chart resolves to a real date
    // (clicks in the left/right margins map to the first/last date).
    const idxFromX = (offsetX: number): number => {
      const val = chart.convertFromPixel({ xAxisIndex: 0 }, offsetX) as number;
      const n = datesRef.current.length;
      if (!Number.isFinite(val)) return -1;
      return Math.max(0, Math.min(n - 1, Math.floor(val)));
    };
    zr.on("mousemove", (e: { offsetX?: number }) => {
      const dates = datesRef.current;
      if (dates.length === 0) return;
      const idx = idxFromX(e.offsetX ?? 0);
      const date = idx >= 0 ? dates[idx] : undefined;
      const dom = chart.getDom();
      if (!dom) return;
      dom.style.cursor =
        date && intradaySetRef.current.has(date) ? "pointer" : "crosshair";
    });
    zr.on("click", (e: { offsetX?: number }) => {
      const dates = datesRef.current;
      if (dates.length === 0) return;
      const idx = idxFromX(e.offsetX ?? 0);
      const date = idx >= 0 ? dates[idx] : undefined;
      if (date && intradaySetRef.current.has(date)) {
        clickCbRef.current(date);
      }
    });
  }, []);

  // Detect whether OHLC is available — when most rows have all four
  // components, render a candlestick; otherwise fall back to a close line.
  const hasOhlc = useMemo(() => {
    const rows = data.rows;
    if (rows.length === 0) return false;
    const ohlcCount = rows.filter(
      (r) => r.open != null && r.high != null && r.low != null && r.close != null,
    ).length;
    return ohlcCount > 0 && ohlcCount >= rows.length * 0.5;
  }, [data]);

  const option = useMemo<EChartsOption>(() => {
    const c = axisColors(themeMode);
    const rows = data.rows;
    const dates = rows.map((r) => r.date);
    const open = rows.map((r) => r.open);
    const high = rows.map((r) => r.high);
    const low = rows.map((r) => r.low);
    const close = rows.map((r) => r.close);
    // Index volume is stored in raw shares — convert to mil (1 mil = 1,000,000 shares)
    const volume = rows.map((r) => (r.volume != null ? r.volume / 1e6 : null));
    const ma5 = rows.map((r) => r.ma5);
    const ma20 = rows.map((r) => r.ma20);
    const ma60 = rows.map((r) => r.ma60);
    const ma120 = rows.map((r) => r.ma120);
    const pe = rows.map((r) => r.pe);

    // Always pass open/high/low/close so the candlestick also breaks at gaps.
    // Index mapping: 0=open, 1=high, 2=low, 3=close, 4=ma5, 5=ma20,
    //                6=ma60, 7=ma120, 8=volume, 9=pe
    const broken = breakArraysAtGaps(dates, [
      open, high, low, close, ma5, ma20, ma60, ma120, volume, pe,
    ]);

    const volData = broken.arrays[8].map((v, i) => {
      const o = broken.arrays[0][i];
      const cl = broken.arrays[3][i];
      const up = o != null && cl != null ? cl >= o : true;
      return { value: v, itemStyle: { color: up ? UP_COLOR : DOWN_COLOR, opacity: 0.35 } };
    });

    // Build candlestick data — gap positions naturally get NaN values from
    // breakArraysAtGaps, which ECharts candlestick handles by skipping those
    // candles (null at the top level would throw "Cannot read properties of
    // null (reading 'value')").
    // ECharts candlestick data order: [open, close, low, high]
    const candleData: Array<Array<number | null>> = broken.dates.map((_, i) => [
      broken.arrays[0][i], // open
      broken.arrays[3][i], // close
      broken.arrays[2][i], // low
      broken.arrays[1][i], // high
    ]);

    return {
      backgroundColor: "transparent",
      animation: false,
      grid: { left: 55, right: 90, top: 30, bottom: 30 },
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
            value?: number | Array<number | string | null>;
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
      legend: {
        top: 0,
        right: 0,
        textStyle: { color: c.textColor, fontSize: 9 },
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
          fontSize: 9,
          formatter: (v: string) => v.slice(0, 7),
          interval: Math.max(1, Math.floor(broken.dates.length / 8)),
        },
        splitLine: { show: false },
      },
      yAxis: [
        {
          type: "value",
          scale: true,
          name: "Price",
          nameTextStyle: { color: c.textColor, fontSize: 9 },
          axisLine: { lineStyle: { color: c.axisLineColor } },
          axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: number) => fmtNum(v) },
          splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
        },
        {
          type: "value",
          scale: true,
          name: "Volume (mil)",
          nameTextStyle: { color: c.textColor, fontSize: 9 },
          axisLine: { lineStyle: { color: c.axisLineColor } },
          axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: number) => fmtNum(v) + " mil" },
          splitLine: { show: false },
        },
        {
          type: "value",
          scale: true,
          name: "PE",
          nameTextStyle: { color: PE_COLOR, fontSize: 9 },
          axisLine: { lineStyle: { color: PE_COLOR } },
          axisLabel: { color: PE_COLOR, fontSize: 9, formatter: (v: number) => fmtNum(v) },
          splitLine: { show: false },
          offset: 40,
        },
      ],
      series: [
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
              lineStyle: { color: PALETTE_HI, width: 1.3 },
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
        {
          type: "bar",
          name: "Volume",
          yAxisIndex: 1,
          data: volData,
          barWidth: "90%",
          z: 1,
        },
        {
          type: "line",
          name: "PE",
          yAxisIndex: 2,
          data: broken.arrays[9],
          smooth: false,
          symbol: "none",
          lineStyle: { color: PE_COLOR, width: 1.1, opacity: 0.85 },
          z: 6,
        },
      ],
    };
  }, [data, themeMode, hasOhlc]);

  const subtitle = hasOhlc
    ? "Candlestick OHLC + MA5/MA20/MA60/MA120 · Volume bars · PE ratio · click a date with 5-min bars (pointer cursor) to expand intraday"
    : "Close + MA5/MA20/MA60/MA120 · Volume bars · PE ratio · click a date with 5-min bars (pointer cursor) to expand intraday";

  return (
    <ChartCard
      title={`${data.code} · ${data.name}`}
      subtitle={subtitle}
      height={380}
    >
      <EChart option={option} height={360} onReady={handleReady} />
    </ChartCard>
  );
}

/**
 * Closeable intraday 5-min candlestick panel — rendered below the daily chart
 * when the user clicks a date that has intraday bars.
 */
function IntradayPanel({
  data,
  themeMode,
  loading,
  error,
  onClose,
}: {
  data: IndexIntraday5minResponse | null;
  themeMode: "light" | "dark";
  loading: boolean;
  error: string | null;
  onClose: () => void;
}) {
  const option = useMemo<EChartsOption>(() => {
    const c = axisColors(themeMode);
    const bars = data?.bars ?? [];
    const times = bars.map((b) => b.time);
    // ECharts candlestick data order: [open, close, low, high]
    const ohlc = bars.map((b) => [b.open, b.close, b.low, b.high]);

    return {
      backgroundColor: "transparent",
      animation: false,
      grid: { left: 55, right: 20, top: 24, bottom: 30 },
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
          fontSize: 9,
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
    <Card sx={{ mb: 2 }}>
      <CardHeader
        title={
          <span style={{ fontSize: "0.95rem", fontWeight: 600 }}>
            {data ? `${data.code} · ${data.name} — 5-min intraday · ${data.date}` : "5-min intraday"}
          </span>
        }
        subheader={
          <span style={{ fontSize: "0.75rem", color: "var(--chart-subtitle)" }}>
            {data ? `${data.bars.length} bars` : "Loading…"}
          </span>
        }
        action={
          <IconButton aria-label="close intraday" onClick={onClose} size="small">
            <Close fontSize="small" />
          </IconButton>
        }
        sx={{ pb: 0.5, "& .MuiCardHeader-content": { overflow: "hidden" } }}
      />
      <CardContent sx={{ pt: 0.5, pb: 1.5, height: 320 }}>
        <Box sx={{ width: "100%" }}>
          {loading && (
            <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
              <CircularProgress size={28} />
            </Box>
          )}
          {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}
          {!loading && !error && data && (
            <EChart option={option} height={300} />
          )}
        </Box>
      </CardContent>
    </Card>
  );
}

export default function IndexBaselinePage() {
  const themeMode = useStore((s) => s.themeMode);
  const [indices, setIndices] = useState<IndexInfo[]>([]);
  const [selectedCode, setSelectedCode] = useState<string>("");
  const [fullData, setFullData] = useState<IndexBaselineResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [range, setRange] = useState<[number, number]>([0, 0]);

  // Intraday 5-min expansion state — keyed by (code, date).
  const [intradayDate, setIntradayDate] = useState<string | null>(null);
  const [intradayData, setIntradayData] = useState<IndexIntraday5minResponse | null>(null);
  const [intradayLoading, setIntradayLoading] = useState(false);
  const [intradayError, setIntradayError] = useState<string | null>(null);

  // Fetch index list on mount
  useEffect(() => {
    let cancelled = false;
    fetchIndexList()
      .then((list) => {
        if (cancelled) return;
        setIndices(list);
        if (list.length > 0) setSelectedCode(list[0].code);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Fetch data when selected code changes
  useEffect(() => {
    if (!selectedCode) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchIndexBaseline(selectedCode)
      .then((d) => {
        if (cancelled) return;
        setFullData(d);
        setRange([0, d.rows.length - 1]);
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
  }, [selectedCode]);

  // Filter data to slider window
  const data = useMemo<IndexBaselineResponse | null>(() => {
    if (!fullData || fullData.rows.length === 0) return fullData;
    const [s, e] = range;
    const rows = fullData.rows.slice(s, e + 1);
    const dates = rows.map((r) => r.date);
    return { ...fullData, dates, rows };
  }, [fullData, range]);

  // Clear the intraday panel when switching to a different index.
  useEffect(() => {
    setIntradayDate(null);
    setIntradayData(null);
    setIntradayError(null);
    setIntradayLoading(false);
  }, [selectedCode]);

  // Fetch intraday 5-min bars when a date is selected.
  useEffect(() => {
    if (!selectedCode || !intradayDate) return;
    let cancelled = false;
    setIntradayLoading(true);
    setIntradayError(null);
    fetchIndexIntraday5min(selectedCode, intradayDate)
      .then((d) => {
        if (cancelled) return;
        setIntradayData(d);
        setIntradayLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setIntradayError(e.message);
        setIntradayLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedCode, intradayDate]);

  // Only expand intraday for dates flagged as having 5-min bars.
  const handleDateClick = useCallback(
    (date: string) => {
      const row = data?.rows.find((r) => r.date === date);
      if (row?.has_intraday_5mins) {
        setIntradayDate(date);
        setIntradayData(null);
        setIntradayError(null);
      }
    },
    [data],
  );

  const selectedInfo = indices.find((i) => i.code === selectedCode);

  return (
    <Stack spacing={2}>
      <Box>
        <Typography variant="h5" sx={{ fontWeight: 700 }}>
          Index Baseline
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {selectedInfo ? `${selectedInfo.name} (${selectedInfo.code})` : "Select an index"} — interactive mirror of plot_csindex.py
        </Typography>
      </Box>

      <Stack direction="row" spacing={2} sx={{ mb: 0 }} alignItems="center">
        <Autocomplete
          sx={{ minWidth: 320, flexGrow: 1 }}
          options={indices}
          getOptionLabel={(o) => `${o.code} · ${o.name}`}
          renderOption={(props, o) => (
            <li {...props}>
              <Typography component="span" sx={{ fontSize: "0.85rem" }}>
                <strong>{o.code}</strong> · {o.name}
                <span style={{ color: "var(--chart-muted-inline)", marginLeft: 8 }}>
                  ({o.n_days} days)
                </span>
              </Typography>
            </li>
          )}
          renderInput={(params) => (
            <TextField {...params} label="Select index" size="small" />
          )}
          value={selectedInfo ?? null}
          onChange={(_e, v) => {
            if (v) setSelectedCode(v.code);
          }}
          isOptionEqualToValue={(a, b) => a.code === b.code}
        />
      </Stack>

      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress />
        </Box>
      )}
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      {data && !loading && (
        <>
          {/* Summary caption — mirrors the etf-margin page header pattern */}
          <Typography variant="caption" color="text.secondary">
            {data.rows.length} trading days · {data.dates[0] ?? "—"} → {data.dates[data.dates.length - 1] ?? "—"}
            {selectedInfo ? ` · ${selectedInfo.n_days} total history` : ""}
          </Typography>

          <PricePanel data={data} themeMode={themeMode} onDateClick={handleDateClick} />

          {intradayDate && (
            <IntradayPanel
              data={intradayData}
              themeMode={themeMode}
              loading={intradayLoading}
              error={intradayError}
              onClose={() => {
                setIntradayDate(null);
                setIntradayData(null);
                setIntradayError(null);
              }}
            />
          )}

          {fullData && fullData.rows.length > 1 && (
            <Box sx={{ px: 1, mt: 1 }}>
              <Slider
                value={range}
                onChange={(_, v) => setRange(v as [number, number])}
                min={0}
                max={fullData.rows.length - 1}
                size="small"
                valueLabelDisplay="auto"
                valueLabelFormat={(idx) => fullData.rows[idx]?.date ?? ""}
                sx={{ mt: 0.5 }}
              />
              <Stack direction="row" justifyContent="space-between">
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.7rem" }}>
                  {fullData.rows[range[0]]?.date ?? "—"}
                </Typography>
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.7rem" }}>
                  {fullData.rows[range[1]]?.date ?? "—"}
                </Typography>
              </Stack>
            </Box>
          )}
        </>
      )}
    </Stack>
  );
}
