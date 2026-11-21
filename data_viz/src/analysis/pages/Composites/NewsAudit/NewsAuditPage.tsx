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
import TimeSlider from "@/shared/components/time-slider/TimeSlider";
import {
  fetchNewsContentAudit,
  fetchNewsProviderAudit,
  runNewsAuditRefresh,
} from "@/lib/api-client";
import type {
  NewsProviderAuditResponse,
  NewsContentAuditResponse,
} from "@shared/types";
import ProviderAuditTable from "./ProviderAuditTable";
import ContentAuditTable from "./ContentAuditTable";
import AuditFilterSelect, {
  type AuditFilterOption,
} from "./AuditFilterSelect";
import { authorFilterLabel } from "./format";

/** The page's two halves — which audit table feeds the ExpandedTable. */
type AuditView = "providers" | "content";

const VIEWS: AuditView[] = ["providers", "content"];

const VIEW_LABELS: Record<AuditView, string> = {
  providers: "Provider audit",
  content: "Content audit",
};

/** Recent-window counts (5-day windows ≈ 40d / 130d / 260d). */
const WINDOW_CHOICES = [8, 26, 52];

/** Rows whose window_start falls inside the slider's [lo, hi] date span —
 * tiny pure helper shared by both views' window filters. */
function rowsInWindow<T extends { window_start: string }>(
  rows: T[],
  dates: string[],
  range: [number, number],
): T[] {
  if (dates.length === 0) return [];
  const lo = dates[range[0]] ?? dates[0];
  const hi = dates[range[1]] ?? dates[dates.length - 1];
  return rows.filter((r) => r.window_start >= lo && r.window_start <= hi);
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
    NewsProviderAuditResponse | NewsContentAuditResponse
  >(
    () =>
      view === "providers"
        ? fetchNewsProviderAudit(limitWindows)
        : fetchNewsContentAudit(limitWindows),
    [view, limitWindows],
  );

  // Discriminated by the response shape ("source" only exists on the
  // provider response) — sound because the fetcher switches on the same
  // `view` dep.
  const providerRows = useMemo(
    () => (data != null && "source" in data ? data.rows : []),
    [data],
  );
  const contentRows = useMemo(
    () => (data != null && "industry_id" in data ? data.rows : []),
    [data],
  );
  const latestWindow = useMemo(() => {
    const rows =
      view === "providers" ? providerRows : contentRows;
    return rows.length > 0 ? rows[0].window_start : null;
  }, [view, providerRows, contentRows]);

  // The slider's date grid — distinct ASCENDING window starts over the
  // active view's rows. A fresh grid (view / windows selector / refresh)
  // re-seeds the slider at the full span, and the selects clear on a view
  // switch (they only exist in the providers view).
  const windowDates = useMemo(
    () =>
      [...new Set((view === "providers" ? providerRows : contentRows).map((r) => r.window_start))].sort(),
    [view, providerRows, contentRows],
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
    () => rowsInWindow(providerRows, windowDates, range),
    [providerRows, windowDates, range],
  );
  const windowedContentRows = useMemo(
    () => rowsInWindow(contentRows, windowDates, range),
    [contentRows, windowDates, range],
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
    view === "providers" ? viewProviderRows : windowedContentRows
  ).length;
  const totalCount = (view === "providers" ? providerRows : contentRows).length;

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
          5-day windows · stride 5d{latestWindow ? ` · latest ${latestWindow}` : ""}
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
        ) : (
          <Chip variant="outlined" label="industry “· untagged ·” = broad-market / gov items" />
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
      ) : (
        <ContentAuditTable
          rows={windowedContentRows}
          enableFilters={data?.enable_filters === true}
          filterScopeDeps={tableScopeDeps}
        />
      )}
    </Box>
  );
}
