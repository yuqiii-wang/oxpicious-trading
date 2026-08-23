/**
 * CompositionPieChart — 2-layer pie chart for security holdings.
 *
 * Shared between the ETF + Margin page, the Index Baseline page and the ETF
 * Holdings analysis page. Accepts any code (ETF code like "510050" or bare
 * index code like "000300" / "H30007") and fetches composition from
 * /api/sec-composition, which queries stats.sec_composition (ALL holdings
 * for ETFs and full constituents for CSI indices).
 *
 * Seasonal mode: when a `date` prop ("YYYY-MM-DD") is provided, the fetch is
 * constrained to the calendar QUARTER containing the date — the API returns
 * the latest snapshot within that quarter and the response carries a
 * `quarter` label shown in the summary line. Without `date`, the latest
 * snapshot overall is used (original behavior).
 *
 * Color consistency: an optional `colorByIndustry` map (industry → hex color)
 * pins each Layer-1 slice to a fixed color instead of the default palette
 * cycling. The ETF Holdings page passes the same map it uses for its
 * quarterly stacked BAR chart, so a given industry has the SAME color in the
 * bars and in this pie.
 *
 * Layer 1: Pie chart by industry (aggregated weight).
 *   Click an industry slice → drills into Layer 2. Clicking a DIFFERENT
 *   industry switches Layer 2 to that industry; clicking the selected
 *   industry again closes Layer 2.
 * Layer 2: Pie chart of individual stocks within the selected industry.
 *   Click a stock slice → toggles a daily OHLC chart of that stock
 *   below the pies (click the same stock again, or the × button, to close).
 *
 * The `open` / `onToggle` props are controlled by the parent
 * (e.g. EtfMarginPanel / IndexPanel) so the parent box can expand to fit
 * the pie chart. `onStockOhlcOpenChange` lets the parent expand further
 * when the per-stock OHLC expansion is open.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Alert, Box, Button, Chip, CircularProgress, Stack, Typography } from "@mui/material";
import { PieChart as PieChartIcon } from "@mui/icons-material";
import EChart from "@/components/EChart";
import RefreshButton from "@/components/RefreshButton";
import StockOhlcExpansionChart from "@/components/StockOhlcExpansionChart";
import { useStore } from "@/store/filters";
import { fetchSecComposition, invalidateCacheForUrl } from "@/lib/api-client";
import { MUTED_PALETTE, axisColors } from "@/theme/chart-palette";
import { fmtNum, fmtPct } from "@/lib/series";
import type { SecCompositionResponse } from "@shared/types";
import type { EChartsOption } from "echarts";

interface Props {
  /** Security code — ETF code (e.g. "510050") or bare index code (e.g. "000300"). */
  code: string;
  /** Controlled open state — lifted to parent so it can expand the card. */
  open: boolean;
  onToggle: () => void;
  /** Seasonal mode — when provided ("YYYY-MM-DD"), the composition shown is
   *  the latest snapshot within the calendar QUARTER containing this date
   *  (mapped to the corresponding season server-side). */
  date?: string;
  /** Notified when the per-stock OHLC expansion opens/closes so the
   *  parent can expand its card height to fit the chart. */
  onStockOhlcOpenChange?: (open: boolean) => void;
  /** When true, the toggle + refresh buttons are NOT rendered — the parent
   *  renders them in a shared button row (e.g. IndexPanel places the
   *  Composition and Linked-ETFs buttons on the same horizontal row).
   *  Defaults to false (standalone mode used by EtfMarginPanel). */
  hideButton?: boolean;
  /** Optional industry → color map. When provided, each Layer-1 (industry)
   *  slice is pinned to its mapped color instead of cycling MUTED_PALETTE —
   *  used to keep colors consistent with an external chart (e.g. the ETF
   *  Holdings quarterly stacked bars). Layer 2 (stocks) always cycles. */
  colorByIndustry?: Record<string, string>;
  /** External refresh key — when provided, the parent owns the refresh state
   *  and renders its own refresh button. Bumping this value triggers a refetch.
   *  When absent, the internal refresh key is used (standalone mode). */
  refreshKey?: number;
  /** Notifies the parent of loading-state changes so the parent's refresh
   *  button can show a spinner. Only meaningful when hideButton=true. */
  onLoadingChange?: (loading: boolean) => void;
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
  itemStyle?: { color: string };
}

export default function CompositionPieChart({
  code,
  open,
  onToggle,
  date,
  onStockOhlcOpenChange,
  hideButton = false,
  colorByIndustry,
  refreshKey: externalRefreshKey,
  onLoadingChange,
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
  const [internalRefreshKey, setInternalRefreshKey] = useState(0);
  // When the parent provides an external refresh key (hideButton mode), it
  // owns the refresh state; otherwise the internal key is used.
  const refreshKey = externalRefreshKey ?? internalRefreshKey;

  // Ref so onLoadingChange can be called from the fetch effect without
  // being added to the effect's dependency array (avoids re-run loops).
  const onLoadingChangeRef = useRef(onLoadingChange);
  onLoadingChangeRef.current = onLoadingChange;

  // Ref so the stock-pie click handler (set once via onReady) always reads
  // the latest value without needing to re-bind.
  const selectedStockRef = useRef<SelectedStock | null>(null);
  selectedStockRef.current = selectedStock;

  // Fetch composition data when the panel is opened. With a `date` prop the
  // fetch is constrained to that date's quarter (seasonal mode).
  useEffect(() => {
    if (!open || !code) return;
    let cancelled = false;
    setLoading(true);
    onLoadingChangeRef.current?.(true);
    setError(null);
    fetchSecComposition(code, date)
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setLoading(false);
        onLoadingChangeRef.current?.(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
        onLoadingChangeRef.current?.(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, code, date, refreshKey]);

  const handleRefresh = () => {
    const params = new URLSearchParams({ code });
    if (date) params.set("date", date);
    invalidateCacheForUrl(`/api/sec-composition?${params.toString()}`);
    setInternalRefreshKey((k) => k + 1);
  };

  // Reset to industry layer when data changes
  useEffect(() => {
    setSelectedIndustry(null);
    setSelectedStock(null);
  }, [data]);

  // Notify parent whenever the per-stock OHLC expansion toggles, so
  // the parent box can grow to fit the chart.
  useEffect(() => {
    onStockOhlcOpenChange?.(selectedStock != null);
  }, [selectedStock, onStockOhlcOpenChange]);

  // Layer 1: aggregate holdings by industry. When `colorByIndustry` is
  // provided, each industry's slice is pinned to its mapped color so the pie
  // agrees with an external chart using the same mapping (e.g. the ETF
  // Holdings quarterly stacked bars).
  const industryData = useMemo<PieItem[]>(() => {
    if (!data || data.holdings.length === 0) return [];
    const byIndustry = new Map<string, number>();
    for (const h of data.holdings) {
      byIndustry.set(h.industry, (byIndustry.get(h.industry) ?? 0) + h.weight_pct);
    }
    return Array.from(byIndustry.entries())
      .map(([name, value]) => ({
        name,
        value: Number(value.toFixed(2)),
        ...(colorByIndustry?.[name] ? { itemStyle: { color: colorByIndustry[name] } } : {}),
      }))
      .sort((a, b) => b.value - a.value);
  }, [data, colorByIndustry]);

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
      {!hideButton && (
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
      )}

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
                  {data!.holdings.length} holdings ({data!.snapshot_date}
                  {data!.quarter ? ` · ${data!.quarter}` : ""})
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
                  Industry mapping not yet populated — run build_classification.py to classify stocks.
                </Alert>
              )}

              {/* Layer 1 (industry) always shown; Layer 2 (stocks) renders
                  beside it when an industry is selected, instead of overlapping.
                  Clicking another industry switches Layer 2 to it; clicking
                  the selected industry again closes it. */}
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
                      // Click handler: show the clicked industry's stock pie
                      // beside this chart. Clicking a DIFFERENT industry
                      // switches the stock pie to it; clicking the selected
                      // industry again closes it (replaces the old Back
                      // button). The per-stock OHLC selection always resets —
                      // it belongs to the previously selected industry.
                      chart.on("click", (params: { componentType: string; name?: string }) => {
                        if (params.componentType !== "series" || !params.name) return;
                        const name = params.name;
                        setSelectedIndustry((prev) => (prev === name ? null : name));
                        setSelectedStock(null);
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
                    Click an industry to view its stocks
                    {selectedIndustry ? " · click it again to close" : ""}
                  </Typography>
                </Box>
                {selectedIndustry && (
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Typography
                      variant="caption"
                      sx={{ fontSize: "0.7rem", fontWeight: 600, display: "block", mb: 0.25 }}
                      color="text.secondary"
                    >
                      Stocks in &quot;{selectedIndustry}&quot;
                    </Typography>
                    <EChart
                      option={stockOption}
                      height={280}
                      onReady={(chart) => {
                        // Click handler: toggle the per-stock OHLC
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
                      Click a slice to {selectedStock ? "switch" : "show"} the stock OHLC
                      {selectedStock ? " · click again to close" : ""}
                    </Typography>
                  </Box>
                )}
              </Stack>

              {/* Per-stock daily OHLC expansion — opens when a stock
                  slice is clicked in Layer 2. The parent box is notified via
                  onStockOhlcOpenChange so it can grow to fit. */}
              {selectedStock && (
                <StockOhlcExpansionChart
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
