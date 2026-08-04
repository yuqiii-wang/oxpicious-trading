/**
 * IntradayPanel — single-code 5-minute intraday OHLC + volume chart for the
 * Live Data page. Renders one code's bars for a single trading day.
 *
 * Layout (mirrors the existing IntradayPanel used by IndexPanel but adds an
 * optional volume bar series for stocks — the stock intraday table has a
 * `volume` column; the index table does not):
 *   • OHLC bars (custom series, green/red on close-vs-open)
 *   • Volume bars (twin axis, only when `hasVolume` is true)
 *   • Time x-axis (HH:MM)
 *   • OHLC mode toggle (absolute / % change rebased to first bar's close)
 *
 * When `showComposition` is true, a Composition button + CompositionPieChart
 * are rendered below the intraday chart — same shared component used by the
 * Index Baseline and ETF + Margin pages.
 */
import { useMemo, useState } from "react";
import { Box, Button, Stack } from "@mui/material";
import { PieChart as PieChartIcon } from "@mui/icons-material";
import ChartCard from "@/components/ChartCard";
import CompositionPieChart from "@/components/CompositionPieChart";
import EChart from "@/components/EChart";
import OhlcModeToggle from "@/components/OhlcModeToggle";
import RefreshButton from "@/components/RefreshButton";
import { fmtNum } from "@/lib/series";
import {
  rebasePriceArrays,
  formatPriceValue,
  type OhlcMode,
} from "@/lib/ohlc";
import {
  UP_COLOR,
  DOWN_COLOR,
  axisColors,
  commonLegend,
  commonGrid,
} from "@/theme/chart-palette";
import { useStore } from "@/store/filters";
import { invalidateCacheForUrl } from "@/lib/api-client";
import type { LiveDataBundle } from "../../../shared/types";
import type { EChartsOption, CustomSeriesRenderItem } from "echarts";

interface Props {
  bundle: LiveDataBundle;
  /** Trading day (YYYY-MM-DD) the bars belong to — shown in the subtitle. */
  date: string;
  /** Whether the source table has a `volume` column (only stocks do). */
  hasVolume: boolean;
  /** When true, renders a Composition button + CompositionPieChart below the
   *  intraday chart. Used by the Index live-data page (mirrors the Index
   *  Baseline panel); stock live-data leaves this off. */
  showComposition?: boolean;
}

export default function IntradayPanel({ bundle, date, hasVolume, showComposition = false }: Props) {
  const themeMode = useStore((s) => s.themeMode);
  const [ohlcMode, setOhlcMode] = useState<OhlcMode>("percentage");

  // Lifted composition panel open state — controls ChartCard height so the
  // pie chart stays inside the parent box when expanded (mirrors IndexPanel).
  const [compositionOpen, setCompositionOpen] = useState(false);
  // Per-stock OHLC expansion open state — when true the card grows
  // further to fit the stock chart below the pie charts.
  const [stockOhlcOpen, setStockOhlcOpen] = useState(false);
  // Plot-level refresh key for the CompositionPieChart — bumped by the
  // panel's refresh button to force a cache bypass + refetch.
  const [compositionRefreshKey, setCompositionRefreshKey] = useState(0);
  const [compositionLoading, setCompositionLoading] = useState(false);

  const handleCompositionRefresh = () => {
    invalidateCacheForUrl(`/api/sec-composition?code=${bundle.code}`);
    setCompositionRefreshKey((k) => k + 1);
  };

  const option = useMemo<EChartsOption>(() => {
    const c = axisColors(themeMode);
    // Pin the x-axis to trading hours 9:30–15:30 on a true time axis so the
    // lunch break (11:30–13:00) shows as a gap and post-market bars beyond
    // 15:30 are excluded. `date` (YYYY-MM-DD) + `b.time` (HH:MM:SS) combine
    // into local timestamps; the browser runs in the user's TZ so 9:30 local
    // == 9:30 Shanghai.
    const TRADING_START = "09:30:00";
    const TRADING_END = "15:30:00";
    const BAR_INTERVAL_MS = 5 * 60 * 1000; // 5-minute intraday bars

    const bars = bundle.bars.filter(
      (b) => b.time >= TRADING_START && b.time <= TRADING_END,
    );
    const tsList = bars.map((b) => new Date(`${date}T${b.time}`).getTime());
    const open = bars.map((b) => b.open);
    const high = bars.map((b) => b.high);
    const low = bars.map((b) => b.low);
    const close = bars.map((b) => b.close);

    // Rebase OHLC to % change from first close in percentage mode.
    // Volume is NOT price-derived — kept in absolute units.
    const { rebased } = rebasePriceArrays(
      { open, high, low, close },
      ohlcMode,
    );

    // OHLC data on a time axis: [timestamp, open, close, low, high].
    const candleData: Array<Array<number | null>> = tsList.map((ts, i) => [
      ts,
      rebased.open[i],
      rebased.close[i],
      rebased.low[i],
      rebased.high[i],
    ]);

    // Volume bars: green when close >= open, red otherwise. Each item carries
    // its own timestamp so the bar series aligns with the time axis.
    const volData = hasVolume
      ? bars.map((b, i) => {
          const o = rebased.open[i];
          const cl = rebased.close[i];
          const up = o != null && cl != null ? cl >= o : true;
          return {
            value: [tsList[i], b.volume],
            itemStyle: { color: up ? UP_COLOR : DOWN_COLOR, opacity: 0.35 },
          };
        })
      : [];

    // Time-axis OHLC renderer. Mirrors the shared ohlcRenderItem but uses the
    // per-bar timestamp (dimension 0) as the x coordinate — the shared one
    // relies on params.dataIndexInside, which only works on a category axis.
    const renderItem: CustomSeriesRenderItem = (params, api) => {
      const ts = api.value(0);
      const openVal = api.value(1);
      const closeVal = api.value(2);
      const lowVal = api.value(3);
      const highVal = api.value(4);
      if (
        typeof ts !== "number" || !Number.isFinite(ts) ||
        typeof openVal !== "number" || !Number.isFinite(openVal) ||
        typeof closeVal !== "number" || !Number.isFinite(closeVal) ||
        typeof lowVal !== "number" || !Number.isFinite(lowVal) ||
        typeof highVal !== "number" || !Number.isFinite(highVal)
      ) {
        return undefined;
      }
      const high = api.coord([ts, highVal]);
      const low = api.coord([ts, lowVal]);
      const openPt = api.coord([ts, openVal]);
      const closePt = api.coord([ts, closeVal]);
      const x = high[0];
      let band = 6;
      if (api.size) {
        const s = api.size([BAR_INTERVAL_MS, 0]);
        band = Array.isArray(s) ? (s[0] as number) : (s as number);
      }
      const tickLen = Math.max(1, band * 0.3);
      const color = closeVal >= openVal ? UP_COLOR : DOWN_COLOR;
      return {
        type: "group",
        children: [
          { type: "line", shape: { x1: x, y1: high[1], x2: x, y2: low[1] }, style: { stroke: color, lineWidth: 1 } },
          { type: "line", shape: { x1: x - tickLen, y1: openPt[1], x2: x, y2: openPt[1] }, style: { stroke: color, lineWidth: 1 } },
          { type: "line", shape: { x1: x, y1: closePt[1], x2: x + tickLen, y2: closePt[1] }, style: { stroke: color, lineWidth: 1 } },
        ],
      };
    };

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
    if (hasVolume) {
      (yAxis as Array<unknown>).push({
        type: "value",
        scale: true,
        name: "Vol (sh)",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: number) => fmtNum(v) },
        splitLine: { show: false },
      });
    }

    const series: EChartsOption["series"] = [
      {
        type: "custom",
        name: "OHLC",
        yAxisIndex: 0,
        z: 5,
        data: candleData,
        encode: { x: 0, y: [1, 2, 3, 4] },
        renderItem,
        clip: true,
      },
    ];
    if (hasVolume) {
      series.push({
        type: "bar",
        name: "Volume",
        yAxisIndex: 1,
        data: volData,
        barWidth: "90%",
        z: 1,
      });
    }

    const minTs = date ? new Date(`${date}T${TRADING_START}`).getTime() : undefined;
    const maxTs = date ? new Date(`${date}T${TRADING_END}`).getTime() : undefined;
    const formatHM = (ms: number): string => {
      const d = new Date(ms);
      return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    };
    const isLunch = (ms: number): boolean => {
      const d = new Date(ms);
      const mins = d.getHours() * 60 + d.getMinutes();
      return mins > 11 * 60 + 30 && mins < 13 * 60;
    };

    return {
      backgroundColor: "transparent",
      animation: false,
      grid: commonGrid({ left: 50, right: hasVolume ? 56 : 50, bottom: 28 }),
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross", snap: true },
        backgroundColor: c.tooltipBg,
        borderColor: c.splitLineColor,
        textStyle: { color: c.textColor, fontSize: 11 },
        formatter: (params: unknown) => {
          const arr = (Array.isArray(params) ? params : [params]) as Array<{
            axisValue?: string | number;
            marker?: string;
            seriesName?: string;
            value?: Array<number | null> | number;
          }>;
          if (arr.length === 0) return "";
          const av = arr[0].axisValue;
          const time = typeof av === "number" && Number.isFinite(av)
            ? formatHM(av)
            : String(av ?? "");
          let html = `<div style="font-weight:600;margin-bottom:4px">${time}</div>`;
          for (const p of arr) {
            if (p.value == null) continue;
            const name = p.seriesName ?? "";
            if (name === "Volume") {
              const val = Array.isArray(p.value)
                ? p.value[p.value.length - 1]
                : p.value;
              const v = val as number;
              if (!Number.isFinite(v)) continue;
              html += `<div>${p.marker ?? ""} ${name}: <b>${fmtNum(v)}</b></div>`;
            } else if (Array.isArray(p.value)) {
              // [ts, open, close, low, high] — skip the leading timestamp.
              const off = p.value.length >= 5 ? 1 : 0;
              const o = p.value[off];
              const cl = p.value[off + 1];
              const l = p.value[off + 2];
              const h = p.value[off + 3];
              if (o == null && cl == null && l == null && h == null) continue;
              html += `<div>${p.marker ?? ""} ${name}: O=${formatPriceValue(o, ohlcMode)} H=${formatPriceValue(h, ohlcMode)} L=${formatPriceValue(l, ohlcMode)} C=${formatPriceValue(cl, ohlcMode)}</div>`;
            }
          }
          return html;
        },
      },
      legend: commonLegend(themeMode, { type: "scroll" }),
      xAxis: {
        type: "time",
        min: minTs,
        max: maxTs,
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 8,
          interval: 30 * 60 * 1000,
          formatter: (val: number) => (isLunch(val) ? "" : formatHM(val)),
        },
        splitLine: { show: false },
      },
      yAxis,
      series,
    };
  }, [bundle, date, themeMode, ohlcMode, hasVolume]);

  const subtitle = `${bundle.sector_label} / ${bundle.industry_label} · ${date} · 5-min OHLC${hasVolume ? " + Volume" : ""}${ohlcMode === "percentage" ? " (% change)" : ""} · ${bundle.bars.length} bars`;

  // Dynamic card height: baseline when collapsed; expand when the composition
  // panel opens; expand further when the per-stock OHLC is open.
  // Mirrors IndexPanel's height logic.
  const cardHeight = showComposition && compositionOpen
    ? (stockOhlcOpen ? 1020 : 680)
    : 360;

  return (
    <ChartCard
      title={`${bundle.code} · ${bundle.name}`}
      subtitle={subtitle}
      action={
        <Stack direction="row" spacing={0.5} alignItems="center">
          <OhlcModeToggle value={ohlcMode} onChange={setOhlcMode} />
        </Stack>
      }
      height={cardHeight}
    >
      <Box sx={{ width: "100%" }}>
        <EChart option={option} height={280} />

        {showComposition && (
          <>
            {/* Composition toggle + refresh — shared button row pattern
                (mirrors IndexPanel). The CompositionPieChart renders only
                its content; buttons are owned by this parent via hideButton. */}
            <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, flexWrap: "wrap" }}>
              <Button
                size="small"
                variant="outlined"
                startIcon={<PieChartIcon />}
                onClick={() => setCompositionOpen(!compositionOpen)}
                sx={{ fontSize: "0.7rem", textTransform: "none", mt: 0.5 }}
              >
                {compositionOpen ? "Hide Composition" : "Composition"}
              </Button>
              {compositionOpen && (
                <RefreshButton
                  onClick={handleCompositionRefresh}
                  loading={compositionLoading}
                  size="tiny"
                  tooltip={`Refresh composition for ${bundle.code}`}
                />
              )}
            </Box>

            <CompositionPieChart
              code={bundle.code}
              open={compositionOpen}
              onToggle={() => setCompositionOpen(!compositionOpen)}
              onStockOhlcOpenChange={setStockOhlcOpen}
              hideButton
              refreshKey={compositionRefreshKey}
              onLoadingChange={setCompositionLoading}
            />
          </>
        )}
      </Box>
    </ChartCard>
  );
}
