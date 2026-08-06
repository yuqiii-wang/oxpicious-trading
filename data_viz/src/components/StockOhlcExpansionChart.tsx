/**
 * StockOhlcExpansionChart — closeable daily OHLC expansion for a single
 * stock, rendered below the composition pie chart when the user clicks a
 * stock slice in Layer 2.
 *
 * This is the thin chrome wrapper (Card + header + close button + data
 * fetch). The OHLC chart itself is the shared `StockOhlcChart` component,
 * reused by the Stock Baseline page's StockPanel so the two render
 * identically.
 *
 * Fetches OHLC + PE from /api/stock-baseline (stats.v_stock_baseline). The
 * close (×) button calls `onClose`; the parent CompositionPieChart also
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
  Slider,
  Stack,
  Typography,
} from "@mui/material";
import { Close } from "@mui/icons-material";
import OhlcModeToggle from "@/components/OhlcModeToggle";
import StockOhlcChart from "@/components/StockOhlcChart";
import { fetchStockBaseline } from "@/lib/api-client";
import { type OhlcMode } from "@/lib/ohlc";
import type { StockBaselineResponse } from "../../shared/types";

interface Props {
  /** Stock code — suffixed ("000001.SZ") or bare ("000001"). */
  code: string;
  /** Display name (from the pie chart's stock_name). */
  name: string;
  /** Weight % the stock held in the parent ETF/index (for the subtitle). */
  weightPct?: number;
  onClose: () => void;
}

export default function StockOhlcExpansionChart({
  code,
  name,
  weightPct,
  onClose,
}: Props) {
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

  // Date-range slider state — window the chart to a subrange of rows.
  const allRows = data?.rows ?? [];
  const maxIdx = allRows.length - 1;
  const [range, setRange] = useState<[number, number]>([0, 0]);

  // Reset slider to full range when data changes (new stock selected).
  useEffect(() => {
    setRange([0, allRows.length - 1]);
  }, [code, allRows.length]);

  // Filter rows to the selected date window.
  const filteredRows = useMemo(
    () => allRows.slice(range[0], range[1] + 1),
    [allRows, range],
  );

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
            <>
              <StockOhlcChart
                rows={filteredRows}
                ohlcMode={ohlcMode}
                height={250}
                dividends={data?.dividends ?? []}
              />
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
            </>
          )}
        </Box>
      </CardContent>
    </Card>
  );
}
