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
 *     bucket family (mov_rsi default / mov_std /
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
 *     into one uniform shade). When the clicked bucket registered as a
 *     signal strategy (the table's signal ✓ chip), each mid day is ALSO
 *     labeled with the code trend's trade-signal buy/sell triangle — a
 *     green up-triangle below the day's low for buy, a red
 *     down-triangle above the day's high for sell, driven by the
 *     bucket's own signal_action — and the axis tooltip reports the
 *     action with its expected move. Hovering a streak-shaded day
 *     reports the run in the axis tooltip — its streak day count and
 *     span. A chip in the chart header names the bucket (plus the
 *     buy/sell action) and clears the markers;
 *     while the dates query is in flight the chip spins and further
 *     row clicks are ignored (no query-per-click forecast switching).
 *
 *   • Forecast header forecast_id search — typing a forecast_id (copied
 *     from a row, a signal or a log) and pressing Enter resolves it via
 *     the shared-PK registry analysis_forecasts.forecast_identities
 *     (GET /mov-ave-spread/forecast-identity) and jumps the whole panel
 *     to the bucket: sec_type + code (trend chart + table), bucket
 *     family and snapshot year, tinting the found row (the caption shows
 *     bucket's mean streak_signal_days). Ids of retired families without
 *     a table here resolve to a caption only.
 *
 *   • Forecast header AI Ask — a shared-kit "?" beside the Forecast title
 *     (AiAskButton + derivePlotInfo) whose payload is MAPPED FROM THE
 *     TOGGLES: the active family toggle supplies the state tag + the intro
 *     reading guide (its tooltip) + the online-search seed keyword (the
 *     same aiSearchKeywords the trend chart's "?" gets), and ForecastTable's
 *     horizon toggle — controlled from here so the mirror cannot desync
 *     when the table remounts — supplies the horizon state tag; an active
 *     row highlight adds the trigger-days tag.
 *
 * Nav trees sources per sec_type (baseline registries):
 *   ETF   → /api/etf-margin/themes + /api/etf-margin/strategy-themes
 *   Index → /api/index-baseline/themes + /api/index-baseline/strategy-themes
 *   Stock → /api/stock-baseline/themes + /api/stock-baseline/strategy-themes
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ECharts } from "echarts";
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
import type { OhlcForecastAction } from "@/components/StockOhlcChart";
import { findCodeInStrategyThemes, findCodeInThemes } from "@/components/CodeSearchBar";
import { AiAskButton, derivePlotInfo } from "@/shared/ai-ask";
import { ForecastTable } from "./ForecastTable";
import { DOWN_COLOR, TRIGGER_DATE_COLOR, UP_COLOR } from "@/theme/chart-palette";
import type {
  ForecastIdentityResponse,
  ForecastKind,
  ForecastPeriod,
  HighLowStreaksForecastRow,
  MarginRatioForecastRow,
  MovPairsEmaForecastRow,
  MovPairsForecastRow,
  MovRsiForecastRow,
  MovStdForecastRow,
  PeForecastRow,
  DividendForecastRow,
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

/** One ForecastTable bucket family — toggle label + full-name tooltip +
 * `search`: the search-engine term fed into the AI-ask online-search seeds
 * while this family is active — both the trend chart's "?" (via
 * aiSearchKeywords) and the Forecast card's own "?" (the toggle row is the
 * view's most searchable item of interest). */
const FORECAST_KINDS: {
  kind: ForecastKind; label: string; tooltip: string; search: string;
}[] = [
  { kind: "mov_rsi", label: "RSI", tooltip: "RSI extreme-percentile buckets (mov_rsi)", search: "RSI" },
  { kind: "mov_std", label: "Bollinger", tooltip: "Bollinger breach buckets (mov_std)", search: "Bollinger 布林带" },
  { kind: "mov_pairs", label: "MA cross", tooltip: "MA5 (and close-price) vs MA60/120/255 cross buckets (mov_pairs — fast legs ma5 + price)", search: "MA 均线交叉" },
  { kind: "mov_pairs_ema", label: "EMA cross", tooltip: "EMA6 (and close-price) vs EMA60/120/255 cross buckets (mov_pairs_ema — fast legs ema6 + price)", search: "EMA 交叉" },
  { kind: "margin_ratio", label: "Margin", tooltip: "Margin-buy intensity z states (margin_ratio)", search: "融资余额" },
  { kind: "high_low_streaks", label: "HL streak", tooltip: "MA-Spread High/Low streak mean-mid anchor buckets (high_low_streaks) — every band-break excursion streak audited at its mid day", search: "high low streak" },
  { kind: "pe", label: "PE", tooltip: "PE extreme-percentile buckets over the raw pe series (pe_state) — top-pct% PE days = expensive = bearish/top (lower the better), bottom-pct% = cheap = bullish/bottom", search: "PE 市盈率" },
  { kind: "dividend", label: "Div yld", tooltip: "Dividend-yield extreme-percentile buckets over the trailing-12m D/P series (dividend_state) — top-pct% yield days = cheap/well-supported = bullish/bottom (higher the better), bottom-pct% = bearish/top", search: "股息率" },
];

/** Union of all bucket row shapes (matches ForecastTable's ForecastRow). */
type ForecastRow =
  | MovRsiForecastRow | MovStdForecastRow
  | MovPairsForecastRow | MovPairsEmaForecastRow
  | MarginRatioForecastRow | HighLowStreaksForecastRow
  | PeForecastRow | DividendForecastRow;

/** Horizon period → "+n" label (the forward forecast window in trading
 *  days — the chart shades each signal day through this window). */
const PERIOD_PLUS: Record<ForecastPeriod, string> = {
  next: "+1", "5d": "+5", "20d": "+20",
};

/** Horizon period → forward-window length in trading rows (feeds the
 *  chart's signal + forecast-window shading width). */
const PERIOD_DAYS: Record<ForecastPeriod, number> = {
  next: 1, "5d": 5, "20d": 20,
};

/** forecast_id search status (Forecast header). `found` keeps the whole
 *  identity so the jump below can read code / sec_type / stat_date /
 *  kind — and so the row tint + snapshot focus can be re-derived after
 *  scope changes the jump itself causes. */
type IdSearchState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "found"; identity: ForecastIdentityResponse };

/** Resolved identity → short caption ("→ 000300.SS · mov_rsi · 2026-05
 * · 2d · delay 1d"). Ids of families without a table here (retired
 * mov_gap / opp_pair) name the bucket family instead of jumping. */
function describeIdentity(i: ForecastIdentityResponse): string {
  const m = i.stat_date.slice(0, 4);
  const streak =
    i.streak_signal_days != null ? ` · ${i.streak_signal_days}d` : "";
  const delay =
    i.delayed_signal_days != null && i.delayed_signal_days > 0
      ? ` · delay ${i.delayed_signal_days}d`
      : "";
  return i.kind != null
    ? `→ ${i.code} · ${i.kind} · ${m}${streak}${delay}`
    : `→ ${m} · ${i.bucket} ${i.code} (no table in this UI)`;
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
  const m = r.stat_date.slice(0, 4);
  let cfg: string;
  if (kind === "mov_rsi") {
    const x = r as MovRsiForecastRow;
    cfg = `RSI${x.rsi_window} · ${x.side} ${x.pct}%`;
  } else if (kind === "mov_std") {
    const x = r as MovStdForecastRow;
    cfg = `MA${x.ma_window} · ${x.side} ${x.k}σ`;
  } else if (kind === "mov_pairs" || kind === "mov_pairs_ema") {
    const x = r as MovPairsForecastRow;
    const leg = x.fast_leg === "price" ? "close"
      : kind === "mov_pairs" ? "MA5" : "EMA6";
    cfg = `${leg} cross · ${x.side}`;
  } else if (kind === "high_low_streaks") {
    const x = r as HighLowStreaksForecastRow;
    cfg = `HL${x.band_period} · ${x.side === "top" ? "above" : "below"} ${x.pct_type}%`;
  } else if (kind === "pe") {
    const x = r as PeForecastRow;
    cfg = `PE · ${x.side} ${x.pct}%`;
  } else if (kind === "dividend") {
    const x = r as DividendForecastRow;
    cfg = `Div · ${x.side} ${x.pct}%`;
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

  // The ForecastTable's horizon toggle, mirrored here (controlled) so the
  // Forecast header's AI-ask state tag can report it. The table unmounts on
  // family change and restarts at Next — the effect below resets the mirror
  // in step, so the tag never describes a horizon the table no longer shows.
  const [forecastHorizon, setForecastHorizon] = useState<ForecastPeriod>("next");
  useEffect(() => {
    setForecastHorizon("next");
  }, [forecastKind]);

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
    /** The clicked bucket's registered signal action (the table's
     *  signal ✓ chip — the signals-layer gate's buy/sell + expected
     *  move): labels the trigger days on the trend chart with the
     *  trade-signal buy/sell triangles (green up below the low / red
     *  down above the high). null = the bucket never registered — no
     *  triangles, circles + shading only. */
    action: OhlcForecastAction | null;
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
  // the bucket's security + family + snapshot. idFocus is deliberately NOT
  // cleared by the scope-change effect above — the jump itself changes
  // the scope; the derived row tint / snapshot focus below drop out as
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
   *  and jump the panel (family + security + snapshot; found row tinted). */
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
        // Viewable families only (ids of retired families have no table
        // in this UI — the caption alone reports them).
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
   *  table). `delay` > 0 = the click landed on an INLINE EXPANSION
   *  (delay) row — that rung's own trigger days shade instead of the
   *  delay-0 ones, and the chip names the rung ("· d2"). Identity-
   *  stable (memoized ForecastTable consumer) — the freeze guard lives
   *  in the ref, not the closure. */
  const handleForecastRowClick = useCallback(
    (row: ForecastRow, period: ForecastPeriod, delay = 0) => {
      if (triggerLoadingRef.current) return;
      triggerLoadingRef.current = true;
      const seq = ++triggerSeq.current;
      const base =
        describeBucket(forecastKind || "mov_rsi", row, period) +
        (delay > 0 ? ` · d${delay}` : "");
      // The bucket's registered signal action (the signal ✓ chip) — the
      // forecast signal the trend chart labels the trigger days with.
      // Synthetic delay-rung rows carry signal_action null (the gate
      // reads delay 0), so rung clicks shade without triangles.
      const rawAction = row.signal_action;
      const action: OhlcForecastAction | null =
        rawAction === "buy" || rawAction === "sell"
          ? { action: rawAction, confidence: row.signal_confidence ?? null }
          : null;
      setSelectedForecastId(row.forecast_id);
      setPendingBase(base);
      setTriggerLoading(true);
      fetchForecastTriggerDates(row.forecast_id, delay)
        .then((d) => {
          if (seq !== triggerSeq.current) return;
          const dates = d.periods[period];
          setTriggerDates({
            // Fresh array identities on EVERY fetch — the client cache
            // hands the same response object back on a same-row re-click,
            // and the chart's overlay effect (memoized on these arrays)
            // would skip re-applying and never report settled, sticking
            // the table freeze.
            dates: dates ? [...dates] : [],
            streaks: (d.streaks[period] ?? []).map((s) => ({ ...s })),
            base,
            period,
            action,
          });
          setPendingBase(null);
        })
        .catch(() => {
          if (seq !== triggerSeq.current) return;
          setTriggerDates({ dates: [], streaks: [], base, period, action: null });
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
  // onChartReady captures the live trend-chart instance for the Forecast
  // card's AI ask — the table has no canvas of its own, so its screenshot
  // is the shaded trend chart above (trigger days / streaks / forward
  // window).
  const trendChartRef = useRef<ECharts | null>(null);
  const handleTrendChartReady = useCallback((c: ECharts | null) => {
    trendChartRef.current = c;
  }, []);
  const triggerChartOptions = useMemo(
    () => ({
      highlightDates: triggerDates?.dates ?? NO_HIGHLIGHT,
      // Dark-purple shade over each merged signal's qualifying streak
      // period (the run behind the mid date).
      highlightSpans: triggerDates?.streaks ?? NO_SPANS,
      // Light shade over each mid day's forward forecast window
      // (+1/+5/+20/+60 of the clicked horizon).
      highlightHorizonDays: triggerDates ? PERIOD_DAYS[triggerDates.period] : 1,
      // The clicked bucket's registered buy/sell — trade-signal triangle
      // labels at each trigger day (null = no triangles). Applied with
      // the rest of the trigger overlay (no chart rebuild).
      highlightAction: triggerDates?.action ?? null,
      onHighlightSettled: handleHighlightSettled,
      onChartReady: handleTrendChartReady,
    }),
    [triggerDates, handleHighlightSettled, handleTrendChartReady],
  );

  // Active forecast family → AI-ask online-search seed keyword (the
  // RSI / Bollinger / … toggle row beneath the trend chart is the view's
  // most searchable item of interest). Identity-stable for the same
  // reason as triggerChartOptions.
  const aiSearchKeywords = useMemo(
    () =>
      forecastKind
        ? [FORECAST_KINDS.find((k) => k.kind === forecastKind)?.search ?? ""]
            .filter(Boolean)
        : [],
    [forecastKind],
  );

  /** Stable identity for the horizon-toggle mirror (ForecastTable is
   *  memoized — a fresh callback would re-render the 100+ row body on
   *  every page state flip). */
  const handleForecastHorizonChange = useCallback(
    (period: ForecastPeriod) => setForecastHorizon(period),
    [],
  );

  // Header chip's forecast-action tag — the colored ▲ buy / ▼ sell
  // segment. While a new fetch is in flight it shows the PREVIOUS
  // action, consistent with the chip keeping the previous base text
  // until the fetch settles.
  const triggerAction = triggerDates?.action ?? null;

  // The Forecast card's AI-ask payload — the "?" beside the Forecast
  // title. The toggles ARE the panel's view state, so they map straight
  // into the spec: the active family supplies the state tag + the intro's
  // reading guide (its tooltip) + the online-search seed keyword (the same
  // aiSearchKeywords the trend chart's "?" gets), the horizon toggle the
  // horizon tag, and an active row highlight the trigger-days tag.
  // Table-only surface — the screenshot comes from the trend chart above
  // (trendChartRef, captured via chartOptions.onChartReady): its trigger-
  // day / streak / forward-window shading IS the visual this ask reasons
  // about. searchCode is nullable — findItemName normalizes via
  // toUpperCase, so guard it.
  const securityName = nav.searchCode ? nav.findItemName(nav.searchCode) : undefined;
  const forecastAiAskPlotInfo = useMemo(() => {
    const fam = FORECAST_KINDS.find((k) => k.kind === forecastKind);
    return derivePlotInfo({
      title: `Forecast · ${nav.searchCode}${securityName ? ` · ${securityName}` : ""}`,
      spec: {
        product: "forecast",
        intro:
          `Extreme-day forecast buckets for ${nav.searchCode}` +
          (securityName ? ` (${securityName})` : "") +
          (fam ? ` — family: ${fam.tooltip}.` : " — no family selected.") +
          " Each row = one bucket config; stats span the trailing 10y ending at" +
          " its stat_date (annual snapshots). Horizon toggle (Next/5d/20d) picks" +
          " the forward columns —" +
          " mean/max/min/std forward change, P>1%, days. signal ✓ = the weight-blended" +
          " forward profile clears the signals-layer gate — the tradable subset;" +
          " clicking a row expands its per-delay forecast rows inline" +
          " (every anchor delay's own stats at the selected horizon under" +
          " the same columns, the highest weighted-return delay bold) and" +
          " marks its trigger days on the trend chart — labeled with the" +
          " bucket's registered buy/sell triangles when the signal ✓ is set" +
          " (green up below the low / red down above the high) — plus the" +
          " streak and forward-window shading.",
        instruments: [
          {
            code: nav.searchCode,
            ...(securityName ? { name: securityName } : {}),
            assetClass: nav.secType,
          },
        ],
        state: {
          family: fam?.label ?? "none",
          horizon: PERIOD_PLUS[forecastHorizon],
          ...(triggerDates ? { trigger_days: triggerDates.base } : {}),
        },
        searchKeywords: aiSearchKeywords,
        notes: [
          "All change columns (mean/max/min/std) and P>1% are percent points; days is the qualifying trading-day count.",
          "P>1% = swing-aware reversal probability — the share of the bucket's historical forward windows that swung ≥1% AGAINST the bucket's side.",
          "signal ✓ = the weight-blended forward profile (5d 65% / next 25% / 20d 10%) clears the signals-layer gate.",
        ],
      },
    });
  }, [forecastKind, forecastHorizon, aiSearchKeywords, triggerDates, nav.searchCode, nav.secType, securityName]);

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
            aiSearchKeywords={aiSearchKeywords}
            headerAction={
              triggerDates || triggerLoading ? (
                <Chip
                  size="small"
                  icon={
                    triggerLoading ? (
                      <CircularProgress size={11} thickness={5} sx={{ color: TRIGGER_DATE_COLOR, ml: "6px" }} />
                    ) : undefined
                  }
                  label={
                    <>
                      {"⦿ trigger days · "}
                      {triggerDates?.base ?? pendingBase}
                      {triggerAction && (
                        <Box
                          component="span"
                          sx={{
                            color: triggerAction.action === "buy" ? UP_COLOR : DOWN_COLOR,
                            fontWeight: 700,
                          }}
                        >
                          {` · ${triggerAction.action === "buy" ? "▲ buy" : "▼ sell"}`}
                        </Box>
                      )}
                      {` · ${
                        triggerLoading ? "loading…" : triggerDates && triggerDates.dates.length > 0
                          ? `${triggerDates.dates.length} days`
                          : "no dates"
                      }`}
                    </>
                  }
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
              buckets (mov_rsi), Bollinger breach buckets (mov_std), MA5-vs-MA
              cross buckets (mov_pairs), EMA6-vs-EMA cross buckets
              (mov_pairs_ema) or margin-buy intensity z states
              (margin_ratio); clicking the active one again hides the table.
              Selecting one mounts ForecastTable, which lists ALL stat_dates
              of this code's buckets (config + regime_state [+ excess/
              mean-ratio-z cols] → forecast results). */}
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
                {/* AI Ask "?" — the toggles map into the ask payload
                    (family → state tag + intro + search seed, horizon →
                    state tag); the screenshot is the trend chart above
                    (its trigger-day shading is the visual context).
                    Hidden with the table when no family is picked. */}
                {forecastKind && (
                  <AiAskButton
                    plotInfo={forecastAiAskPlotInfo}
                    getInstance={() => trendChartRef.current}
                  />
                )}
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
                          ? "header dropdowns filter buckets (year header = end-month selector over annual snapshots, seeded at the latest snapshot — only that snapshot's rows show) · click a row to expand its per-delay forecasts + mark its trigger days on the trend chart above · forecast id ⏎ jumps to its bucket"
                          : "pick a bucket family to show its extreme-day forecast table"}
              </Typography>
            </Stack>
            {forecastKind && (
              <Box sx={{ px: 1, py: 0.75 }}>
                <ForecastTable
                  code={nav.searchCode}
                  secType={nav.secType}
                  kind={forecastKind}
                  horizon={forecastHorizon}
                  onHorizonChange={handleForecastHorizonChange}
                  onRowClick={handleForecastRowClick}
                  selectedRowKey={
                    selectedForecastId != null
                      ? String(selectedForecastId)
                      : idFocusActive && idFocus != null
                        ? String(idFocus.forecast_id)
                        : null
                  }
                  focusStatDate={idFocusActive && idFocus != null ? idFocus.stat_date : null}
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
