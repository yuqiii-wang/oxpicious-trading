/**
 * StockPanel — single stock chart + slider.
 *
 * Layout (mirrors IndexPanel + StockOhlcExpansionChart but without intraday
 * expansion — 5-min intraday bars are not yet collected for stocks):
 *   • Daily OHLC + MA5/MA20/MA60/MA120 (computed client-side from close) +
 *     PE ratio on a twin axis (when available — only SZSE stocks publish PE
 *     via the source endpoint).
 *   • Date range slider (windowing) per panel.
 *
 * The OHLC chart itself is the shared `StockOhlcChart` component, reused by
 * the composition pie expansion (StockOhlcExpansionChart) so the two render
 * identically. This file owns only the ChartCard chrome, the slider, and the
 * return badges; `hasOhlc`/`hasPe` are computed here only to drive the
 * subtitle (the chart recomputes them internally).
 */
import { useEffect, useMemo, useState } from "react";
import { Alert, Box, Chip, Slider, Stack, Typography } from "@mui/material";
import ChartCard from "@/components/ChartCard";
import OhlcModeToggle from "@/components/OhlcModeToggle";
import StockOhlcChart from "@/components/StockOhlcChart";
import { fmtPct } from "@/lib/series";
import { type OhlcMode } from "@/lib/ohlc";
import { UP_COLOR, DOWN_COLOR } from "@/theme/chart-palette";
import type { StockBundle } from "../../../../shared/types";

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
  const allRows = stock.rows;
  const maxIdx = allRows.length - 1;
  const [range, setRange] = useState<[number, number]>([0, maxIdx]);
  // OHLC display mode — "percentage" (default) rebases OHLC + MAs to % change
  // from the first valid close; "absolute" shows raw prices.
  const [ohlcMode, setOhlcMode] = useState<OhlcMode>("percentage");

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
  // components, render an OHLC chart; otherwise fall back to a close line.
  const hasOhlc = useMemo(() => {
    if (filteredRows.length === 0) return false;
    const ohlcCount = filteredRows.filter(
      (r) => r.open != null && r.high != null && r.low != null && r.close != null,
    ).length;
    return ohlcCount > 0 && ohlcCount >= filteredRows.length * 0.5;
  }, [filteredRows]);

  const hasPe = useMemo(() => {
    if (filteredRows.length === 0) return false;
    return filteredRows.some((r) => r.pe != null && r.pe !== 0);
  }, [filteredRows]);

  const subtitle = hasOhlc
    ? `${stock.sector_label} / ${stock.industry_label} · OHLC${ohlcMode === "percentage" ? " %" : ""} + MA5/MA20/MA60/MA120${hasPe ? " · PE" : ""}`
    : `${stock.sector_label} / ${stock.industry_label} · Close${ohlcMode === "percentage" ? " %" : ""} + MA5/MA20/MA60/MA120${hasPe ? " · PE" : ""}`;

  const filteredStock: StockBundle = useMemo(
    () => ({ ...stock, rows: filteredRows }),
    [stock, filteredRows],
  );

  return (
    <ChartCard
      title={`${stock.code} · ${stock.name}`}
      subtitle={subtitle}
      action={
        <Stack direction="row" spacing={0.5} alignItems="center" flexWrap="wrap" useFlexGap>
          <OhlcModeToggle value={ohlcMode} onChange={setOhlcMode} />
          <ReturnBadges stock={filteredStock} />
        </Stack>
      }
      height={360}
    >
      <Box sx={{ width: "100%" }}>
        <StockOhlcChart rows={filteredRows} ohlcMode={ohlcMode} height={250} />
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
