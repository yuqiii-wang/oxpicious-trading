/**
 * CompositionPieChart — 2-layer pie chart for security holdings.
 *
 * Shared between the ETF + Margin page and the Index Baseline page. Accepts
 * any code (ETF code like "510050" or bare index code like "000300" / "H30007")
 * and fetches composition from /api/sec-composition, which queries
 * stats.sec_composition (ALL holdings for ETFs and full constituents
 * for CSI indices).
 *
 * Layer 1: Pie chart by industry (aggregated weight).
 *   Click an industry slice → drills into Layer 2.
 * Layer 2: Pie chart of individual stocks within the selected industry.
 *   "← Back" button returns to Layer 1.
 *   Click a stock slice → toggles a daily candlestick chart of that stock
 *   below the pies (click the same stock again, or the × button, to close).
 *
 * The `open` / `onToggle` props are controlled by the parent
 * (e.g. EtfMarginPanel / IndexPanel) so the parent box can expand to fit
 * the pie chart. `onStockCandleOpenChange` lets the parent expand further
 * when the per-stock candlestick expansion is open.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Alert, Box, Button, Chip, CircularProgress, Stack, Typography } from "@mui/material";
import { PieChart as PieChartIcon } from "@mui/icons-material";
import EChart from "@/components/EChart";
import RefreshButton from "@/components/RefreshButton";
import StockCandlestickChart from "@/components/StockCandlestickChart";
import { useStore } from "@/store/filters";
import { fetchSecComposition, invalidateCacheForUrl } from "@/lib/api-client";
import { MUTED_PALETTE, axisColors } from "@/theme/chart-palette";
import { fmtNum, fmtPct } from "@/lib/series";
import type { SecCompositionResponse } from "../../shared/types";
import type { EChartsOption } from "echarts";

interface Props {
  /** Security code — ETF code (e.g. "510050") or bare index code (e.g. "000300"). */
  code: string;
  /** Controlled open state — lifted to parent so it can expand the card. */
  open: boolean;
  onToggle: () => void;
  /** Notified when the per-stock candlestick expansion opens/closes so the
   *  parent can expand its card height to fit the chart. */
  onStockCandleOpenChange?: (open: boolean) => void;
}

interface SelectedStock {
  code: string;
  name: string;
  weightPct: number;
}

interface PieItem {
  name: string;
  value: number;
  code?: string;
}

export default function CompositionPieChart({
  code,
  open,
  onToggle,
  onStockCandleOpenChange,
}: Props) {
  const themeMode = useStore((s) => s.themeMode);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<SecCompositionResponse | null>(null);
  const [selectedIndustry, setSelectedIndustry] = useState<string | null>(null);
  const [selectedStock, setSelectedStock] = useState<SelectedStock | null>(null);
  // Plot-level refresh key — bumped by the refresh button to force a cache
  // bypass + refetch of this code's composition. Each code has its own
  // cache key (/api/sec-composition?code=…), so only this plot is affected.
  const [refreshKey, setRefreshKey] = useState(0);

  // Refs so the ECharts click handlers (set once via onReady) always read
  // the latest values without needing to re-bind.
  const selectedIndustryRef = useRef<string | null>(null);
  selectedIndustryRef.current = selectedIndustry;
  const selectedStockRef = useRef<SelectedStock | null>(null);
  selectedStockRef.current = selectedStock;

  // Fetch composition data when the panel is opened
  useEffect(() => {
    if (!open || !code) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchSecComposition(code)
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
  }, [open, code, refreshKey]);

  const handleRefresh = () => {
    invalidateCacheForUrl(`/api/sec-composition?code=${code}`);
    setRefreshKey((k) => k + 1);
  };

  // Reset to industry layer when data changes
  useEffect(() => {
    setSelectedIndustry(null);
    setSelectedStock(null);
  }, [data]);

  // Notify parent whenever the per-stock candlestick expansion toggles, so
  // the parent box can grow to fit the chart.
  useEffect(() => {
    onStockCandleOpenChange?.(selectedStock != null);
  }, [selectedStock, onStockCandleOpenChange]);

  // Layer 1: aggregate holdings by industry
  const industryData = useMemo<PieItem[]>(() => {
    if (!data || data.holdings.length === 0) return [];
    const byIndustry = new Map<string, number>();
    for (const h of data.holdings) {
      byIndustry.set(h.industry, (byIndustry.get(h.industry) ?? 0) + h.weight_pct);
    }
    return Array.from(byIndustry.entries())
      .map(([name, value]) => ({ name, value: Number(value.toFixed(2)) }))
      .sort((a, b) => b.value - a.value);
  }, [data]);

  // Layer 2: stocks within the selected industry
  const stockData = useMemo<PieItem[]>(() => {
    if (!data || !selectedIndustry) return [];
    return data.holdings
      .filter((h) => h.industry === selectedIndustry)
      .map((h) => ({
        name: h.stock_name || h.stock_code,
        value: Number(h.weight_pct.toFixed(2)),
        code: h.stock_code,
      }))
      .sort((a, b) => b.value - a.value);
  }, [data, selectedIndustry]);

  // Layer 1 option: industry pie (always rendered while open)
  const industryOption = useMemo<EChartsOption>(() => {
    const c = axisColors(themeMode);
    const textColor = c.textColor;
    const tooltipBg = c.tooltipBg;
    const borderColor = c.splitLineColor;

    return {
      backgroundColor: "transparent",
      animation: false,
      tooltip: {
        trigger: "item",
        formatter: (p: unknown) => {
          const item = p as { name?: string; value?: number };
          return `${item.name}: ${fmtPct(item.value)}`;
        },
        backgroundColor: tooltipBg,
        borderColor,
        textStyle: { color: textColor, fontSize: 11 },
      },
      legend: {
        type: "scroll",
        orient: "vertical",
        right: 0,
        top: "middle",
        textStyle: { color: textColor, fontSize: 9 },
        itemWidth: 8,
        itemHeight: 6,
        pageIconColor: textColor,
        pageTextStyle: { color: textColor },
      },
      series: [
        {
          type: "pie",
          radius: ["25%", "58%"],
          center: ["38%", "50%"],
          data: industryData,
          label: {
            color: textColor,
            fontSize: 9,
            formatter: (p: unknown) => {
              const item = p as { name?: string; percent?: number };
              return `${item.name}\n${fmtNum(item.percent)}%`;
            },
          },
          labelLine: {
            lineStyle: { color: textColor, opacity: 0.5 },
          },
          emphasis: {
            itemStyle: {
              shadowBlur: 10,
              shadowOffsetX: 0,
              shadowColor: "rgba(0,0,0,0.3)",
            },
            scaleSize: 6,
          },
        },
      ],
      color: MUTED_PALETTE,
    };
  }, [industryData, themeMode]);

  // Layer 2 option: stocks within the selected industry (rendered beside Layer 1)
  const stockOption = useMemo<EChartsOption>(() => {
    const c = axisColors(themeMode);
    const textColor = c.textColor;
    const tooltipBg = c.tooltipBg;
    const borderColor = c.splitLineColor;

    return {
      backgroundColor: "transparent",
      animation: false,
      tooltip: {
        trigger: "item",
        formatter: (p: unknown) => {
          const item = p as { name?: string; value?: number };
          return `${item.name}: ${fmtPct(item.value)}`;
        },
        backgroundColor: tooltipBg,
        borderColor,
        textStyle: { color: textColor, fontSize: 11 },
      },
      legend: {
        type: "scroll",
        orient: "vertical",
        right: 0,
        top: "middle",
        textStyle: { color: textColor, fontSize: 9 },
        itemWidth: 8,
        itemHeight: 6,
        pageIconColor: textColor,
        pageTextStyle: { color: textColor },
      },
      series: [
        {
          type: "pie",
          radius: ["25%", "58%"],
          center: ["38%", "50%"],
          data: stockData,
          label: {
            color: textColor,
            fontSize: 9,
            formatter: (p: unknown) => {
              const item = p as { name?: string; percent?: number };
              return `${item.name}\n${fmtNum(item.percent)}%`;
            },
          },
          labelLine: {
            lineStyle: { color: textColor, opacity: 0.5 },
          },
          emphasis: {
            itemStyle: {
              shadowBlur: 10,
              shadowOffsetX: 0,
              shadowColor: "rgba(0,0,0,0.3)",
            },
            scaleSize: 6,
          },
        },
      ],
      color: MUTED_PALETTE,
    };
  }, [stockData, selectedIndustry, themeMode]);

  const hasData = data && data.holdings.length > 0;
  const allUnclassified =
    industryData.length === 1 && industryData[0]?.name === "未分类";
  const isIndexFallback = data?.source === "index";

  // Source badge label + color.
  const sourceBadge = (() => {
    if (!data) return { label: "Full", color: "default" as const };
    if (data.source === "full") return { label: "Full", color: "success" as const };
    return { label: "Index", color: "info" as const };
  })();

  return (
    <Box>
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, flexWrap: "wrap" }}>
        <Button
          size="small"
          variant="outlined"
          startIcon={<PieChartIcon />}
          onClick={onToggle}
          sx={{ fontSize: "0.7rem", textTransform: "none", mt: 0.5 }}
        >
          {open ? "Hide Composition" : "Composition"}
        </Button>
        {open && (
          <RefreshButton
            onClick={handleRefresh}
            loading={loading}
            size="tiny"
            tooltip={`Refresh composition for ${code}`}
          />
        )}
      </Box>

      {open && (
        <Box sx={{ mt: 1 }}>
          {loading && (
            <Stack direction="row" spacing={1} alignItems="center">
              <CircularProgress size={16} />
              <Typography variant="caption" color="text.secondary">
                Loading composition…
              </Typography>
            </Stack>
          )}
          {error && (
            <Alert severity="error" sx={{ py: 0.5 }}>
              {error}
            </Alert>
          )}
          {hasData && !loading && (
            <>
              {/* Top-level summary: holdings count + date + source badge */}
              <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }} flexWrap="wrap" useFlexGap>
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.7rem" }}>
                  {data!.holdings.length} holdings ({data!.snapshot_date})
                </Typography>
                <Chip
                  label={sourceBadge.label}
                  size="small"
                  color={sourceBadge.color}
                  variant="outlined"
                  sx={{ fontSize: "0.6rem", height: 16 }}
                />
                {isIndexFallback && data!.index_source && (
                  <Chip
                    label={`via ${data!.index_source.code} · ${data!.index_source.name}`}
                    size="small"
                    color="info"
                    variant="filled"
                    sx={{ fontSize: "0.6rem", height: 16 }}
                  />
                )}
              </Stack>

              {isIndexFallback && data!.index_source && (
                <Alert severity="info" sx={{ py: 0.25, mb: 0.5 }} icon={false}>
                  No direct holdings available — showing composition of tracking index{" "}
                  <b>{data!.index_source.code}</b> ({data!.index_source.name || "—"}).
                </Alert>
              )}

              {allUnclassified && !selectedIndustry && (
                <Alert severity="info" sx={{ py: 0.25, mb: 0.5 }} icon={false}>
                  Industry mapping not yet populated — run build_stock_industry.py to classify stocks.
                </Alert>
              )}

              {/* Layer 1 (industry) always shown; Layer 2 (stocks) renders
                  beside it when an industry is selected, instead of overlapping.
                  Each chart has its own left-aligned header; the Back button
                  lives on the stock chart's header so it aligns with that chart. */}
              <Stack
                direction={{ xs: "column", sm: "row" }}
                spacing={1}
                alignItems="stretch"
                sx={{ width: "100%" }}
              >
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography
                    variant="caption"
                    sx={{ fontSize: "0.7rem", fontWeight: 600, display: "block", mb: 0.25 }}
                    color="text.secondary"
                  >
                    By Industry
                  </Typography>
                  <EChart
                    option={industryOption}
                    height={280}
                    onReady={(chart) => {
                      // Click handler: drill into industry → show stock pie
                      // beside it. Only active on Layer 1 (selectedIndustry is null).
                      chart.on("click", (params: { componentType: string; name?: string }) => {
                        if (
                          params.componentType === "series" &&
                          params.name &&
                          !selectedIndustryRef.current
                        ) {
                          setSelectedIndustry(params.name);
                        }
                      });
                    }}
                  />
                </Box>
                {selectedIndustry && (
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Stack direction="row" spacing={0.5} alignItems="center" sx={{ mb: 0.25 }}>
                      <Button
                        size="small"
                        onClick={() => {
                          setSelectedIndustry(null);
                          setSelectedStock(null);
                        }}
                        sx={{ fontSize: "0.7rem", textTransform: "none", minWidth: 0, py: 0 }}
                      >
                        ← Back
                      </Button>
                      <Typography
                        variant="caption"
                        sx={{ fontSize: "0.7rem", fontWeight: 600 }}
                        color="text.secondary"
                      >
                        Stocks in &quot;{selectedIndustry}&quot;
                      </Typography>
                    </Stack>
                    <EChart
                      option={stockOption}
                      height={280}
                      onReady={(chart) => {
                        // Click handler: toggle the per-stock candlestick
                        // expansion. Clicking the same stock again closes it;
                        // clicking a different stock switches the chart.
                        chart.on("click", (params: { componentType?: string; data?: unknown }) => {
                          if (params.componentType !== "series") return;
                          const item = params.data as PieItem | undefined;
                          if (!item || !item.code) return;
                          const cur = selectedStockRef.current;
                          if (cur && cur.code === item.code) {
                            setSelectedStock(null);
                          } else {
                            setSelectedStock({
                              code: item.code,
                              name: item.name,
                              weightPct: item.value,
                            });
                          }
                        });
                      }}
                    />
                    <Typography
                      variant="caption"
                      sx={{
                        fontSize: "0.6rem",
                        display: "block",
                        mt: 0.25,
                        textAlign: "center",
                      }}
                      color="text.secondary"
                    >
                      Click a slice to {selectedStock ? "switch" : "show"} the stock candlestick
                      {selectedStock ? " · click again to close" : ""}
                    </Typography>
                  </Box>
                )}
              </Stack>

              {/* Per-stock daily candlestick expansion — opens when a stock
                  slice is clicked in Layer 2. The parent box is notified via
                  onStockCandleOpenChange so it can grow to fit. */}
              {selectedStock && (
                <StockCandlestickChart
                  code={selectedStock.code}
                  name={selectedStock.name}
                  weightPct={selectedStock.weightPct}
                  onClose={() => setSelectedStock(null)}
                />
              )}
            </>
          )}
          {!hasData && !loading && !error && (
            <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.7rem" }}>
              No composition data available.
            </Typography>
          )}
        </Box>
      )}
    </Box>
  );
}
