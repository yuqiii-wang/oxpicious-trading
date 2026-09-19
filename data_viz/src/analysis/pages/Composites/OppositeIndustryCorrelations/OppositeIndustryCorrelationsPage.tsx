/**
 * Opposite Industry Correlations (by benchmark offset) — first Composites
 * detail page.
 *
 * MODULE LAYOUT (one directory, small files):
 *   • constants.ts              — shared types/constants + row helpers.
 *   • corrOffsetChartOption.ts  — ECharts option builder (pair lines +
 *                                 all-windows tooltip).
 *   • useOffsetCorrRefresh.ts   — Refresh button + run-tag poll + auto-run
 *                                 for fresh selections without rows.
 *   • AuditTable.tsx            — the latest-window ExpandedTable audit.
 *   • OppositeIndustryCorrelationsPage.tsx — this page.
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
 *
 * Industry selection uses the SAME shared multi-label nav kit as Industry
 * Sentiments: useSecNav (index-only sector/industry + strategy/theme trees
 * with the exchange filter) + SecClassificationNavMulti — multi-select L2
 * industry chips across sectors, merged (non-exclusive) with the RIGHT
 * strategy→theme column into one industry-id set. The per-industry L3 index
 * row is omitted — pair correlations are per-industry, not per-code.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  IconButton,
  MenuItem,
  Select,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import RefreshIcon from "@mui/icons-material/Refresh";
import EChart from "@/components/EChart";
import { useChartThemeMode } from "@/shared/charts/base-chart";
import { AiAskButton, derivePlotInfo } from "@/shared/ai-ask";
import type { AiAskSpec } from "@/shared/ai-ask";
import type { ECharts } from "echarts";
import { useSecNav } from "@/shared/components/sec-nav";
import SecClassificationNavMulti from "@/shared/components/sec-classification/SecClassificationNavMulti";
import {
  fetchIndustryCorrOffsetBenchmarks,
  fetchIndustryCorrOffsetIndustries,
  fetchIndustryCorrOffsets,
} from "@/lib/api-client";
import type {
  IndustryCorrOffsetIndustry,
  IndustryCorrOffsetRow,
  IndustryCorrOffsetsResponse,
} from "@shared/types";
import AuditTable from "./AuditTable";
import { buildCorrOffsetChartOption } from "./corrOffsetChartOption";
import { useOffsetCorrRefresh } from "./useOffsetCorrRefresh";
import {
  METRIC_COLS,
  METRIC_LABELS,
  THEMES_SOURCES,
  WINDOWS,
  rowVal,
} from "./constants";
import type { CorrWindow, OffsetMetric, PoolSize } from "./constants";

export default function OppositeIndustryCorrelationsPage() {
  const themeMode = useChartThemeMode();

  // ---- Selection state ---------------------------------------------------
  // Shared nav kit (same as Industry Sentiments): loads the sector→industry
  // (LEFT) and strategy→theme (RIGHT) trees for the index sec_type, with the
  // exchange filter. Non-exclusive mode — both columns contribute to the
  // same merged pair set.
  const nav = useSecNav({
    themesSources: THEMES_SOURCES,
    defaultSecType: "index",
    dataLabel: "opposite-industry-correlations",
    mutuallyExclusive: false,
  });
  // Composites industry list (has_rows flags — used only to seed the initial
  // selection with industries that already have materialized rows).
  const [industries, setIndustries] = useState<IndustryCorrOffsetIndustry[]>([]);
  // Multi-select L2 industries (LEFT column): slugs persist across sector
  // switches so the user can pick industries from multiple sectors.
  const [selectedIndustrySlugs, setSelectedIndustrySlugs] = useState<string[]>([]);
  const [pool, setPool] = useState<PoolSize>("all");
  const [benchmark, setBenchmark] = useState("000300");
  const [benchmarkOptions, setBenchmarkOptions] = useState<string[]>([]);
  const [win, setWin] = useState<CorrWindow>("60d");
  const [metric, setMetric] = useState<OffsetMetric>("sub");

  // ---- Derived selection: slugs/themes → industry_ids ---------------------
  // slug → industry_id lookup across the sector tree (LEFT column).
  const slugToIndustryId = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of nav.sectors) {
      for (const ind of s.industries) m.set(ind.industry_slug, ind.industry_id);
    }
    return m;
  }, [nav.sectors]);

  // LEFT column: selected slugs → industry_ids (dropping any slug that no
  // longer maps, e.g. after an exchange switch pruned the tree).
  const selectedIndustryIds = useMemo(
    () =>
      selectedIndustrySlugs
        .map((slug) => slugToIndustryId.get(slug))
        .filter((id): id is string => Boolean(id)),
    [selectedIndustrySlugs, slugToIndustryId],
  );

  // RIGHT column: strategy theme industry_ids, fetched the SAME way as
  // industries (strategy-primary indices carry their theme as industry_id).
  // When no theme is picked, ALL themes under the strategy are included.
  const selectedStrategyThemeIds = useMemo(() => {
    if (!nav.strategyId) return [];
    const strat = nav.strategies.find((s) => s.sector_id === nav.strategyId);
    if (!strat) return [];
    if (nav.themeSlug) {
      const th = strat.industries.find((t) => t.industry_slug === nav.themeSlug);
      return th ? [th.industry_id] : [];
    }
    return strat.industries.map((t) => t.industry_id);
  }, [nav.strategyId, nav.themeSlug, nav.strategies]);

  // Combined industry-id selection the API is queried with (LEFT + RIGHT).
  const selectedIds = useMemo(
    () => [...selectedIndustryIds, ...selectedStrategyThemeIds],
    [selectedIndustryIds, selectedStrategyThemeIds],
  );

  // ---- Data state --------------------------------------------------------
  const [data, setData] = useState<IndustryCorrOffsetsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ---- Refresh (filtered recompute) — see useOffsetCorrRefresh -----------
  const { refreshing, refreshError, refreshTick, handleRefresh, tryAutoRun } =
    useOffsetCorrRefresh(selectedIds, benchmark);

  // Load the composites industry list (has_rows seeding) + benchmarks once.
  useEffect(() => {
    let cancelled = false;
    fetchIndustryCorrOffsetIndustries()
      .then((resp) => {
        if (cancelled) return;
        setIndustries(resp.industries);
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

  // Prune multi-select slugs that no longer exist in the (re)loaded tree
  // (e.g. switching exchange drops mainland-only industries), then seed the
  // multi-select ONCE with the first industries that have materialized offset
  // rows so the page shows data immediately on first load. If nothing
  // materialized maps into the tree, fall back to the first industries of the
  // first sector (the auto-recompute below fetches their rows on demand).
  const seededRef = useRef(false);
  useEffect(() => {
    if (nav.sectors.length === 0) return;
    const validSlugs = new Set<string>();
    for (const s of nav.sectors) {
      for (const ind of s.industries) validSlugs.add(ind.industry_slug);
    }
    setSelectedIndustrySlugs((prev) => {
      const next = prev.filter((slug) => validSlugs.has(slug));
      return next.length === prev.length ? prev : next;
    });
    if (!seededRef.current && selectedIndustrySlugs.length === 0 && !nav.strategyId) {
      const idToSlug = new Map<string, string>();
      for (const [slug, id] of slugToIndustryId) {
        if (!idToSlug.has(id)) idToSlug.set(id, slug);
      }
      const seedSlugs = industries
        .filter((i) => i.has_rows)
        .map((i) => idToSlug.get(i.industry_id))
        .filter((s): s is string => Boolean(s))
        .slice(0, 3);
      if (seedSlugs.length === 0) {
        for (const ind of nav.sectors[0].industries.slice(0, 3)) {
          seedSlugs.push(ind.industry_slug);
        }
      }
      if (seedSlugs.length > 0) {
        seededRef.current = true;
        const firstSector = nav.sectors.find((s) =>
          s.industries.some((i) => seedSlugs.includes(i.industry_slug)),
        );
        if (firstSector) nav.handleSectorChange(firstSector.sector_id);
        setSelectedIndustrySlugs(seedSlugs);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nav.sectors, nav.strategyId, selectedIndustrySlugs, industries, slugToIndustryId]);

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
          tryAutoRun(`${idsKey}|${pool}|${benchmark}`);
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

  // ---- Chart option — one line per pair on the selected metric/window
  //      (builder + tooltip in corrOffsetChartOption.ts) --------------------
  const option = useMemo(
    () => (data ? buildCorrOffsetChartOption(data, themeMode, metric, win) : null),
    [data, themeMode, metric, win],
  );

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

  // ---- AI Ask — raw EChart under the page caption: the "?" rides the
  // metric/window controls row. State carries the CURRENT metric / window /
  // pool / benchmark so the modal and the LLM describe the view on screen.
  const chartRef = useRef<ECharts | null>(null);
  const aiAskSpec = useMemo<AiAskSpec>(
    () => ({
      intro:
        "Benchmark-offset industry correlations: each selected industry's MA trend has " +
        "the broad-market benchmark rebased to its level at each window start and " +
        "SUBTRACTED (the common market factor removed; prices recomputed from 100), " +
        "then every industry PAIR's Pearson correlation is charted per rolling " +
        "20/60/255-trading-day window as horizontal segments starting at each window " +
        "date. The metric toggle switches what a segment encodes: Overall = raw " +
        "correlation (benchmark still in), Offset = correlation after the removal, " +
        "Opposite = the score (1 − offset)/2 in [0, 1] — 1 = perfectly opposite once " +
        "the market factor is removed, 0.5 = uncorrelated, 0 = co-moving.",
      instruments: [
        { code: benchmark, assetClass: "index" },
        ...selectedIds.map((id) => ({ code: id, assetClass: "industry" as const })),
      ],
      state: {
        metric: METRIC_LABELS[metric],
        window: win,
        pool,
        benchmark,
        industries_selected: selectedIds.length,
        pairs: numPairs,
      },
      notes: [
        "One line per industry pair (dynamic series names); windows start every 20 trading days.",
        "An industry up while the benchmark rises MORE is DOWN after the offset — that is the opposite-industry detector.",
      ],
    }),
    [metric, win, pool, benchmark, selectedIds, numPairs],
  );
  const aiAskPlotInfo = useMemo(
    () =>
      option
        ? derivePlotInfo({
            title: "Opposite Industry Correlations",
            subtitle: `${METRIC_LABELS[metric]} · ${win} windows · benchmark ${benchmark}`,
            option,
            spec: aiAskSpec,
          })
        : null,
    [option, aiAskSpec, metric, win, benchmark],
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
        trading days. Pick industries with the classification nav below — tick multiple
        industry chips (across sectors, they persist while you browse) and optionally a
        strategy/theme; both columns merge into the same pair set.
      </Typography>

      {/* ---- Classification nav (multi-select) — the same shared kit as
           Industry Sentiments: tick multiple industry chips across sectors
           (switching the active sector only changes the browsing context;
           picked industries persist), merged non-exclusively with the
           RIGHT strategy→theme column. The per-industry L3 index row is
           omitted — pair correlations are per-industry, not per-code. ---- */}
      <SecClassificationNavMulti
        sectors={nav.sectors}
        sectorId={nav.sectorId}
        onSectorChange={nav.handleSectorChange}
        selectedIndustrySlugs={selectedIndustrySlugs}
        onMultiIndustryChange={setSelectedIndustrySlugs}
        exchange={nav.exchange}
        onExchangeChange={nav.handleExchangeChange}
        strategies={nav.strategies}
        strategyId={nav.strategyId}
        themeSlug={nav.themeSlug}
        onStrategyChange={nav.handleStrategyChange}
        onThemeChange={nav.handleThemeChange}
        loading={nav.loading}
      />

      {/* ---- Controls ---- */}
      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1.5, alignItems: "center", mb: 2 }}>
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
            <AiAskButton plotInfo={aiAskPlotInfo} getInstance={() => chartRef.current} />
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
          {!loading && option && (
            <EChart
              option={option}
              height={380}
              onReady={(c) => {
                chartRef.current = c;
              }}
            />
          )}
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
