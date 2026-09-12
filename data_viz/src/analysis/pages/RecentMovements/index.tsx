/**
 * Recent Movements signal page (default export) — /analysis/signals/recent-movements.
 *
 * Built on the shared analysis nav kit (@/shared/components/sec-nav):
 *   • SecNavShell — header (sec_type toggle ETF/Index/Stock · CodeSearchBar ·
 *     Refresh) + SecClassificationNav (two-level cascade L1 sector → L2
 *     industry + parallel strategy column + exchange filter row + L3
 *     security-level chips), fully wired from useSecNav
 *   • Content — once a security is picked (code search or L3 chip), the
 *     shared CodeTrendChart renders its daily price trend (OHLC + MAs via
 *     the shared StockOhlcChart, per sec_type baseline endpoint), and the
 *     Forecast section beneath it (migrated from the MA-Spread panel's 2nd
 *     plot) shows the code's analysis_forecasts extreme-day bucket table
 *     via ForecastTable — a card panel whose kind toggle group picks the
 *     bucket family (mov_rsi default / mov_std / mov_gap / px_vol /
 *     margin_ratio); clicking the active family again hides the table.
 *     Clicking a forecast ROW fetches the bucket's trigger_dates
 *     (forecast_results per-period DATE[] — under the 2026-09
 *     streak-merge, the merged signals' MID days) plus each signal's
 *     qualifying streak period (streak_starts / streak_ends) and its
 *     trading-day count (streak_days), and marks them on the
 *     CodeTrendChart above: a purple pinpoint circle at each mid date,
 *     a relatively DARK purple band over each signal's streak period,
 *     and a light band over the mid's forward forecast window
 *     (+1/+5/+20/+60 of the clicked horizon; overlapping bands merge
 *     into one uniform shade). Hovering a streak-shaded day reports the
 *     run in the axis tooltip — its streak day count and span. A chip
 *     in the chart header names the bucket and clears the markers;
 *     while the dates query is in flight the chip spins and further
 *     row clicks are ignored (no query-per-click forecast switching).
 *
 *   • Forecast header forecast_id search — typing a forecast_id (copied
 *     from a row, a signal or a log) and pressing Enter resolves it via
 *     the shared-PK registry analysis_forecasts.forecast_identities
 *     (GET /mov-ave-spread/forecast-identity) and jumps the whole panel
 *     to the bucket: sec_type + code (trend chart + table), bucket
 *     family and month, tinting the found row (the caption shows the
 *     bucket's mean streak_signal_days). Industry-pair
 *     (opp_pair_state) ids resolve to a caption only — that family has
 *     no table in this UI.
 *
 * Nav trees sources per sec_type (baseline registries):
 *   ETF   → /api/etf-margin/themes + /api/etf-margin/strategy-themes
 *   Index → /api/index-baseline/themes + /api/index-baseline/strategy-themes
 *   Stock → /api/stock-baseline/themes + /api/stock-baseline/strategy-themes
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Box,
  Card,
  Chip,
  CircularProgress,
  IconButton,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import { ArrowForward, Insights, Search } from "@mui/icons-material";
import { SecNavShell, useSecNav } from "@/shared/components/sec-nav";
import type { SecNavSecType, SecNavThemesSource } from "@/shared/components/sec-nav";
import CodeTrendChart from "@/components/CodeTrendChart";
import { findCodeInStrategyThemes, findCodeInThemes } from "@/components/CodeSearchBar";
import { ForecastTable } from "./ForecastTable";
import { TRIGGER_DATE_COLOR } from "@/theme/chart-palette";
import type {
  ForecastIdentityResponse,
  ForecastKind,
  ForecastPeriod,
  HighLowStreaksForecastRow,
  MarginRatioForecastRow,
  MovGapForecastRow,
  MovPairsEmaForecastRow,
  MovPairsForecastRow,
  MovRsiForecastRow,
  MovStdForecastRow,
  PeForecastRow,
  DividendForecastRow,
  PxVolForecastRow,
  SectorNode,
} from "@shared/types";
import {
  fetchThemes as fetchEtfThemes,
  fetchEtfStrategyThemes,
  fetchForecastIdentity,
  fetchForecastTriggerDates,
  fetchIndexThemes,
  fetchIndexStrategyThemes,
  fetchStockThemes,
  fetchStockStrategyThemes,
  invalidateCacheForPrefix,
} from "@/lib/api-client";

/** Nav trees endpoints per sec_type (baseline classification registries). */
const THEMES_SOURCES: Record<SecNavSecType, SecNavThemesSource> = {
  etf: {
    themes: (exchange) => fetchEtfThemes(exchange),
    strategyThemes: (exchange) => fetchEtfStrategyThemes(exchange),
  },
  index: {
    themes: (exchange) => fetchIndexThemes(exchange),
    strategyThemes: (exchange) => fetchIndexStrategyThemes(exchange),
  },
  stock: {
    themes: (exchange) => fetchStockThemes(exchange),
    strategyThemes: (exchange) => fetchStockStrategyThemes(exchange),
  },
};

/** Cache prefixes invalidated on Refresh, per sec_type. */
const CACHE_PREFIXES: Record<SecNavSecType, string[]> = {
  etf: ["/api/etf-margin/themes", "/api/etf-margin/strategy-themes"],
  index: ["/api/index-baseline/themes", "/api/index-baseline/strategy-themes"],
  stock: ["/api/stock-baseline/themes", "/api/stock-baseline/strategy-themes"],
};

/** One ForecastTable bucket family — toggle label + full-name tooltip. */
const FORECAST_KINDS: { kind: ForecastKind; label: string; tooltip: string }[] = [
  { kind: "mov_rsi", label: "RSI", tooltip: "RSI extreme-percentile buckets (mov_rsi)" },
  { kind: "mov_std", label: "Bollinger", tooltip: "Bollinger breach buckets (mov_std)" },
  { kind: "mov_gap", label: "Gap", tooltip: "N-day price-return extreme-percentile buckets (mov_gap)" },
  { kind: "mov_pairs", label: "MA cross", tooltip: "MA5 vs MA60/120/255 cross buckets (mov_pairs)" },
  { kind: "mov_pairs_ema", label: "EMA cross", tooltip: "EMA6 vs EMA60/120/255 cross buckets (mov_pairs_ema)" },
  { kind: "px_vol", label: "Px×Vol", tooltip: "σ-speed × amount-level-z state cells (px_vol)" },
  { kind: "margin_ratio", label: "Margin", tooltip: "Margin-buy intensity z states (margin_ratio)" },
  { kind: "high_low_streaks", label: "HL streak", tooltip: "MA-Spread High/Low streak mean-mid anchor buckets (high_low_streaks) — every band-break excursion streak audited at its mid day" },
  { kind: "pe", label: "PE", tooltip: "PE z states over the raw pe series (pe_state) — high PE = expensive = bearish/top (lower the better), low PE = cheap = bullish/bottom" },
  { kind: "dividend", label: "Div yld", tooltip: "Dividend-yield z states over the trailing-12m D/P series (dividend_state) — high yield = cheap/well-supported = bullish/bottom (higher the better), low yield = bearish/top" },
];

/** Union of all bucket row shapes (matches ForecastTable's ForecastRow). */
type ForecastRow =
  | MovRsiForecastRow | MovStdForecastRow | MovGapForecastRow
  | MovPairsForecastRow | MovPairsEmaForecastRow | PxVolForecastRow
  | MarginRatioForecastRow | HighLowStreaksForecastRow
  | PeForecastRow | DividendForecastRow;

/** Horizon period → "+n" label (the forward forecast window in trading
 *  days — the chart shades each signal day through this window). */
const PERIOD_PLUS: Record<ForecastPeriod, string> = {
  next: "+1", "5d": "+5", "20d": "+20", "60d": "+60",
};

/** Horizon period → forward-window length in trading rows (feeds the
 *  chart's signal + forecast-window shading width). */
const PERIOD_DAYS: Record<ForecastPeriod, number> = {
  next: 1, "5d": 5, "20d": 20, "60d": 60,
};

/** forecast_id search status (Forecast header). `found` keeps the whole
 *  identity so the jump below can read code / sec_type / stat_month /
 *  kind — and so the row tint + month focus can be re-derived after the
 *  scope changes the jump itself causes. */
type IdSearchState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "found"; identity: ForecastIdentityResponse };

/** Resolved identity → short caption ("→ 000300.SS · mov_rsi · 2026-05").
 *  Industry-pair ids name the dropping industry instead — that family
 *  has no table in this UI. */
function describeIdentity(i: ForecastIdentityResponse): string {
  const m = i.stat_month.slice(0, 7);
  const streak =
    i.streak_signal_days != null ? ` · ${i.streak_signal_days}d` : "";
  return i.kind != null
    ? `→ ${i.code} · ${i.kind} · ${m}${streak}`
    : `→ ${m} · opp_pair ${i.code} (industry pair — no table in this UI)`;
}

/** Stable EMPTY highlight identity — a fresh [] per render would
 *  recompute / re-apply the (empty) chart overlay on every page state
 *  change. */
const NO_HIGHLIGHT: string[] = [];
const NO_SPANS: Array<{ start: string; end: string; days: number | null }> = [];

/** Clicked-row chip label base: the bucket's config in one short string
 *  with the horizon period as "+n" (the day count is appended once the
 *  trigger-dates fetch settles). */
function describeBucket(
  kind: ForecastKind,
  r: ForecastRow,
  period: ForecastPeriod,
): string {
  const m = r.stat_month.slice(0, 7);
  let cfg: string;
  if (kind === "mov_rsi") {
    const x = r as MovRsiForecastRow;
    cfg = `RSI${x.rsi_window} · ${x.side} ${x.pct}%`;
  } else if (kind === "mov_std") {
    const x = r as MovStdForecastRow;
    cfg = `MA${x.ma_window} · ${x.side} ${x.k}σ`;
  } else if (kind === "mov_gap") {
    const x = r as MovGapForecastRow;
    cfg = `Gap${x.gap_window} · ${x.side} ${x.pct}%`;
  } else if (kind === "mov_pairs" || kind === "mov_pairs_ema") {
    const x = r as MovPairsForecastRow;
    cfg = `${kind === "mov_pairs" ? "MA" : "EMA"} cross · ${x.side}`;
  } else if (kind === "px_vol") {
    const x = r as PxVolForecastRow;
    cfg = `${x.px_speed} × ${x.vol_state}`;
  } else if (kind === "high_low_streaks") {
    const x = r as HighLowStreaksForecastRow;
    cfg = `HL${x.band_period} · ${x.side === "top" ? "above" : "below"} ${x.pct_type}%`;
  } else if (kind === "pe") {
    const x = r as PeForecastRow;
    cfg = `PE · ${x.val_state} · ${x.side}`;
  } else if (kind === "dividend") {
    const x = r as DividendForecastRow;
    cfg = `Div · ${x.val_state} · ${x.side}`;
  } else {
    const x = r as MarginRatioForecastRow;
    cfg = `margin · ${x.ratio_state}`;
  }
  return `${m} · ${cfg} · ${PERIOD_PLUS[period]}`;
}

export default function RecentMovementsPage() {
  // Shared nav: sec_type toggle + code search + classification selection.
  // (onInvalidateCache's closure only fires on a later Refresh click, so
  // reading nav.secType inside it is safe.)
  const nav = useSecNav({
    themesSources: THEMES_SOURCES,
    dataLabel: "classification",
    onInvalidateCache: () => {
      for (const prefix of CACHE_PREFIXES[nav.secType]) {
        invalidateCacheForPrefix(prefix);
      }
    },
  });

  // Forecast bucket-family selector (Forecast card beneath the trend chart):
  // "" = hidden, else which analysis_forecasts bucket table ForecastTable
  // shows. Defaults to RSI (mov_rsi) so the section is populated on arrival;
  // clicking the active family again deselects it (exclusive toggle → null).
  const [forecastKind, setForecastKind] = useState<ForecastKind | "">("mov_rsi");

  // Trigger-day highlight from the LAST clicked forecast row: the row's
  // trigger_dates (the exact days its stats were computed over) shaded
  // light purple on the trend chart above — each signal day pinpointed
  // with a purple circle plus its forward forecast window (+1/+5/+20/+60
  // of the clicked horizon) as a full-height band. streaks carries each
  // merged signal's qualifying-run [start, end] + trading-day count —
  // shaded dark purple, and hovering a shaded day reports the run (day
  // count + span) in the axis tooltip. Cleared when the security or
  // bucket family changes; the chip in the chart header clears it
  // manually.
  const [triggerDates, setTriggerDates] = useState<{
    dates: string[];
    streaks: Array<{ start: string; end: string; days: number | null }>;
    base: string;
    period: ForecastPeriod;
  } | null>(null);
  const [selectedForecastId, setSelectedForecastId] = useState<number | null>(null);
  // One trigger-dates query at a time: from the row click until the
  // overlay has RENDERED (fetch settled + the chart applied the
  // shading), the whole forecast table is FROZEN (translucent backdrop
  // + spinner — further clicks physically blocked), so quick forecast
  // switching cannot fire a query per click. The ref mirrors the state
  // so the click handler identity stays stable across freeze flips.
  const [triggerLoading, setTriggerLoading] = useState(false);
  const [pendingBase, setPendingBase] = useState<string | null>(null);
  const triggerSeq = useRef(0);
  const triggerLoadingRef = useRef(false);

  // ---- Search by forecast_id (shared-PK registry jump) -----------------
  // Typing a forecast_id in the Forecast header resolves it against
  // analysis_forecasts.forecast_identities and jumps the whole panel to
  // the bucket's security + family + month. idFocus is deliberately NOT
  // cleared by the scope-change effect above — the jump itself changes
  // the scope; the derived row tint / month focus below drop out as
  // soon as the panel no longer matches the identity.
  const [idInput, setIdInput] = useState("");
  const [idSearch, setIdSearch] = useState<IdSearchState>({ status: "idle" });
  const [idFocus, setIdFocus] = useState<ForecastIdentityResponse | null>(null);
  // Cross-sec_type jumps land AFTER the nav's NEW classification trees
  // have arrived: setSecType flips one commit BEFORE the trees effect
  // swaps nav.sectors, and in that gap the trees (and loading flag) are
  // still the OLD sec_type's. The jump therefore waits until the
  // sectors ARRAY REFERENCE has been replaced since the jump was
  // queued — reliable even when the trees resolve from the client-side
  // cache (the loading flag may never render true in that case).
  const [pendingJump, setPendingJump] = useState<{
    secType: SecNavSecType;
    code: string;
    sectorsAtJump: SectorNode[];
  } | null>(null);

  /** Apply a jumped-to code: prefer the tree-resolving search (it also
   *  sets the sector/industry highlights); codes outside the
   *  classification trees (e.g. minor indices with forecasts but no
   *  nav entry) still jump via the direct setter — the trend chart and
   *  forecast table key on the code alone. */
  const applyCodeJump = useCallback(
    (code: string) => {
      if (findCodeInThemes(nav.sectors, code)
          || findCodeInStrategyThemes(nav.strategies, code)) {
        nav.handleSearch(code);
      } else {
        nav.handleCodeJump(code);
      }
    },
    [nav.sectors, nav.strategies, nav.handleSearch, nav.handleCodeJump],
  );

  useEffect(() => {
    if (!pendingJump || nav.secType !== pendingJump.secType) return;
    if (nav.sectors === pendingJump.sectorsAtJump || nav.loading) return;
    setPendingJump(null);
    applyCodeJump(pendingJump.code);
  }, [pendingJump, nav.loading, nav.secType, nav.sectors, applyCodeJump]);

  /** Enter in the forecast-id field → resolve via the identities registry
   *  and jump the panel (family + security + month; found row tinted). */
  const handleIdSearch = () => {
    const raw = idInput.trim();
    const id = Number(raw);
    if (!raw || !Number.isInteger(id) || id <= 0) {
      setIdSearch({ status: "error", message: `"${raw}" is not a forecast_id (positive integer)` });
      return;
    }
    setIdSearch({ status: "loading" });
    fetchForecastIdentity(id)
      .then((identity) => {
        setIdSearch({ status: "found", identity });
        setIdFocus(identity);
        // Viewable families only (opp_pair ids — industry pairs — have no
        // table in this UI; the caption alone reports them, and their
        // code is an industry id the trend chart could not render).
        if (identity.kind != null) {
          setForecastKind(identity.kind);
          if (identity.sec_type === "etf" || identity.sec_type === "index" || identity.sec_type === "stock") {
            if (identity.sec_type !== nav.secType) {
              nav.setSecType(identity.sec_type as SecNavSecType);
              setPendingJump({
                secType: identity.sec_type as SecNavSecType,
                code: identity.code,
                sectorsAtJump: nav.sectors,
              });
            } else {
              applyCodeJump(identity.code);
            }
          }
        }
      })
      .catch((e: Error) => setIdSearch({ status: "error", message: e.message }));
  };

  useEffect(() => {
    // Scope change — drop the highlight and invalidate any in-flight
    // fetch (its .then must not settle into the new scope).
    triggerSeq.current += 1;
    triggerLoadingRef.current = false;
    setTriggerDates(null);
    setSelectedForecastId(null);
    setTriggerLoading(false);
    setPendingBase(null);
  }, [nav.searchCode, nav.secType, forecastKind]);

  /** Forecast row click → fetch the bucket's trigger_dates and shade
   *  them on the trend chart (the clicked row stays tinted in the
   *  table). Identity-stable (memoized ForecastTable consumer) — the
   *  freeze guard lives in the ref, not the closure. */
  const handleForecastRowClick = useCallback(
    (row: ForecastRow, period: ForecastPeriod) => {
      if (triggerLoadingRef.current) return;
      triggerLoadingRef.current = true;
      const seq = ++triggerSeq.current;
      const base = describeBucket(forecastKind || "mov_rsi", row, period);
      setSelectedForecastId(row.forecast_id);
      setPendingBase(base);
      setTriggerLoading(true);
      fetchForecastTriggerDates(row.forecast_id)
        .then((d) => {
          if (seq !== triggerSeq.current) return;
          const dates = d.periods[period];
          setTriggerDates({
            dates: dates ?? [],
            streaks: d.streaks[period] ?? [],
            base,
            period,
          });
          setPendingBase(null);
        })
        .catch(() => {
          if (seq !== triggerSeq.current) return;
          setTriggerDates({ dates: [], streaks: [], base, period });
          setPendingBase(null);
        });
      // NOTE: the freeze is RELEASED by handleHighlightSettled, once the
      // chart has applied the overlay — not by the fetch alone.
    },
    [forecastKind],
  );

  /** Chart-side "overlay rendered" gate — unfreezes the table. Stable
   *  identity so StockOhlcChart's overlay effect deps never churn. */
  const handleHighlightSettled = useCallback(() => {
    triggerLoadingRef.current = false;
    setTriggerLoading(false);
  }, []);

  // The forecast-id search focus only drives the table while the panel
  // actually shows the identity's scope (code / sec_type / family) —
  // after the jump lands, or when the id already belonged to this view.
  const idFocusActive =
    idFocus != null &&
    idFocus.kind === forecastKind &&
    idFocus.code === nav.searchCode &&
    idFocus.sec_type === nav.secType;

  /** Clear the highlight (chip ×) — also cancels an in-flight fetch. */
  const clearTriggerDates = () => {
    triggerSeq.current += 1;
    triggerLoadingRef.current = false;
    setTriggerDates(null);
    setSelectedForecastId(null);
    setTriggerLoading(false);
    setPendingBase(null);
  };

  // Identity-stable chartOptions: a fresh object per render would
  // re-render (and re-run effect bookkeeping in) the chart subtree on
  // every page state change.
  const triggerChartOptions = useMemo(
    () => ({
      highlightDates: triggerDates?.dates ?? NO_HIGHLIGHT,
      // Dark-purple shade over each merged signal's qualifying streak
      // period (the run behind the mid date).
      highlightSpans: triggerDates?.streaks ?? NO_SPANS,
      // Light shade over each mid day's forward forecast window
      // (+1/+5/+20/+60 of the clicked horizon).
      highlightHorizonDays: triggerDates ? PERIOD_DAYS[triggerDates.period] : 1,
      onHighlightSettled: handleHighlightSettled,
    }),
    [triggerDates, handleHighlightSettled],
  );

  return (
    <SecNavShell
      nav={nav}
      title="Recent Movements"
      backPath="/analysis/signals"
      backLabel="back to signals"
      subtitle="Per-security recent price-movement signals — pick a security via
            the classification nav or code search to see its price trend."
      secTypes={["etf", "index", "stock"]}
      refreshTooltip="Refresh the classification trees (bypass cache)"
      errorPrefix="classification data"
    >
      {nav.searchCode ? (
        <>
          <CodeTrendChart
            secType={nav.secType}
            code={nav.searchCode}
            name={nav.findItemName(nav.searchCode)}
            chartOptions={triggerChartOptions}
            headerAction={
              triggerDates || triggerLoading ? (
                <Chip
                  size="small"
                  icon={
                    triggerLoading ? (
                      <CircularProgress size={11} thickness={5} sx={{ color: TRIGGER_DATE_COLOR, ml: "6px" }} />
                    ) : undefined
                  }
                  label={`⦿ trigger days · ${triggerDates?.base ?? pendingBase} · ${
                    triggerLoading ? "loading…" : triggerDates && triggerDates.dates.length > 0
                      ? `${triggerDates.dates.length} days`
                      : "no dates"
                  }`}
                  onDelete={clearTriggerDates}
                  sx={{
                    maxWidth: 400,
                    height: 24,
                    fontSize: "0.65rem",
                    color: TRIGGER_DATE_COLOR,
                    borderColor: TRIGGER_DATE_COLOR,
                    bgcolor: "rgba(186, 104, 200, 0.08)",
                    "& .MuiChip-deleteIcon": { color: TRIGGER_DATE_COLOR },
                    "& .MuiChip-label": { whiteSpace: "normal" },
                    ...(triggerLoading ? { opacity: 0.85 } : {}),
                  }}
                />
              ) : undefined
            }
          />

          {/* ---- 2nd plot: forecast bucket table (analysis_forecasts) ----
              Card panel beneath the trend chart: header row (icon + kind
              toggle group) + the ForecastTable body. The exclusive toggle
              picks which bucket family to show — RSI extreme-percentile
              buckets (mov_rsi), Bollinger breach buckets (mov_std), N-day
              price-return extreme-percentile buckets (mov_gap), MA5-vs-MA
              cross buckets (mov_pairs), EMA6-vs-EMA cross buckets
              (mov_pairs_ema), σ-speed ×
              量比-z state cells (px_vol) or margin-buy intensity z states
              (margin_ratio); clicking the active one again hides the table.
              Selecting one mounts ForecastTable, which lists ALL stat_months
              of this code's buckets (config + is_market_hyped [+ excess/
              mean-t-z cols] → forecast results). */}
          <Card variant="outlined" sx={{ mt: 1.5 }}>
            <Stack
              direction="row"
              alignItems="center"
              spacing={1.25}
              flexWrap="wrap"
              rowGap={0.5}
              sx={{
                px: 1.25,
                py: 0.75,
                borderBottom: forecastKind ? 1 : 0,
                borderColor: "divider",
                bgcolor: "action.hover",
                borderTopLeftRadius: "inherit",
                borderTopRightRadius: "inherit",
              }}
            >
              <Stack direction="row" alignItems="center" spacing={0.5}>
                <Insights sx={{ fontSize: "1rem", color: "primary.main" }} />
                <Typography sx={{ fontSize: "0.78rem", fontWeight: 700 }}>
                  Forecast
                </Typography>
              </Stack>
              <ToggleButtonGroup
                size="small"
                exclusive
                value={forecastKind}
                onChange={(_, v) => setForecastKind((v ?? "") as ForecastKind | "")}
              >
                {FORECAST_KINDS.map((k) => (
                  <Tooltip key={k.kind} title={k.tooltip} arrow>
                    <ToggleButton
                      value={k.kind}
                      sx={{
                        px: 1.25,
                        py: 0.15,
                        fontSize: "0.68rem",
                        lineHeight: 1.4,
                        textTransform: "none",
                      }}
                    >
                      {k.label}
                    </ToggleButton>
                  </Tooltip>
                ))}
              </ToggleButtonGroup>
              {/* Search by forecast_id — resolves against the shared-PK
                  registry (analysis_forecasts.forecast_identities) and
                  jumps the whole panel to the bucket. Enter submits; the
                  trailing arrow button does the same on click. */}
              <TextField
                size="small"
                placeholder="forecast id"
                value={idInput}
                onChange={(e) => setIdInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleIdSearch();
                }}
                sx={{ width: 176, "& .MuiInputBase-root": { fontSize: "0.7rem", height: 26 } }}
                InputProps={{
                  startAdornment: (
                    <Search sx={{ fontSize: 14, mr: 0.5, color: "text.disabled" }} />
                  ),
                  endAdornment: (
                    <Tooltip title="Resolve this forecast_id and jump to its bucket" arrow>
                      <IconButton
                        size="small"
                        aria-label="search forecast id"
                        onClick={handleIdSearch}
                        sx={{ p: 0.25, mr: -0.25 }}
                      >
                        <ArrowForward sx={{ fontSize: 14 }} />
                      </IconButton>
                    </Tooltip>
                  ),
                }}
              />
              <Box sx={{ flexGrow: 1 }} />
              <Typography
                variant="caption"
                color={idSearch.status === "error" ? "error" : "text.secondary"}
                sx={{ fontSize: "0.62rem" }}
              >
                {idSearch.status === "loading"
                  ? "resolving forecast_id…"
                  : idSearch.status === "error"
                    ? idSearch.message
                    : idSearch.status === "found"
                      ? describeIdentity(idSearch.identity)
                      : forecastKind
                        ? "header dropdowns filter buckets (month header = end-month selector, only that month's rows) · click a row to mark its trigger days on the trend chart above · forecast id ⏎ jumps to its bucket"
                        : "pick a bucket family to show its extreme-day forecast table"}
              </Typography>
            </Stack>
            {forecastKind && (
              <Box sx={{ px: 1, py: 0.75 }}>
                <ForecastTable
                  code={nav.searchCode}
                  secType={nav.secType}
                  kind={forecastKind}
                  onRowClick={handleForecastRowClick}
                  selectedRowKey={
                    selectedForecastId != null
                      ? String(selectedForecastId)
                      : idFocusActive && idFocus != null
                        ? String(idFocus.forecast_id)
                        : null
                  }
                  focusStatMonth={idFocusActive && idFocus != null ? idFocus.stat_month : null}
                  frozen={triggerLoading}
                />
              </Box>
            )}
          </Card>
        </>
      ) : (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <Typography variant="body2" color="text.secondary">
            Pick a security via the classification nav or code search to see its
            price trend.
          </Typography>
        </Box>
      )}
    </SecNavShell>
  );
}
