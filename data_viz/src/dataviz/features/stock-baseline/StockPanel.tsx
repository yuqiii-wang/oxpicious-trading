/**
 * StockPanel — single stock chart + dataZoom.
 *
 * Layout (mirrors IndexPanel + StockOhlcExpansionChart but without intraday
 * expansion — 5-min intraday bars are not yet collected for stocks):
 *   • Daily OHLC + MA5/MA20/MA60/MA120 (computed client-side from close) +
 *     PE ratio on a twin axis (when available — only SZSE stocks publish PE
 *     via the source endpoint).
 *   • In-chart dataZoom (windowing) per panel.
 *
 * The OHLC chart itself is the shared `StockOhlcChart` component, reused by
 * the composition pie expansion (StockOhlcExpansionChart) so the two render
 * identically. This file owns only the ChartCard chrome, the dataZoom window,
 * and the return badges; `hasOhlc`/`hasPe` are computed here only to drive the
 * subtitle (the chart recomputes them internally).
 */
import { useMemo, useRef, useState } from "react";
import { Alert, Box, Chip, Stack } from "@mui/material";
import type { ECharts } from "echarts";
import ChartCard from "@/components/ChartCard";
import OhlcModeToggle from "@/components/OhlcModeToggle";
import StockOhlcChart from "@/components/StockOhlcChart";
import { useAiAskAddon } from "@/shared/ai-ask";
import type { AiAskSpec } from "@/shared/ai-ask";
import { fmtPct } from "@/lib/series";
import { type OhlcMode } from "@/lib/ohlc";
import { UP_COLOR, DOWN_COLOR } from "@/theme/chart-palette";
import type { StockBundle } from "@shared/types";

interface Props {
  stock: StockBundle;
  /** Optional default window (inclusive date strings). When provided
   *  the dataZoom initializes to the range covering [defaultStartDate,
   *  defaultEndDate] inside this stock's rows — used to align multiple panels
   *  to the shortest common time range. */
  defaultStartDate?: string;
  defaultEndDate?: string;
  /** Optional callback fired when the user clicks any date on the chart.
   *  Used by the PE & Dividend analysis page to highlight the matching
   *  month-end row in the stats table. */
  onDateClick?: (date: string) => void;
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

export default function StockPanel({ stock, defaultStartDate, defaultEndDate, onDateClick }: Props) {
  const allRows = stock.rows;
  // OHLC display mode — "percentage" (default) rebases OHLC + MAs to % change
  // from the first valid close; "absolute" shows raw prices.
  const [ohlcMode, setOhlcMode] = useState<OhlcMode>("percentage");

  // Initial dataZoom window. When defaultStartDate/defaultEndDate are
  // provided (aligned to the shortest common time range across sibling
  // panels), the dataZoom initializes to the percentage covering that window
  // inside this stock's full rows; otherwise the full range is shown.
  const dataZoomRange = useMemo<{ start: number; end: number }>(() => {
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
    const n = allRows.length;
    if (n <= 1) return { start: 0, end: 100 };
    return {
      start: (startIdx / (n - 1)) * 100,
      end: (endIdx / (n - 1)) * 100,
    };
  }, [allRows, defaultStartDate, defaultEndDate]);

  // Detect whether OHLC is available — when most rows have all four
  // components, render an OHLC chart; otherwise fall back to a close line.
  const hasOhlc = useMemo(() => {
    if (allRows.length === 0) return false;
    const ohlcCount = allRows.filter(
      (r) => r.open != null && r.high != null && r.low != null && r.close != null,
    ).length;
    return ohlcCount > 0 && ohlcCount >= allRows.length * 0.5;
  }, [allRows]);

  const hasPe = useMemo(() => {
    if (allRows.length === 0) return false;
    return allRows.some((r) => r.pe != null && r.pe !== 0);
  }, [allRows]);

  const subtitle = hasOhlc
    ? `${stock.sector_label} / ${stock.industry_label} · OHLC${ohlcMode === "percentage" ? " %" : ""} + MA5/MA20/MA60/MA120${hasPe ? " · PE" : ""}`
    : `${stock.sector_label} / ${stock.industry_label} · Close${ohlcMode === "percentage" ? " %" : ""} + MA5/MA20/MA60/MA120${hasPe ? " · PE" : ""}`;

  // AI Ask — the option lives inside StockOhlcChart, so the spec carries the
  // semantics (the screenshot + intro do the visual work); state tracks the
  // OHLC/percentage mode toggle.
  const aiAskChartRef = useRef<ECharts | null>(null);
  const aiAskSpec = useMemo<AiAskSpec>(
    () => ({
      intro:
        `Single-stock daily chart of ${stock.code} (${stock.name}, ` +
        `${stock.industry_label}): ` +
        (hasOhlc
          ? "candlesticks (or close line when OHLC is sparse) "
          : "close line ") +
        "with MA5/MA20/MA60/MA120 moving averages, amount bars on a twin axis " +
        "(Amt (亿), close-vs-open colored)" +
        (hasPe ? ", PE ratio on an offset twin axis" : "") +
        ", and dividend diamonds on their ex-dates. Percentage mode rebases " +
        "prices and MAs to % change from the first valid close; the in-chart " +
        "dataZoom slider windows the history.",
      instruments: [{ code: stock.code, name: stock.name, assetClass: "stock" }],
      series: [
        ...(hasOhlc
          ? [{ name: "OHLC", description: "daily open/high/low/close candles" }]
          : [{ name: "Close", unit: "元", description: "daily closing price" }]),
        { name: "MA5", unit: "元", description: "5-day mean of close" },
        { name: "MA20", unit: "元", description: "20-day mean of close" },
        { name: "MA60", unit: "元", description: "60-day mean of close" },
        { name: "MA120", unit: "元", description: "120-day mean of close" },
        { name: "Amt (亿)", unit: "亿", description: "daily trading amount" },
        ...(hasPe ? [{ name: "PE", unit: "×", description: "price/earnings ratio (SZSE stocks)" }] : []),
        { name: "Dividend", description: "ex-dividend markers" },
      ],
      state: { mode: ohlcMode },
      notes: [
        "Return badges in the header: 1m/3m/6m/total % change of close.",
        "Percentage mode rebases from the first VISIBLE close (zoom-dependent).",
      ],
    }),
    [stock.code, stock.name, stock.industry_label, ohlcMode, hasOhlc, hasPe],
  );
  const aiAskAddon = useAiAskAddon({
    title: `${stock.code} · ${stock.name}`,
    subtitle,
    spec: aiAskSpec,
    getInstance: () => aiAskChartRef.current,
  });

  return (
    <ChartCard
      title={`${stock.code} · ${stock.name}`}
      subtitle={subtitle}
      titleAddon={aiAskAddon}
      action={
        <Stack direction="row" spacing={0.5} alignItems="center" flexWrap="wrap" useFlexGap>
          <OhlcModeToggle value={ohlcMode} onChange={setOhlcMode} />
          <ReturnBadges stock={stock} />
        </Stack>
      }
      height={360}
    >
      <Box sx={{ width: "100%" }}>
        <StockOhlcChart
          rows={allRows}
          ohlcMode={ohlcMode}
          height={250}
          dividends={stock.dividends}
          dataZoomStart={dataZoomRange.start}
          dataZoomEnd={dataZoomRange.end}
          onDateClick={onDateClick}
          onChartReady={(c) => {
            aiAskChartRef.current = c;
          }}
        />
        {allRows.length < 40 && (
          <Alert severity="info" sx={{ mt: 0.5, py: 0.25 }} icon={false}>
            Insufficient data ({allRows.length} rows).
          </Alert>
        )}
      </Box>
    </ChartCard>
  );
}
