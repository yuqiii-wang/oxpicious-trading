/**
 * Performance Attribution analysis page (ETF/Index subjects × Index benchmarks).
 *
 * Layout mirrors the data-viz ETF + Index pages:
 *   • Header — title + subtitle (active sector/industry label)
 *   • Controls — ETF | Index toggle + CodeSearchBar + RefreshButton
 *   • ThemeSelector — two-level cascade (L1 sector → L2 industry)
 *   • Stack of PerfAttrPanel cards — one per code on the current page.
 *     Each panel renders the Fluctuation Attribution chart for the latest
 *     date: grouped bars per benchmark showing benchmark_return (signed,
 *     green=positive, red=negative) on the left axis and code_sec_shared_weight
 *     (overlap %) on the right axis; tooltip includes benchmark_amount (亿).
 *   • Pagination — page_size codes per page.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  Pagination,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import ChartCard from "@/components/ChartCard";
import CodeSearchBar, { findCodeInThemes } from "@/components/CodeSearchBar";
import EChart from "@/components/EChart";
import RefreshButton from "@/components/RefreshButton";
import ThemeSelector from "@/components/ThemeSelector";
import { useStore } from "@/store/filters";
import { fmtNum, fmtYi } from "@/lib/series";
import { UP_COLOR, DOWN_COLOR, MUTED_PALETTE, axisColors } from "@/theme/chart-palette";
import {
  fetchPerfAttrAttribution,
  fetchPerfAttrCodes,
  fetchPerfAttrThemes,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type {
  PerfAttrAttributionResponse,
  PerfAttrCodesResponse,
  PerfAttrSecType,
  SectorNode,
} from "../../../shared/types";
import type { ThemeMode } from "@/store/filters";
import type { EChartsOption } from "echarts";

const PAGE_SIZE = 2;

// ============================================================================
//  Chart: Fluctuation Attribution
//  Vertical grouped bar chart — one pair of bars per benchmark:
//    Bar 1 (left  Y-axis): effective contribution = benchmark_return ×
//                           (code_sec_shared_weight / 100). Green if positive,
//                           red if negative. The raw return is shown in the
//                           label and tooltip for reference.
//    Bar 2 (right Y-axis): code_sec_shared_weight (% overlap with subject)
//
//  Label overlap mitigation:
//    • xAxis category labels rotated 55° and truncated to 6 chars.
//    • Bar value labels are placed INSIDE each bar (insideTop for non-negative,
//      insideBottom for negative) so they never collide with neighbours or
//      with the x-axis — no padding hack. Labels are hidden for tiny bars
//      where they wouldn't fit.
// ============================================================================
function buildFluctuationOption(
  data: PerfAttrAttributionResponse,
  themeMode: ThemeMode,
): EChartsOption {
  const c = axisColors(themeMode);
  // Sort benchmarks by effective contribution (discounted) rather than raw
  // return — more relevant after the discount is applied.
  const sorted = [...data.benchmarks].sort((a, b) => {
    const ar = a.benchmark_return ?? 0;
    const br = b.benchmark_return ?? 0;
    const aw = a.code_sec_shared_weight ?? 0;
    const bw = b.code_sec_shared_weight ?? 0;
    const aeff = ar * (aw / 100);
    const beff = br * (bw / 100);
    if (aeff >= 0 && beff < 0) return -1;
    if (aeff < 0 && beff >= 0) return 1;
    return beff - aeff;
  });

  const labels = sorted.map((b) => b.benchmark_name || b.benchmark_code);
  const returns = sorted.map((b) => b.benchmark_return);
  const sharedWts = sorted.map((b) => b.code_sec_shared_weight);
  const amounts = sorted.map((b) => b.benchmark_amount);
  const activeReturns = sorted.map((b) => b.active_return);
  const codes = sorted.map((b) => b.benchmark_code);

  // Discounted (effective) contribution: return × overlap_fraction
  const contrib = sorted.map((b, i) => {
    const r = returns[i];
    const w = sharedWts[i];
    if (r == null || w == null) return null as number | null;
    return r * (w / 100);
  });

  // Max absolute contribution — used to hide labels that can't fit.
  const maxAbsContrib = contrib.reduce(
    (m, v) => (v == null ? m : Math.max(m, Math.abs(v))),
    0,
  );
  const LABEL_MIN_RATIO = 0.08; // hide label if bar < 8% of max height

  // Per-bar color: green for rise, red for drop, neutral gray when null.
  // Color still follows RAW return (green/red tells the direction of the
  // underlying benchmark move), while height shows the discounted impact.
  const returnColors = returns.map((v) =>
    v == null ? c.axisLineColor : v >= 0 ? UP_COLOR : DOWN_COLOR,
  );

  // Signed-position helpers for bar labels. Positive contributions label
  // near the top edge, negative near the bottom edge, both INSIDE the bar.
  const contribLabelPosition = (val: number | null) => {
    if (val == null) return "insideTop" as const;
    return val >= 0 ? ("insideTop" as const) : ("insideBottom" as const);
  };
  const contribLabelVisible = (val: number | null) => {
    if (val == null || maxAbsContrib === 0) return false;
    return Math.abs(val) / maxAbsContrib >= LABEL_MIN_RATIO;
  };

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: { left: 64, right: 64, top: 30, bottom: 96 },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      backgroundColor: c.tooltipBg,
      borderColor: c.splitLineColor,
      textStyle: { color: c.textColor, fontSize: 11 },
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          dataIndex?: number;
          seriesName?: string;
          value?: number | null;
        }>;
        if (arr.length === 0) return "";
        const idx = arr[0].dataIndex ?? 0;
        const code = codes[idx];
        const rv = returns[idx];
        const cv = contrib[idx];
        const sw = sharedWts[idx];
        const amt = amounts[idx];
        const ar = activeReturns[idx];
        const sign = cv == null ? "" : cv >= 0 ? "▲ " : "▼ ";
        const rsign = rv == null ? "" : rv >= 0 ? "▲ " : "▼ ";
        return `
          <div style="font-weight:600">${labels[idx]} <span style="opacity:0.6">(${code})</span></div>
          <div style="margin-top:2px">${sign}Contribution (Return×Wt): <b style="color:${cv == null ? c.textColor : cv >= 0 ? UP_COLOR : DOWN_COLOR}">${fmtNum(cv, 4)}</b></div>
          <div>${rsign}Raw Return: ${rv == null ? "—" : fmtNum(rv, 4)}</div>
          <div>Active vs subject: ${fmtNum(ar, 4)}</div>
          <div>Shared wt (in subject): ${sw == null ? "—" : fmtNum(sw, 4) + "%"}</div>
          <div>Amount: ${amt == null ? "—" : fmtYi(amt, 2)}</div>
        `;
      },
    },
    legend: {
      top: 0,
      right: 0,
      textStyle: { color: c.textColor, fontSize: 10 },
      itemWidth: 12,
      itemHeight: 7,
      data: ["Contribution", "Shared Wt"],
    },
    xAxis: {
      type: "category",
      data: labels,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 8,
        interval: 0,
        rotate: 55,
        formatter: (v: string) => (v.length > 6 ? v.slice(0, 5) + "…" : v),
      },
      splitLine: { show: false },
    },
    yAxis: [
      {
        type: "value",
        name: "Contribution",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => fmtNum(v, 2),
        },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
      {
        type: "value",
        name: "Shared Wt %",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => fmtNum(v, 1) + "%",
        },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: "Contribution",
        type: "bar",
        yAxisIndex: 0,
        // Per-item label: each bar carries its own label position + visibility
        // (series-level label.position doesn't accept per-item functions in
        // the ECharts TS bindings, and per-item overrides are the documented
        // escape hatch for directional bar charts).
        data: contrib.map((v, i) => {
          const visible = contribLabelVisible(v);
          const pos: "insideTop" | "insideBottom" = contribLabelPosition(v);
          const raw = returns[i];
          const rawStr =
            raw == null ? "" : `  [${raw >= 0 ? "▲" : "▼"}${fmtNum(raw, 2)}]`;
          const lblText =
            visible && v != null ? fmtNum(v, 2) + rawStr : "";
          return {
            value: v,
            itemStyle: { color: returnColors[i] },
            label: {
              show: visible,
              position: pos,
              distance: 2,
              color: c.textColor,
              fontSize: 8,
              fontWeight: 600,
              formatter: () => lblText,
            },
          };
        }),
        barMaxWidth: 28,
        // Series-level label is a fallback; data items override the key fields.
        label: {
          show: false,
          color: c.textColor,
          fontSize: 8,
        },
      },
      {
        name: "Shared Wt",
        type: "bar",
        yAxisIndex: 1,
        data: sharedWts.map((v) => ({
          value: v,
          itemStyle: { color: MUTED_PALETTE[5], opacity: 0.7 },
          label: {
            show: !(v == null || v < 1.5),
            position: "insideTop",
            distance: 2,
            color: c.textColor,
            fontSize: 8,
            formatter: () =>
              v == null || v < 1.5 ? "" : fmtNum(v, 1) + "%",
          },
        })),
        barMaxWidth: 28,
        label: { show: false, color: c.textColor, fontSize: 8 },
      },
    ],
  };
}

// ============================================================================
//  Panel — one card per code: fetches its attribution and renders the chart.
// ============================================================================
interface PanelProps {
  code: string;
  name: string;
  secType: PerfAttrSecType;
  themeMode: ThemeMode;
}

function PerfAttrPanel({ code, name, secType, themeMode }: PanelProps) {
  const [data, setData] = useState<PerfAttrAttributionResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchPerfAttrAttribution(code, secType)
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
  }, [code, secType]);

  const subtitle = data
    ? `${data.code} · ${data.name || name || "—"} · ${data.latest_date || "—"}`
    : `${code} · ${name || "—"}`;

  return (
    <ChartCard title="Fluctuation Attribution" subtitle={subtitle}>
      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
          <CircularProgress size={20} />
        </Box>
      )}
      {error && (
        <Alert severity="error" sx={{ py: 0.5 }}>
          {error}
        </Alert>
      )}
      {!loading && !error && data && data.benchmarks.length > 0 && (
        <EChart option={buildFluctuationOption(data, themeMode)} height={360} />
      )}
      {!loading && !error && data && data.benchmarks.length === 0 && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
          <Typography variant="body2" color="text.secondary">
            No benchmark data for {code}.
          </Typography>
        </Box>
      )}
    </ChartCard>
  );
}

// ============================================================================
//  Page
// ============================================================================
export default function PerfAttrPage() {
  const themeMode = useStore((s) => s.themeMode);
  // Local sector/industry state (independent from the global ETF/index filters
  // — perf-attr has its own themes tree scoped to sec_type, so a shared global
  // sector_id would not map cleanly between ETF and Index themes).
  const [sectorId, setSectorId] = useState<string | null>(null);
  const [industrySlug, setIndustrySlug] = useState<string | null>(null);

  const [secType, setSecType] = useState<PerfAttrSecType>("etf");
  const [sectors, setSectors] = useState<SectorNode[]>([]);
  const [codesData, setCodesData] = useState<PerfAttrCodesResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [searchCode, setSearchCode] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  // ---- Reset selection when secType changes -------------------------------
  // Wipes themes + codes + sector/industry so the user never sees stale
  // data from the other sec_type while the new sec_type's data is loading.
  useEffect(() => {
    setSectors([]);
    setCodesData(null);
    setError(null);
    setSectorId(null);
    setIndustrySlug(null);
    setSearchCode(null);
    setPage(1);
  }, [secType]);

  // ---- Load themes + codes whenever secType changes or refresh is bumped --
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([fetchPerfAttrThemes(secType), fetchPerfAttrCodes(secType)])
      .then(([t, c]) => {
        if (cancelled) return;
        setSectors(t);
        setCodesData(c);
        // Auto-select the first sector (highest count) if available.
        if (t.length > 0 && sectorId == null) {
          setSectorId(t[0].sector_id);
        }
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [secType, refreshKey]);

  // Reset to page 1 whenever sector or industry changes.
  useEffect(() => {
    setPage(1);
  }, [sectorId, industrySlug]);

  const handleRefresh = () => {
    // All perf-attr endpoints share the "/api/analysis/perf-attr/" prefix.
    invalidateCacheForPrefix("/api/analysis/perf-attr/");
    setRefreshKey((k) => k + 1);
  };

  // Resolve a searched code against the themes tree.
  const handleSearch = (code: string) => {
    const found = findCodeInThemes(sectors, code);
    if (!found) {
      setError(`Code not found in ${secType.toUpperCase()} perf-attr data: ${code}`);
      setSearchCode(null);
      return;
    }
    setError(null);
    setSectorId(found.sectorId);
    setIndustrySlug(found.industrySlug);
    setSearchCode(code);
    setPage(1);
  };

  const handleClearSearch = () => {
    setSearchCode(null);
  };

  const handleSectorChange = (id: string | null) => {
    setSearchCode(null);
    setSectorId(id);
  };
  const handleIndustryChange = (slug: string | null) => {
    setSearchCode(null);
    setIndustrySlug(slug);
  };

  // ---- Filter codes by sector/industry or by exact code search -------------
  const { pageCodes, totalCodes } = useMemo(() => {
    const all = codesData?.codes ?? [];
    if (searchCode) {
      // Exact-code search: bypass sector/industry filter, find the one match.
      const norm = searchCode.toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "");
      const match = all.find(
        (c) => c.code.toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "") === norm,
      );
      return { pageCodes: match ? [match] : [], totalCodes: match ? 1 : 0 };
    }
    // Build the set of codes that belong to the selected sector/industry in
    // the themes tree, then preserve the order from `all` (which is already
    // sorted by avg_abs_active_return DESC).
    const wantedSet = new Set<string>();
    for (const s of sectors) {
      if (sectorId && s.sector_id !== sectorId) continue;
      for (const ind of s.industries) {
        if (industrySlug && ind.industry_slug !== industrySlug) continue;
        for (const item of ind.items) {
          wantedSet.add(item.code);
        }
      }
    }
    const wanted = all.filter((c) => wantedSet.has(c.code));
    return { pageCodes: wanted, totalCodes: wanted.length };
  }, [codesData, sectors, sectorId, industrySlug, searchCode]);

  const totalPages = Math.max(1, Math.ceil(totalCodes / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const visibleCodes = pageCodes.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  const activeSector = sectors.find((s) => s.sector_id === sectorId);
  const activeIndustry = activeSector?.industries.find(
    (i) => i.industry_slug === industrySlug,
  );
  const headerLabel = activeIndustry
    ? `${activeSector?.sector_label ?? ""} / ${activeIndustry.industry_label}`
    : activeSector
      ? `${activeSector.sector_label} (All)`
      : "Select a sector";

  return (
    <Box>
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          gap: 2,
          flexWrap: "wrap",
        }}
      >
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>
            Perf Attribution
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {headerLabel} — daily fluctuation decomposition vs all index benchmarks.
            Green bars = benchmark rose, red = dropped. Shared Wt % = overlap of the
            subject's holdings with each benchmark.
          </Typography>
        </Box>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
          <ToggleButtonGroup
            value={secType}
            exclusive
            size="small"
            onChange={(_, v) => {
              if (v) setSecType(v as PerfAttrSecType);
            }}
          >
            <ToggleButton value="etf">ETF</ToggleButton>
            <ToggleButton value="index">Index</ToggleButton>
          </ToggleButtonGroup>
          <CodeSearchBar
            activeCode={searchCode}
            onSearch={handleSearch}
            onClear={handleClearSearch}
            placeholder={`${secType === "etf" ? "ETF" : "Index"} code (e.g. ${secType === "etf" ? "510050" : "000300"})`}
          />
          <RefreshButton
            onClick={handleRefresh}
            loading={loading}
            label="Refresh"
            tooltip="Refresh perf-attr themes + codes + attribution (bypass cache)"
          />
        </Box>
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
        <Alert severity="error" variant="filled" sx={{ mb: 2 }}>
          Failed to load perf-attr data: {error}
        </Alert>
      )}
      {!loading && !error && (
        <>
          {visibleCodes.length === 0 ? (
            <Alert severity="warning">
              {searchCode
                ? `No data available for code: ${searchCode}`
                : `No ${secType.toUpperCase()} perf-attr data in this sector/industry.`}
            </Alert>
          ) : (
            <>
              <Typography variant="caption" color="text.secondary">
                {searchCode
                  ? `Search result for ${searchCode}`
                  : `${visibleCodes.length} of ${totalCodes} ${secType.toUpperCase()}s on this page · page ${safePage}/${totalPages}`}
              </Typography>
              <Stack spacing={1.5} sx={{ mt: 0.5 }}>
                {visibleCodes.map((c) => (
                  <PerfAttrPanel
                    key={c.code}
                    code={c.code}
                    name={c.name}
                    secType={secType}
                    themeMode={themeMode}
                  />
                ))}
              </Stack>
              {!searchCode && totalPages > 1 && (
                <Box sx={{ display: "flex", justifyContent: "center", pt: 2, pb: 1 }}>
                  <Pagination
                    count={totalPages}
                    page={safePage}
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
