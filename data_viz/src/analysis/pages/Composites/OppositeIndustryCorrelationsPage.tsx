/**
 * Opposite Industry Correlations (by benchmark offset) — first Composites
 * detail page.
 *
 * Audits analysis_composites.industry_corr_benchmark_offsets: each
 * industry's MA trend (mean_close) is offset by a broad-market benchmark
 * (benchmark MA rebased to the industry's MA level at each window start,
 * then SUBTRACTED — the common market factor removed; prices recomputed
 * starting at 100) and the pairwise Pearson correlations are charted per
 * 20/60/255-trading-day window next to the RAW overall correlation:
 *
 *   Overall   — raw pairwise MA-curve correlation (benchmark still in).
 *   Offset    — correlation after the benchmark is subtracted from each
 *               industry's trend (market factor removed — the
 *               opposite-industry detector: an industry up while the
 *               benchmark is up more is DOWN after the offset).
 *   Opposite  — score (1 − offset) / 2 in [0, 1]: 1 = perfectly opposite
 *               once the benchmark is removed, 0.5 = uncorrelated, 0 =
 *               perfectly co-moving.
 *
 * Below the chart, a table audits the LATEST window per pair across all
 * three periods (overall / offset / opposite score), sorted by the
 * selected window's opposite score.
 *
 * Refresh button (and the auto-trigger when a fresh selection has no
 * materialized rows) runs `python -m analyze.analysis_composites
 * --industry ... --benchmark ...` in filtered mode (recompute + upsert).
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Autocomplete,
  Box,
  Chip,
  CircularProgress,
  IconButton,
  MenuItem,
  Select,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import RefreshIcon from "@mui/icons-material/Refresh";
import EChart from "@/components/EChart";
import ExpandedTable, { type ExpandedTableColumn } from "@/shared/components/ExpandedTable";
import { useTheme } from "@/hooks/useTheme";
import {
  fetchIndustryCorrOffsetBenchmarks,
  fetchIndustryCorrOffsetIndustries,
  fetchIndustryCorrOffsets,
  runIndustryCorrOffsetsRefresh,
  fetchAnalysisRunStatus,
  INDUSTRY_CORR_OFFSET_RUN_TAG,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type {
  IndustryCorrOffsetIndustry,
  IndustryCorrOffsetRow,
  IndustryCorrOffsetsResponse,
} from "@shared/types";
import type { EChartsOption } from "echarts";
import {
  MUTED_PALETTE,
  UP_COLOR,
  axisColors,
  commonLegend,
  commonGrid,
} from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import { renderReactElement, tooltipComponents } from "@/lib/react-tooltip-renderer";

type PoolSize = "all" | "small" | "mid" | "large";
type CorrWindow = "20d" | "60d" | "255d";
type OffsetMetric = "overall" | "sub" | "score";

const WINDOWS: CorrWindow[] = ["20d", "60d", "255d"];

/** metric → per-window row column. */
const METRIC_COLS: Record<OffsetMetric, Record<CorrWindow, string>> = {
  overall: {
    "20d": "overall_corr_ma20_20d",
    "60d": "overall_corr_ma60_60d",
    "255d": "overall_corr_ma255_255d",
  },
  sub: {
    "20d": "offset_sub_corr_ma20_20d",
    "60d": "offset_sub_corr_ma60_60d",
    "255d": "offset_sub_corr_ma255_255d",
  },
  score: {
    "20d": "opposite_score_ma20_20d",
    "60d": "opposite_score_ma60_60d",
    "255d": "opposite_score_ma255_255d",
  },
};

const METRIC_LABELS: Record<OffsetMetric, string> = {
  overall: "Overall",
  sub: "Offset",
  score: "Opposite",
};

function rowVal(r: IndustryCorrOffsetRow, col: string): number | null {
  return (r as unknown as Record<string, number | null>)[col] ?? null;
}

function pairLabel(r: IndustryCorrOffsetRow): string {
  const short = (label: string, id: string) =>
    label.split("  ")[0] || id;
  return `${short(r.industry_label, r.industry_id)} ↔ ${short(
    r.benchmark_industry_label, r.benchmark_industry_id,
  )}`;
}

export default function OppositeIndustryCorrelationsPage() {
  const { theme: themeMode } = useTheme();

  // ---- Selection state ---------------------------------------------------
  const [industries, setIndustries] = useState<IndustryCorrOffsetIndustry[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [pool, setPool] = useState<PoolSize>("all");
  const [benchmark, setBenchmark] = useState("000300");
  const [benchmarkOptions, setBenchmarkOptions] = useState<string[]>([]);
  const [win, setWin] = useState<CorrWindow>("60d");
  const [metric, setMetric] = useState<OffsetMetric>("sub");

  // ---- Data state --------------------------------------------------------
  const [data, setData] = useState<IndustryCorrOffsetsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ---- Refresh (filtered recompute) — mirrors CorrelationChart ----------
  const [refreshing, setRefreshing] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const autoRunKeyRef = useRef<string | null>(null);
  const selectedIdsRef = useRef(selectedIds);
  const benchmarkRef = useRef(benchmark);
  selectedIdsRef.current = selectedIds;
  benchmarkRef.current = benchmark;
  const wasRunningRef = useRef(false);

  // Load industries + benchmarks once.
  useEffect(() => {
    let cancelled = false;
    fetchIndustryCorrOffsetIndustries()
      .then((resp) => {
        if (cancelled) return;
        setIndustries(resp.industries);
        setSelectedIds((prev) =>
          prev.length > 0
            ? prev
            : resp.industries.filter((i) => i.has_rows).slice(0, 3).map((i) => i.industry_id),
        );
      })
      .catch(() => { /* industries list is best-effort */ });
    fetchIndustryCorrOffsetBenchmarks()
      .then((resp) => {
        if (cancelled) return;
        const codes = resp.benchmarks.map((b) => b.benchmark_code);
        setBenchmarkOptions(codes);
        if (codes.length > 0 && !codes.includes("000300")) setBenchmark(codes[0]);
      })
      .catch(() => { /* benchmarks list is best-effort */ });
    return () => { cancelled = true; };
  }, []);

  // Poll the run tag while refreshing — also on mount once, so a run
  // started elsewhere restores the spinner.
  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const st = await fetchAnalysisRunStatus([INDUSTRY_CORR_OFFSET_RUN_TAG]);
        if (cancelled) return;
        const running = Boolean(st[INDUSTRY_CORR_OFFSET_RUN_TAG]);
        if (wasRunningRef.current && !running) {
          invalidateCacheForPrefix("/api/analysis/industry-corr-offsets");
          setRefreshTick((n) => n + 1);
        }
        wasRunningRef.current = running;
        setRefreshing(running);
      } catch {
        /* status is best-effort */
      }
    };
    void poll();
    if (!refreshing) return;
    const t = setInterval(() => { void poll(); }, 3000);
    return () => { cancelled = true; clearInterval(t); };
  }, [refreshing]);

  const handleRefresh = useCallback(async () => {
    setRefreshError(null);
    setRefreshing(true);
    wasRunningRef.current = true;
    const result = await runIndustryCorrOffsetsRefresh(
      selectedIdsRef.current,
      benchmarkRef.current,
    );
    if (result.already_running) return;
    setRefreshing(false);
    wasRunningRef.current = false;
    if (!result.success) {
      setRefreshError(
        `Offset-corr recompute failed${result.stderr_tail ? `: ${result.stderr_tail.slice(-300)}` : ""}`,
      );
      return;
    }
    invalidateCacheForPrefix("/api/analysis/industry-corr-offsets");
    setRefreshTick((n) => n + 1);
  }, []);

  // Fetch rows whenever the selection / pool / benchmark / refreshTick
  // changes.
  const idsKey = selectedIds.slice().sort().join(",");
  useEffect(() => {
    if (selectedIds.length < 2) {
      setData(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchIndustryCorrOffsets(selectedIds, pool, benchmark)
      .then((resp) => {
        if (cancelled) return;
        setData(resp);
        setLoading(false);
        // Auto-trigger the on-demand recompute when the selection has no
        // materialized rows yet (once per selection key).
        if (resp.offsets.length === 0) {
          const key = `${idsKey}|${pool}|${benchmark}`;
          if (autoRunKeyRef.current !== key) {
            autoRunKeyRef.current = key;
            void handleRefresh();
          }
        }
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idsKey, pool, benchmark, refreshTick]);

  // ---- Chart option — one line per pair on the selected metric/window ---
  const option = useMemo<EChartsOption | null>(() => {
    if (!data || data.offsets.length === 0) return null;
    const c = axisColors(themeMode);

    const byPair = new Map<string, IndustryCorrOffsetRow[]>();
    const pairKeys = new Set<string>();
    for (const row of data.offsets) {
      const key = `${row.industry_id}\u0000${row.benchmark_industry_id}`;
      pairKeys.add(key);
      let arr = byPair.get(key);
      if (!arr) { arr = []; byPair.set(key, arr); }
      arr.push(row);
    }
    const sortedPairs = Array.from(pairKeys).sort();
    const allDatesSet = new Set<string>();
    for (const row of data.offsets) allDatesSet.add(row.start_date);
    const allDates = Array.from(allDatesSet).sort();

    const col = METRIC_COLS[metric][win];
    const series: Array<Record<string, unknown>> = sortedPairs.map((key, i) => {
      const rows = byPair.get(key)!;
      const byDate = new Map(rows.map((r) => [r.start_date, r]));
      const color = MUTED_PALETTE[i % MUTED_PALETTE.length];
      const aligned = allDates.map((d) => rowVal(byDate.get(d) ?? rows[0], col));
      return {
        name: pairLabel(rows[0]),
        type: "line",
        smooth: false,
        showSymbol: false,
        connectNulls: false,
        data: aligned,
        lineStyle: { width: 1.6, color },
        itemStyle: { color },
        z: 3,
      };
    });

    const isScore = metric === "score";
    return {
      backgroundColor: "transparent",
      animation: false,
      grid: commonGrid({ left: 56, right: 24, bottom: 32 }),
      legend: commonLegend(themeMode, {
        data: series.map((s) => s.name as string),
      }),
      tooltip: {
        trigger: "axis",
        backgroundColor: c.tooltipBg,
        borderColor: c.splitLineColor,
        textStyle: { color: c.textColor, fontSize: 11 },
        formatter: (params: unknown) => {
          const arr = (Array.isArray(params) ? params : [params]) as Array<{
            dataIndex?: number;
            seriesName?: string;
          }>;
          if (arr.length === 0) return "";
          const idx0 = arr[0].dataIndex ?? 0;
          const dateStr = allDates[idx0] ?? "";
          if (!dateStr) return "";
          const children: React.ReactNode[] = [];
          children.push(React.createElement(tooltipComponents.Header, null, dateStr));
          children.push(React.createElement("div", {
            style: { marginTop: 2, opacity: 0.7 },
          }, `${METRIC_LABELS[metric]} · ${win} window · benchmark ${data.benchmark_code} (window starts every ${data.offsets[0]?.interval ?? 20} trading days)`));
          const rowChildren: React.ReactNode[] = [];
          const col = METRIC_COLS[metric];
          for (const p of arr) {
            const key = sortedPairs.find((k) => {
              const rows0 = byPair.get(k);
              return rows0 && rows0.length > 0 && pairLabel(rows0[0]) === p.seriesName;
            });
            if (!key) continue;
            const rows = byPair.get(key)!;
            const r = rows.find((x) => x.start_date === dateStr);
            if (!r) continue;
            const pairIdx = sortedPairs.indexOf(key);
            const color = MUTED_PALETTE[pairIdx % MUTED_PALETTE.length];
            const makeChip = (w: CorrWindow, v: number | null) => {
              const isSel = w === win;
              const style: React.CSSProperties = isSel
                ? { fontWeight: 700 }
                : { opacity: 0.55, fontSize: "0.85em" };
              return React.createElement("span", { style },
                `${w}:${v == null || !Number.isFinite(v) ? "—" : fmtNum(v, 3)}`,
              );
            };
            rowChildren.push(React.createElement("div", {
              style: { display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" },
            },
              React.createElement("span", { style: { color } }, "●"),
              React.createElement("span", { style: { flex: 1 } }, p.seriesName ?? ""),
              React.createElement("span", { style: { display: "flex", gap: 6, alignItems: "baseline" } },
                makeChip("20d", rowVal(r, col["20d"])),
                makeChip("60d", rowVal(r, col["60d"])),
                makeChip("255d", rowVal(r, col["255d"])),
              ),
            ));
          }
          children.push(React.createElement("div", { style: { marginTop: 4 } }, rowChildren));
          return renderReactElement(React.createElement(React.Fragment, null, children));
        },
      },
      xAxis: {
        type: "category",
        data: allDates,
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: string) => v.slice(0, 7),
        },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value",
        min: isScore ? 0 : -1,
        max: 1,
        name: isScore ? "Opposite score" : "Correlation",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => fmtNum(v, 2),
        },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
      series,
    };
  }, [data, themeMode, metric, win]);

  // ---- Latest-window audit table ----------------------------------------
  // Latest start_date where the selected window's score is non-null; one
  // row per pair with all three periods' overall / offset− / score values.
  const latestRows = useMemo(() => {
    if (!data || data.offsets.length === 0) return [] as IndustryCorrOffsetRow[];
    const scoreCol = METRIC_COLS.score[win];
    const dates = Array.from(
      new Set(
        data.offsets
          .filter((r) => rowVal(r, scoreCol) != null)
          .map((r) => r.start_date),
      ),
    ).sort();
    if (dates.length === 0) return [];
    const latest = dates[dates.length - 1];
    return data.offsets
      .filter((r) => r.start_date === latest && rowVal(r, scoreCol) != null)
      .sort((a, b) => (rowVal(b, scoreCol) ?? 0) - (rowVal(a, scoreCol) ?? 0));
  }, [data, win]);

  const numPairs = data
    ? new Set(data.offsets.map((r) => `${r.industry_id}|${r.benchmark_industry_id}`)).size
    : 0;
  const selectedIndustries = useMemo(
    () => industries.filter((i) => selectedIds.includes(i.industry_id)),
    [industries, selectedIds],
  );

  return (
    <Box>
      <Typography variant="h5" sx={{ fontWeight: 700, mb: 0.5 }}>
        Opposite Industry Correlations
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2, maxWidth: 900 }}>
        Industry MA trends offset by a broad-market benchmark (rebased to each industry's
        level at the window start, then subtracted — the market factor removed; prices
        recomputed from the offset trend). Audits the raw overall correlation against the
        benchmark-removed correlation and the opposite score (1 − offset) / 2 — 1 =
        perfectly opposite once the benchmark factor is removed. Windows start every 20
        trading days.
      </Typography>

      {/* ---- Controls ---- */}
      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1.5, alignItems: "center", mb: 2 }}>
        <Autocomplete
          multiple
          size="small"
          sx={{ minWidth: 420 }}
          options={industries}
          getOptionLabel={(o) => o.industry_label || o.industry_id}
          value={selectedIndustries}
          onChange={(_, v) => setSelectedIds(v.map((o) => o.industry_id))}
          renderInput={(params) => (
            <TextField {...params} label="Industries (2+)" placeholder="Pick industries…" />
          )}
          renderOption={(props, o) => (
            <li {...props} key={o.industry_id}>
              <Box sx={{ display: "flex", gap: 1, alignItems: "center", width: "100%" }}>
                <span>{o.industry_label || o.industry_id}</span>
                <Box component="span" sx={{ flexGrow: 1 }} />
                {!o.has_rows && (
                  <Chip label="no data yet" size="small" variant="outlined" sx={{ fontSize: "0.65rem" }} />
                )}
              </Box>
            </li>
          )}
          renderTags={(v, getTagProps) =>
            v.map((o, idx) => {
              const { key, ...rest } = getTagProps({ index: idx });
              return <Chip key={key} size="small" label={o.industry_label || o.industry_id} {...rest} />;
            })
          }
        />
        <ToggleButtonGroup
          value={pool}
          exclusive
          size="small"
          onChange={(_, v: PoolSize | null) => v && setPool(v)}
        >
          {(["all", "small", "mid", "large"] as PoolSize[]).map((p) => (
            <ToggleButton key={p} value={p}>{p}</ToggleButton>
          ))}
        </ToggleButtonGroup>
        <Select
          size="small"
          value={benchmarkOptions.includes(benchmark) || benchmarkOptions.length === 0 ? benchmark : benchmarkOptions[0] ?? benchmark}
          onChange={(e) => setBenchmark(e.target.value)}
          sx={{ minWidth: 150 }}
        >
          {(benchmarkOptions.length > 0 ? benchmarkOptions : [benchmark]).map((b) => (
            <MenuItem key={b} value={b}>{`Benchmark ${b}`}</MenuItem>
          ))}
        </Select>
        <Tooltip
          title={
            refreshing
              ? "Recomputing offset correlations for the chosen industries…"
              : "Recompute + upsert offset correlations for the chosen industries"
          }
        >
          <span>
            <IconButton
              size="small"
              onClick={() => { void handleRefresh(); }}
              disabled={refreshing || selectedIds.length < 1}
              aria-label="Recompute offset correlations"
            >
              {refreshing ? <CircularProgress size={18} /> : <RefreshIcon fontSize="small" />}
            </IconButton>
          </span>
        </Tooltip>
      </Box>

      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1.5, alignItems: "center", mb: 1 }}>
        <ToggleButtonGroup
          value={metric}
          exclusive
          size="small"
          onChange={(_, v: OffsetMetric | null) => v && setMetric(v)}
        >
          {(Object.keys(METRIC_LABELS) as OffsetMetric[]).map((m) => (
            <ToggleButton key={m} value={m}>{METRIC_LABELS[m]}</ToggleButton>
          ))}
        </ToggleButtonGroup>
        <ToggleButtonGroup
          value={win}
          exclusive
          size="small"
          onChange={(_, v: CorrWindow | null) => v && setWin(v)}
        >
          {WINDOWS.map((w) => (
            <ToggleButton key={w} value={w}>{w}</ToggleButton>
          ))}
        </ToggleButtonGroup>
        {data && (
          <Typography variant="body2" color="text.secondary">
            {numPairs} pair{numPairs === 1 ? "" : "s"} · {data.offsets.length.toLocaleString()} rows ·
            pool={data.pool_size} · benchmark={data.benchmark_code}
          </Typography>
        )}
      </Box>

      {refreshError && <Alert severity="error" sx={{ py: 0.5, mb: 1 }}>{refreshError}</Alert>}
      {error && <Alert severity="error" sx={{ py: 0.5, mb: 1 }}>Failed to load offset correlations: {error}</Alert>}

      {selectedIds.length < 2 ? (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <Typography variant="body2" color="text.secondary">
            Select 2+ industries to audit their correlations by benchmark offset.
          </Typography>
        </Box>
      ) : (
        <>
          {loading && (
            <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
              <CircularProgress size={24} />
            </Box>
          )}
          {!loading && option && <EChart option={option} height={380} />}
          {!loading && !option && !error && (
            <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
              <Typography variant="body2" color="text.secondary">
                No offset-correlation data for this selection yet. Click the{" "}
                <RefreshIcon sx={{ fontSize: 13, verticalAlign: "-2px" }} /> button to
                compute it, or run <code>python -m analyze.analysis_composites</code>.
              </Typography>
            </Box>
          )}

          {latestRows.length > 0 && (
            <Box sx={{ mt: 2 }}>
              <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
                Latest full {win} window audit — {latestRows[0]?.start_date} (sorted by
                opposite score)
              </Typography>
              <AuditTable rows={latestRows} enableFilters={data.enable_filters} />
            </Box>
          )}
        </>
      )}
    </Box>
  );
}

// ---------------------------------------------------------------------------
//  Audit table — the shared ExpandedTable (layered group headers Overall /
//  Offset / Score over the 20d/60d/255d sub-columns, per-column header
//  filters, zebra rows). Score cells >= 0.7 are highlighted (strongly
//  opposite once the benchmark factor is removed).
// ---------------------------------------------------------------------------
const SCORE_HIGHLIGHT = 0.7;

/** One window sub-column per metric group (shared shape). */
function windowCol(
  metric: OffsetMetric,
  w: CorrWindow,
): ExpandedTableColumn<IndustryCorrOffsetRow> {
  return {
    key: `${metric}-${w}`,
    label: w,
    align: "right",
    width: 58,
    group: METRIC_LABELS[metric],
    render: (r) => {
      const v = rowVal(r, METRIC_COLS[metric][w]);
      if (v == null || !Number.isFinite(v)) return <>—</>;
      if (metric === "score" && v >= SCORE_HIGHLIGHT) {
        return (
          <Box component="span" sx={{ color: UP_COLOR, fontWeight: 600 }}>
            {fmtNum(v, 3)}
          </Box>
        );
      }
      return <>{fmtNum(v, 3)}</>;
    },
    filter: { type: "range", value: (r) => rowVal(r, METRIC_COLS[metric][w]) },
  };
}

function AuditTable({
  rows,
  enableFilters,
}: {
  rows: IndustryCorrOffsetRow[];
  enableFilters: boolean;
}) {
  const columns: ExpandedTableColumn<IndustryCorrOffsetRow>[] = [
    {
      key: "pair",
      label: "Pair",
      width: 220,
      render: (r) => pairLabel(r),
      filter: { type: "ticks", value: (r) => pairLabel(r) },
    },
    ...(["overall", "sub", "score"] as OffsetMetric[]).flatMap((m) =>
      WINDOWS.map((w) => windowCol(m, w)),
    ),
  ];
  return (
    <ExpandedTable
      columns={columns}
      rows={rows}
      rowKey={(r) => `${r.industry_id}|${r.benchmark_industry_id}`}
      maxHeight={320}
      enableFilters={enableFilters}
    />
  );
}
