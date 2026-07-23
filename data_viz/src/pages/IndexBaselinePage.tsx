/**
 * Index Baseline page — shows daily OHLCV + MA + PE for CSI indices.
 *
 * Layout (mirrors the ETF + Margin page):
 *   • ThemeSelector — two-level cascade (L1 sector → L2 industry), loads from
 *     /api/index-baseline/themes which returns the precomputed taxonomy tree.
 *   • Stack of IndexPanel cards (one per index, full width) — candlestick/line +
 *     MA5/MA20/MA60/MA120 + volume bars + PE ratio.
 *     Clicking a date point that has 5-min intraday bars (gold-ringed marker on
 *     the close line) expands a closeable intraday candlestick chart below.
 *   • Date range slider (windowing) per panel.
 *   • Pagination — 6 indices per page.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  Card,
  CardContent,
  CardHeader,
  CircularProgress,
  IconButton,
  Pagination,
  Slider,
  Stack,
  Typography,
} from "@mui/material";
import { Close } from "@mui/icons-material";
import ChartCard from "@/components/ChartCard";
import CodeSearchBar, { findCodeInThemes } from "@/components/CodeSearchBar";
import EChart from "@/components/EChart";
import ThemeSelector from "@/components/ThemeSelector";
import {
  fetchIndexThemes,
  fetchIndicesCombined,
  fetchIndexIntraday5min,
} from "@/lib/api-client";
import { useStore } from "@/store/filters";
import type {
  SectorNode,
  IndexBundle,
  IndexCombinedResponse,
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

const PAGE_SIZE = 6;

// ---------------------------------------------------------------------------
//  IndexPanel — single index chart + slider + intraday expansion
// ---------------------------------------------------------------------------
function IndexPanel({
  index,
  themeMode,
}: {
  index: IndexBundle;
  themeMode: "light" | "dark";
}) {
  const allRows = index.rows;
  const maxIdx = allRows.length - 1;
  const [range, setRange] = useState<[number, number]>([0, maxIdx]);

  // Intraday 5-min expansion state — keyed by date.
  const [intradayDate, setIntradayDate] = useState<string | null>(null);
  const [intradayData, setIntradayData] = useState<IndexIntraday5minResponse | null>(null);
  const [intradayLoading, setIntradayLoading] = useState(false);
  const [intradayError, setIntradayError] = useState<string | null>(null);

  // Reset slider when data changes.
  useEffect(() => {
    setRange([0, allRows.length - 1]);
    setIntradayDate(null);
    setIntradayData(null);
    setIntradayError(null);
  }, [index.code, allRows.length]);

  // Filter rows to the selected date window
  const filteredRows = useMemo(
    () => allRows.slice(range[0], range[1] + 1),
    [allRows, range],
  );

  // Fetch intraday 5-min bars when a date is selected.
  useEffect(() => {
    if (!index.code || !intradayDate) return;
    let cancelled = false;
    setIntradayLoading(true);
    setIntradayError(null);
    fetchIndexIntraday5min(index.code, intradayDate)
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
  }, [index.code, intradayDate]);

  // Keep the latest data + click callback in refs so the zrender handlers
  // (registered once on mount) always see current values.
  const datesRef = useRef<string[]>([]);
  const intradaySetRef = useRef<Set<string>>(new Set());
  datesRef.current = filteredRows.map((r) => r.date);
  intradaySetRef.current = new Set(
    filteredRows.filter((r) => r.has_intraday_5mins).map((r) => r.date),
  );

  const handleDateClick = useCallback(
    (date: string) => {
      const row = filteredRows.find((r) => r.date === date);
      if (row?.has_intraday_5mins) {
        setIntradayDate(date);
        setIntradayData(null);
        setIntradayError(null);
      }
    },
    [filteredRows],
  );

  const clickCbRef = useRef<(date: string) => void>(() => {});
  clickCbRef.current = handleDateClick;

  // Per-date cursor + click via zrender (the raw rendering layer).
  const handleReady = useCallback((chart: echarts.ECharts) => {
    const zr = chart.getZr();
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
    if (filteredRows.length === 0) return false;
    const ohlcCount = filteredRows.filter(
      (r) => r.open != null && r.high != null && r.low != null && r.close != null,
    ).length;
    return ohlcCount > 0 && ohlcCount >= filteredRows.length * 0.5;
  }, [filteredRows]);

  const option = useMemo<EChartsOption>(() => {
    const c = axisColors(themeMode);
    const rows = filteredRows;
    const dates = rows.map((r) => r.date);
    const open = rows.map((r) => r.open);
    const high = rows.map((r) => r.high);
    const low = rows.map((r) => r.low);
    const close = rows.map((r) => r.close);
    const volume = rows.map((r) => (r.volume != null ? r.volume / 1e6 : null));
    const ma5 = rows.map((r) => r.ma5);
    const ma20 = rows.map((r) => r.ma20);
    const ma60 = rows.map((r) => r.ma60);
    const ma120 = rows.map((r) => r.ma120);
    const pe = rows.map((r) => r.pe);

    const broken = breakArraysAtGaps(dates, [
      open, high, low, close, ma5, ma20, ma60, ma120, volume, pe,
    ]);

    const volData = broken.arrays[8].map((v, i) => {
      const o = broken.arrays[0][i];
      const cl = broken.arrays[3][i];
      const up = o != null && cl != null ? cl >= o : true;
      return { value: v, itemStyle: { color: up ? UP_COLOR : DOWN_COLOR, opacity: 0.35 } };
    });

    const candleData: Array<Array<number | null>> = broken.dates.map((_, i) => [
      broken.arrays[0][i],
      broken.arrays[3][i],
      broken.arrays[2][i],
      broken.arrays[1][i],
    ]);

    return {
      backgroundColor: "transparent",
      animation: false,
      grid: { left: 50, right: 50, top: 16, bottom: 28 },
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
        textStyle: { color: c.textColor, fontSize: 8 },
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
          fontSize: 8,
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
          axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: number) => fmtNum(v) },
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
  }, [filteredRows, themeMode, hasOhlc]);

  // Compute the common (intersection) date range across all rows — used for
  // the default slider window when multiple panels are shown.
  const subtitle = hasOhlc
    ? `${index.sector_label} / ${index.industry_label} · Candlestick OHLC + MA5/MA20/MA60/MA120 · Volume · PE`
    : `${index.sector_label} / ${index.industry_label} · Close + MA5/MA20/MA60/MA120 · Volume · PE`;

  // Dynamic card height: expand when intraday panel is open.
  const cardHeight = intradayDate ? 680 : 360;

  return (
    <ChartCard
      title={`${index.code} · ${index.name}`}
      subtitle={subtitle}
      height={cardHeight}
    >
      <Box sx={{ width: "100%" }}>
        <EChart option={option} height={250} onReady={handleReady} />
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

        {/* Intraday 5-min expansion */}
        {intradayDate && (
          <IntradayPanel
            code={index.code}
            name={index.name}
            date={intradayDate}
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

        {filteredRows.length < 40 && (
          <Alert severity="info" sx={{ mt: 0.5, py: 0.25 }} icon={false}>
            Insufficient data ({filteredRows.length} rows).
          </Alert>
        )}
      </Box>
    </ChartCard>
  );
}

// ---------------------------------------------------------------------------
//  IntradayPanel — closeable 5-min candlestick expansion
// ---------------------------------------------------------------------------
function IntradayPanel({
  code,
  name,
  date,
  data,
  themeMode,
  loading,
  error,
  onClose,
}: {
  code: string;
  name: string;
  date: string;
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
          <IconButton aria-label="close intraday" onClick={onClose} size="small">
            <Close fontSize="small" />
          </IconButton>
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

// ---------------------------------------------------------------------------
//  IndexBaselinePage — two-level selector + paginated index panels
// ---------------------------------------------------------------------------
export default function IndexBaselinePage() {
  const themeMode = useStore((s) => s.themeMode);

  const [sectors, setSectors] = useState<SectorNode[]>([]);
  const [sectorId, setSectorId] = useState<string | null>(null);
  const [industrySlug, setIndustrySlug] = useState<string | null>(null);
  const [data, setData] = useState<IndexCombinedResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  // Active exact-code search (null = browsing mode). When set, only the
  // matching index is fetched and displayed; sector/industry chips are
  // highlighted to show where the code belongs.
  const [searchCode, setSearchCode] = useState<string | null>(null);

  // Load themes (two-level taxonomy tree) once
  useEffect(() => {
    fetchIndexThemes()
      .then((list) => {
        setSectors(list);
        // Auto-select the first sector (highest count) if available
        if (list.length > 0) setSectorId(list[0].sector_id);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  // Reset to page 1 whenever sector or industry changes
  useEffect(() => {
    setPage(1);
  }, [sectorId, industrySlug]);

  // Load index data whenever sector/industry, page, or search code changes.
  // When searchCode is set, fetch only that one index (bypassing sector/pagination).
  useEffect(() => {
    let cancelled = false;
    if (!sectorId && !searchCode) return;
    setLoading(true);
    setError(null);
    const promise = searchCode
      ? fetchIndicesCombined(null, null, null, null, 1, 1, searchCode)
      : fetchIndicesCombined(sectorId, industrySlug, null, null, page, PAGE_SIZE);
    promise
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
  }, [sectorId, industrySlug, page, searchCode]);

  // Resolve a searched code against the themes tree: update sector/industry
  // highlights + activate single-result mode. Shows an error if not found.
  const handleSearch = (code: string) => {
    const found = findCodeInThemes(sectors, code);
    if (!found) {
      setError(`Index code not found: ${code}`);
      setSearchCode(null);
      return;
    }
    setError(null);
    setSectorId(found.sectorId);
    setIndustrySlug(found.industrySlug);
    setSearchCode(code);
    setPage(1);
  };

  // Clearing the search returns to the normal paginated view for the
  // currently highlighted sector/industry.
  const handleClearSearch = () => {
    setSearchCode(null);
  };

  // Clicking a sector/industry chip exits search mode and browses normally.
  const handleSectorChange = (id: string | null) => {
    setSearchCode(null);
    setSectorId(id);
  };
  const handleIndustryChange = (slug: string | null) => {
    setSearchCode(null);
    setIndustrySlug(slug);
  };

  const activeSector = sectors.find((s) => s.sector_id === sectorId);
  const activeIndustry = activeSector?.industries.find(
    (i) => i.industry_slug === industrySlug,
  );
  const headerLabel = activeIndustry
    ? `${activeSector?.sector_label ?? ""} / ${activeIndustry.industry_label}`
    : activeSector
      ? `${activeSector.sector_label} (All)`
      : "Select a sector";
  const totalPages = data?.total_pages ?? 1;

  return (
    <Box>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 2, flexWrap: "wrap" }}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>
            Index Baseline
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {headerLabel} — interactive mirror of plot_csindex.py
          </Typography>
        </Box>
        <CodeSearchBar
          activeCode={searchCode}
          onSearch={handleSearch}
          onClear={handleClearSearch}
          placeholder="Index code (e.g. 000300)"
        />
      </Box>

      <ThemeSelector
        sectors={sectors}
        sectorId={sectorId}
        industrySlug={industrySlug}
        onSectorChange={handleSectorChange}
        onIndustryChange={handleIndustryChange}
      />

      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress size={32} />
        </Box>
      )}
      {error && (
        <Alert severity="error" variant="filled">
          Failed to load index data: {error}
        </Alert>
      )}
      {!loading && !error && data && (
        <>
          {data.indices.length === 0 ? (
            <Alert severity="warning">
              {searchCode
                ? `No data available for index code: ${searchCode}`
                : "No indices in this sector/industry for the selected date range."}
            </Alert>
          ) : (
            <>
              <Typography variant="caption" color="text.secondary">
                {searchCode
                  ? `Search result for ${searchCode} · ${data.dates[0] ?? "—"} → ${data.dates[data.dates.length - 1] ?? "—"}`
                  : `${data.indices.length} indices on this page · ${data.total_indices} total · page ${data.page}/${data.total_pages} · ${data.dates[0] ?? "—"} → ${data.dates[data.dates.length - 1] ?? "—"}`}
              </Typography>
              <Stack spacing={1.5} sx={{ mt: 0.5 }}>
                {data.indices.map((idx) => (
                  <IndexPanel
                    key={idx.code}
                    index={idx}
                    themeMode={themeMode}
                  />
                ))}
              </Stack>
              {!searchCode && totalPages > 1 && (
                <Box sx={{ display: "flex", justifyContent: "center", pt: 2, pb: 1 }}>
                  <Pagination
                    count={totalPages}
                    page={page}
                    onChange={(_e, v) => setPage(v)}
                    color="primary"
                    showFirstButton
                    showLastButton
                  />
                </Box>
              )}
            </>
          )}
        </>
      )}
    </Box>
  );
}
