/**
 * News Audit page (Composites) — the 5-day provider/content audits of the
 * news corpus (text.news_provider_audit + registry / text.news_content_audit,
 * built by `python -m analysis.news_audit`).
 *
 * The header's VIEW TOGGLE switches between the two audits (the DbAudit
 * page's toggle pattern: the mode rides the fetch deps, a spinner runs
 * while in flight and stale rows stay visible); the WINDOWS selector picks
 * how many recent 5-day windows to load; the refresh button POSTs
 * /news-audit/run (incremental pipeline run through the WSL py-runner) and
 * refetches on success.
 *
 * The TOOLBAR FILTER ROW narrows the loaded rows CLIENT-SIDE before they
 * reach the tables: a TimeSlider over the distinct window_start dates
 * (both views) plus Provider/Author dropdowns (providers view — both
 * vocabularies run long, so both are searchable Autocompletes). The
 * selects scope each
 * other's options (the NewsPage chips pattern: picking a provider narrows
 * the author options); a stale selection that vanished from its options
 * falls back to "(all)". Filter changes also ride the tables'
 * filterScopeDeps so the per-column header filters reset instead of
 * dead-ending against the narrowed row set. Pagination (20/page) lives in
 * the shared ExpandedTable, slicing the post-header-filter rows.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  IconButton,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import { Refresh } from "@mui/icons-material";
import { useChartData } from "@/shared/charts/base-chart";
import { useDocs } from "@/shared/docs";
import { fmtDate } from "@/shared/datetime";
import TimeSlider from "@/shared/components/time-slider/TimeSlider";
import {
  fetchNewsContentAudit,
  fetchNewsCoverageAudit,
  fetchNewsProviderAudit,
  runNewsAuditRefresh,
} from "@/lib/api-client";
import type {
  NewsProviderAuditResponse,
  NewsContentAuditResponse,
  NewsCoverageAuditResponse,
  NewsProviderAuditRow,
  NewsContentAuditRow,
  NewsCoverageAuditRow,
} from "@shared/types";
import ProviderAuditTable from "./ProviderAuditTable";
import ContentAuditTable from "./ContentAuditTable";
import CoverageAuditTable from "./CoverageAuditTable";
import AuditFilterSelect, {
  type AuditFilterOption,
} from "./AuditFilterSelect";
import { authorFilterLabel } from "./format";

/** The page's two halves — which audit table feeds the ExpandedTable. */
type AuditView = "providers" | "content" | "coverage";

const VIEWS: AuditView[] = ["providers", "content", "coverage"];

const VIEW_LABELS: Record<AuditView, string> = {
  providers: "Provider audit",
  content: "Content audit",
  coverage: "Coverage audit (daily)",
};

/** Recent-window counts (5-day windows ≈ 40d / 130d / 260d). */
const WINDOW_CHOICES = [8, 26, 52];

/** Rows whose date falls inside the slider's [lo, hi] date span — tiny
 * pure helper shared by the three views' filters (providers/content key
 * on window_start, the coverage view on its trading date). */
function rowsInWindow<T>(
  rows: T[],
  dates: string[],
  range: [number, number],
  getDate: (r: T) => string,
): T[] {
  if (dates.length === 0) return [];
  const lo = dates[range[0]] ?? dates[0];
  const hi = dates[range[1]] ?? dates[dates.length - 1];
  return rows.filter((r) => getDate(r) >= lo && getDate(r) <= hi);
}

export default function NewsAuditPage() {
  const docs = useDocs();
  const [view, setView] = useState<AuditView>("providers");
  const [limitWindows, setLimitWindows] = useState<number>(8);
  const [runBusy, setRunBusy] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  // Toolbar filters — slider indexes into the ascending window_start list;
  // provider is "" = all, author is null = all (its "" value IS the
  // provider-level sentinel bucket, a selectable filter). All CLIENT-SIDE
  // over the loaded rows.
  const [windowRange, setWindowRange] = useState<[number, number]>([0, 0]);
  const [providerFilter, setProviderFilter] = useState("");
  const [authorFilter, setAuthorFilter] = useState<string | null>(null);

  // One fetch through the shared hook — the view toggle is a dep (the
  // DbAudit page's mode-toggle pattern); stale rows stay visible while
  // the new mode's rows land.
  const { data, error, loading, refresh } = useChartData<
    NewsProviderAuditResponse | NewsContentAuditResponse | NewsCoverageAuditResponse
  >(
    () =>
      view === "providers"
        ? fetchNewsProviderAudit(limitWindows)
        : view === "content"
          ? fetchNewsContentAudit(limitWindows)
          : // the coverage view reads DAYS: 8/26/52 windows ≈ 40/130/260 days
            fetchNewsCoverageAudit(limitWindows * 5),
    [view, limitWindows],
  );

  // Discriminated by the response shape ("source" only exists on the
  // provider response, "n_articles" only on the coverage one) — sound
  // because the fetcher switches on the same `view` dep.
  // Discriminated by the response's own echo fields (the provider
  // response carries `source`, the content one `industry_id`, the
  // coverage one `limit_days`) — sound because the fetcher switches on
  // the same `view` dep.
  const providerRows = useMemo(
    () => (data != null && "source" in data ? (data.rows as NewsProviderAuditRow[]) : []),
    [data],
  );
  const contentRows = useMemo(
    () => (data != null && "industry_id" in data ? (data.rows as NewsContentAuditRow[]) : []),
    [data],
  );
  const coverageRows = useMemo(
    () => (data != null && "limit_days" in data ? (data.rows as NewsCoverageAuditRow[]) : []),
    [data],
  );
  // the active view's date getter (providers/content: window_start;
  // coverage: the trading date)
  const rowDate = useMemo<
    (r: NewsProviderAuditRow | NewsContentAuditRow | NewsCoverageAuditRow) => string
  >(
    () => (view === "coverage"
      ? (r) => (r as NewsCoverageAuditRow).date
      : (r) => (r as NewsProviderAuditRow | NewsContentAuditRow).window_start),
    [view],
  );
  const viewRows = useMemo(
    () =>
      view === "providers" ? providerRows
        : view === "content" ? contentRows
          : coverageRows,
    [view, providerRows, contentRows, coverageRows],
  );
  const latestWindow = useMemo(
    () => (viewRows.length > 0 ? rowDate(viewRows[0]) : null),
    [viewRows, rowDate],
  );

  // The slider's date grid — distinct ASCENDING dates over the active
  // view's rows. A fresh grid (view / windows selector / refresh)
  // re-seeds the slider at the full span, and the selects clear on a view
  // switch (they only exist in the providers view).
  const windowDates = useMemo(
    () => [...new Set(viewRows.map(rowDate))].sort(),
    [viewRows, rowDate],
  );
  const maxIdx = Math.max(windowDates.length - 1, 0);
  // Clamp against the CURRENT grid so the one render between a grid change
  // and the re-seed effect never hands the slider out-of-range indexes.
  // Memoized — a fresh array each render would recompute every rowsInWindow
  // memo below on unrelated state flips.
  const range: [number, number] = useMemo(
    () => [Math.min(windowRange[0], maxIdx), Math.min(windowRange[1], maxIdx)],
    [windowRange, maxIdx],
  );
  useEffect(() => {
    setWindowRange([0, maxIdx]);
  }, [windowDates, maxIdx]);
  useEffect(() => {
    setProviderFilter("");
    setAuthorFilter(null);
  }, [view]);

  // Pipeline: slider window → provider select → author select. Options
  // derive from the row set ABOVE their own filter (the NewsPage chips
  // pattern — picking a provider narrows the author options, not its own).
  const windowedProviderRows = useMemo(
    () => rowsInWindow(providerRows, windowDates, range, (r) => r.window_start),
    [providerRows, windowDates, range],
  );
  const windowedContentRows = useMemo(
    () => rowsInWindow(contentRows, windowDates, range, (r) => r.window_start),
    [contentRows, windowDates, range],
  );
  const windowedCoverageRows = useMemo(
    () => rowsInWindow(coverageRows, windowDates, range, (r) => r.date),
    [coverageRows, windowDates, range],
  );
  const providerOptions: AuditFilterOption[] = useMemo(() => {
    const counts = new Map<string, number>();
    for (const r of windowedProviderRows) {
      counts.set(r.source, (counts.get(r.source) ?? 0) + 1);
    }
    return [...counts.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([value, count]) => ({ value, label: value, count }));
  }, [windowedProviderRows]);
  const providerFilteredRows = useMemo(
    () =>
      providerFilter === ""
        ? windowedProviderRows
        : windowedProviderRows.filter((r) => r.source === providerFilter),
    [windowedProviderRows, providerFilter],
  );
  const authorOptions: AuditFilterOption[] = useMemo(() => {
    const counts = new Map<string, number>();
    for (const r of providerFilteredRows) {
      counts.set(r.author, (counts.get(r.author) ?? 0) + 1);
    }
    return [...counts.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([value, count]) => ({ value, label: authorFilterLabel(value), count }));
  }, [providerFilteredRows]);
  // Stale-value fallback (the ConfigServiceTable pattern): a selection that
  // vanished from its own options (narrowed away) reads as "(all)". The
  // author's null = all also covers ""-sentinel absence.
  const effectiveProvider = providerOptions.some((o) => o.value === providerFilter)
    ? providerFilter
    : "";
  const effectiveAuthor: string | null = authorOptions.some((o) => o.value === authorFilter)
    ? authorFilter
    : null;
  const viewProviderRows = useMemo(
    () =>
      effectiveAuthor == null
        ? providerFilteredRows
        : providerFilteredRows.filter((r) => r.author === effectiveAuthor),
    [providerFilteredRows, effectiveAuthor],
  );

  // The active slider span as a scope dep — header-filter tick selections
  // reset when the toolbar narrows the row set.
  const windowKey =
    windowDates.length > 0
      ? `${windowDates[range[0]] ?? windowDates[0]}..${windowDates[range[1]] ?? windowDates[windowDates.length - 1]}`
      : "";
  const tableScopeDeps =
    view === "providers" ? [effectiveProvider, effectiveAuthor, windowKey] : [windowKey];
  const visibleCount = (
    view === "providers" ? viewProviderRows
      : view === "content" ? windowedContentRows
        : windowedCoverageRows
  ).length;
  const totalCount = viewRows.length;

  const onRefreshRun = async () => {
    if (runBusy) return;
    setRunBusy(true);
    setRunError(null);
    const r = await runNewsAuditRefresh();
    setRunBusy(false);
    if (r.success || r.already_running) {
      refresh();
    } else {
      setRunError(r.stderr_tail || "news_audit run failed");
    }
  };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2, p: 2 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 2, flexWrap: "wrap" }}>
        <Typography variant="h6">News Audit (新闻审计)</Typography>
        <Typography variant="caption" color="text.secondary">
          5-day windows · stride 5d{latestWindow ? ` · latest ${fmtDate(latestWindow)}` : ""}
        </Typography>
        {/* Refetch feedback for the header controls (view / windows /
            refresh): the spinner runs on every in-flight load or pipeline
            run, stale content below stays visible. */}
        {(loading || runBusy) && (
          <CircularProgress size={16} aria-label="loading news audit" />
        )}
        <Box sx={{ flexGrow: 1 }} />
        <ToggleButtonGroup
          size="small"
          exclusive
          value={view}
          onChange={(_, v: AuditView | null) => {
            if (v != null) setView(v);
          }}
        >
          {VIEWS.map((v) => (
            <ToggleButton key={v} value={v}>
              {VIEW_LABELS[v]}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
        <ToggleButtonGroup
          size="small"
          exclusive
          value={limitWindows}
          onChange={(_, v: number | null) => {
            if (v != null) setLimitWindows(v);
          }}
        >
          {WINDOW_CHOICES.map((w) => (
            <ToggleButton key={w} value={w}>
              {w}w
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
        <Tooltip title="Run python -m analysis.news_audit (incremental) and refetch">
          <span>
            <IconButton
              size="small"
              aria-label="refresh news audit"
              onClick={onRefreshRun}
              disabled={runBusy}
            >
              <Refresh fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
      </Box>

      <Typography variant="body2" color="text.secondary" sx={{ maxWidth: 1100 }}>
        {docs.text("ui.composites.news-audit.page")}
      </Typography>

      {error != null && (
        <Alert severity="error">Failed to load the news audit: {error}</Alert>
      )}
      {runError != null && (
        <Alert severity="warning" onClose={() => setRunError(null)}>
          news_audit run failed: {runError}
        </Alert>
      )}

      <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
        <Chip
          variant="outlined"
          label={`${visibleCount.toLocaleString()} / ${totalCount.toLocaleString()} rows · ${limitWindows} windows`}
        />
        {view === "providers" ? (
          <Chip variant="outlined" label="author “· provider-level ·” = the provider's own rows" />
        ) : view === "content" ? (
          <Chip variant="outlined" label="industry “· untagged ·” = broad-market / gov items" />
        ) : (
          <Chip variant="outlined" label="(whole market) = the '000001' rollup row — the daily panel's flow basis" />
        )}
      </Box>

      {/* Toolbar filters — client-side over the loaded rows; the selects
          only exist in the providers view (the content view's grain is
          industry, already covered by its header ticks filter). */}
      <Box sx={{ display: "flex", alignItems: "center", gap: 2, flexWrap: "wrap" }}>
        <TimeSlider
          dates={windowDates}
          value={range}
          onChange={setWindowRange}
          sx={{ flexGrow: 1, minWidth: 320, maxWidth: 900 }}
        />
        {view === "providers" && (
          <>
            <AuditFilterSelect
              label="Provider"
              options={providerOptions}
              value={effectiveProvider}
              onChange={setProviderFilter}
            />
            <AuditFilterSelect
              label="Author"
              options={authorOptions}
              value={effectiveAuthor}
              onChange={setAuthorFilter}
            />
          </>
        )}
      </Box>

      {view === "providers" ? (
        <ProviderAuditTable
          rows={viewProviderRows}
          enableFilters={data?.enable_filters === true}
          filterScopeDeps={tableScopeDeps}
        />
      ) : view === "content" ? (
        <ContentAuditTable
          rows={windowedContentRows}
          enableFilters={data?.enable_filters === true}
          filterScopeDeps={tableScopeDeps}
        />
      ) : (
        <CoverageAuditTable
          rows={windowedCoverageRows}
          enableFilters={data?.enable_filters === true}
          filterScopeDeps={tableScopeDeps}
        />
      )}
    </Box>
  );
}
