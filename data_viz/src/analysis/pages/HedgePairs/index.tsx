/**
 * Hedge Pairs signal page (default export) — /analysis/signals/hedge-pairs.
 *
 * INDUSTRY-ONLY page on the SAME shared multi-select nav kit as the
 * Composites Opposite Industry Correlations page (useSecNav over the
 * industry-sentiments trees + SecClassificationNavMulti; the per-industry
 * L3 index row is omitted — the pair is per-industry, not per-code).
 *
 * PAIR MODE (this page only — the nav feature is prop-gated): the "Mode ›
 * Pair" toggle in the classification nav constrains selection to exactly
 * TWO industries/themes (a 3rd pick auto-forgets the oldest). Pick order
 * defines the roles: pick 1 = subject (the hedging instrument), pick 2 =
 * benchmark (the position); the swap button flips the roles. Pair mode off
 * falls back to free multi-select (the page charts the two most recent
 * picks). Default pair: 红利 (DIV_GENERAL, strategy theme) vs 科技
 * (TECH_GENERAL, sector industry).
 *
 * Layout (top → bottom, the 2026-10-18 signals placement: the RULES
 * above the chart, the replayed history below the curve plots):
 *   1. Pair summary — subject × benchmark chips + role swap.
 *   2. Hedge Pair Signals header + Refresh — the signals section's
 *      one refresh (rules, markers and history together).
 *   3. The pair's RULE GROUPS (the shared PortfolioSignalRulesPanel —
 *      it takes the pair's two industry codes; the server matches the
 *      managed 'A~B' portfolio whose legs sit in them and expands its
 *      signal_rule_ids arrays to rule rows).
 *   4. Upper plot — the PAIR PORTFOLIO chart: both industries'
 *      composite close curves (mean_close, pool "all") on TWO separate
 *      y-axes in one grid (the MA-Spread dual-axis chart style; x = the
 *      union of both calendars), an OPTIONAL broad-market benchmark
 *      curve (rebased to 100, dashed) with an in-chart dropdown, and
 *      the portfolio's signal-history events as buy ▲ / sell ▼ /
 *      wait ◆ markers on BOTH legs at the event date.
 *   5. Lower plot — the pair's industry × industry Pair hedge ratio
 *      (HedgeRatioPanel over the shared pairRatioChart kit).
 *   6. The pair's signal HISTORY (the shared PortfolioSignalHistoryPanel —
 *      the portfolio EVENT tier, ONE row per event day with the day's
 *      portfolio-level classes; expanding a row reveals the event
 *      records AND the triggering cell-level signals, each with its
 *      own date — different sub signals on different nearby days).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Box,
  IconButton,
  MenuItem,
  Paper,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { SwapHoriz } from "@mui/icons-material";
import { SecNavShell, useSecNav } from "@/shared/components/sec-nav";
import SecClassificationNavMulti from "@/shared/components/sec-classification/SecClassificationNavMulti";
import type { PairNavEntry } from "@/shared/components/sec-classification/SecClassificationNavMulti";
import {
  fetchIndustrySentimentsStrategyThemes,
  fetchIndustrySentimentsThemes,
  fetchPortfolioSignalHistory,
  invalidateCacheForPrefix,
  type PortfolioHistorySignal,
} from "@/lib/api-client";
import { PortfolioSignalHistoryPanel, PortfolioSignalRulesPanel } from "@/shared/components/portfolio-signal";
import { signalSubWordLoose } from "@/shared/components/signal-code-trend/families";
import RefreshButton from "@/components/RefreshButton";
import PairCurvesChart, { type PairIndustryRef } from "./PairCurvesChart";
import HedgeRatioPanel from "./HedgeRatioPanel";

/** Nav trees endpoints (index classification — same as Composites). */
const THEMES_SOURCES = {
  index: {
    themes: (exchange: string | null) => fetchIndustrySentimentsThemes(exchange),
    strategyThemes: (exchange: string | null) =>
      fetchIndustrySentimentsStrategyThemes(exchange),
  },
};

/** Cache prefixes invalidated on Refresh. */
const CACHE_PREFIXES = [
  "/api/analysis/industry-sentiments/themes",
  "/api/analysis/industry-sentiments/strategy-themes",
];

/** The default pair — 红利 (strategy theme) vs 科技 (sector industry),
 *  the showcase hedge pair (offset-mode h* ≈ 0..1.1, non-degenerate). */
const DEFAULT_SUBJECT_ID = "DIV_GENERAL";
const DEFAULT_BENCHMARK_ID = "TECH_GENERAL";

/** The hedge_pairs family runs the pipeline's default INDEX scope — its
 *  rows (composite pair codes) carry the index sec_type stamp. */
const HP_SEC_TYPE = "index";

export default function HedgePairsPage() {
  const nav = useSecNav({
    themesSources: THEMES_SOURCES,
    dataLabel: "classification",
    mutuallyExclusive: false,
    onInvalidateCache: () => {
      for (const prefix of CACHE_PREFIXES) {
        invalidateCacheForPrefix(prefix);
      }
    },
  });

  // ---- Pair mode (the nav's prop-gated feature) + the controlled pair -----
  const [pairMode, setPairMode] = useState(true);
  const [pairEntries, setPairEntries] = useState<PairNavEntry[]>([]);
  // Role flip: false = pick 1 is the subject, pick 2 the benchmark.
  const [swapped, setSwapped] = useState(false);

  // Free multi-select state (pair mode OFF fallback — the page charts the
  // two most recent picks of the merged LEFT slugs + RIGHT theme selection).
  const [selectedIndustrySlugs, setSelectedIndustrySlugs] = useState<string[]>([]);

  // ---- Tree lookups (LEFT industries + RIGHT themes share industry_id) ----
  const industryBySlug = useMemo(() => {
    const m = new Map<string, { id: string; label: string; sectorId: string; isStrategy: boolean }>();
    for (const s of nav.sectors) {
      for (const ind of s.industries) {
        if (!m.has(ind.industry_slug)) {
          m.set(ind.industry_slug, {
            id: ind.industry_id, label: ind.industry_label,
            sectorId: s.sector_id, isStrategy: false,
          });
        }
      }
    }
    for (const s of nav.strategies) {
      for (const ind of s.industries) {
        if (!m.has(ind.industry_slug)) {
          m.set(ind.industry_slug, {
            id: ind.industry_id, label: ind.industry_label,
            sectorId: s.sector_id, isStrategy: true,
          });
        }
      }
    }
    return m;
  }, [nav.sectors, nav.strategies]);

  const industryById = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of nav.sectors) {
      for (const ind of s.industries) {
        if (!m.has(ind.industry_id)) m.set(ind.industry_id, ind.industry_label);
      }
    }
    for (const s of nav.strategies) {
      for (const ind of s.industries) {
        if (!m.has(ind.industry_id)) m.set(ind.industry_id, ind.industry_label);
      }
    }
    return m;
  }, [nav.sectors, nav.strategies]);

  // ---- One-time seed: the default pair + its browsing context -------------
  // Resolved through whichever column holds each default (红利 is a RIGHT
  // strategy theme, 科技 a LEFT sector industry — same split as the
  // Composites page's seed). Sets the pair entries so the chips highlight
  // and the charts have data on first paint.
  const seededRef = useRef(false);
  useEffect(() => {
    if (seededRef.current) return;
    if (nav.sectors.length === 0 && nav.strategies.length === 0) return;
    const byId = new Map<string, { slug: string; label: string; sectorId: string; isStrategy: boolean }>();
    for (const s of nav.sectors) {
      for (const ind of s.industries) {
        if (!byId.has(ind.industry_id)) {
          byId.set(ind.industry_id, {
            slug: ind.industry_slug, label: ind.industry_label,
            sectorId: s.sector_id, isStrategy: false,
          });
        }
      }
    }
    for (const s of nav.strategies) {
      for (const ind of s.industries) {
        if (!byId.has(ind.industry_id)) {
          byId.set(ind.industry_id, {
            slug: ind.industry_slug, label: ind.industry_label,
            sectorId: s.sector_id, isStrategy: true,
          });
        }
      }
    }
    const subj = byId.get(DEFAULT_SUBJECT_ID);
    const bench = byId.get(DEFAULT_BENCHMARK_ID);
    if (!subj || !bench) return;
    seededRef.current = true;
    const toEntry = (e: { slug: string; label: string; sectorId: string; isStrategy: boolean }, id: string): PairNavEntry => ({
      industryId: id, label: e.label, slug: e.slug, isStrategy: e.isStrategy,
    });
    setPairEntries([toEntry(subj, DEFAULT_SUBJECT_ID), toEntry(bench, DEFAULT_BENCHMARK_ID)]);
    // Browsing context so both default chips are visible in their columns.
    nav.handleSectorChange(bench.isStrategy ? null : bench.sectorId);
    nav.handleStrategyChange(subj.isStrategy ? subj.sectorId : null);
    if (subj.isStrategy) nav.handleThemeChange(subj.slug);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nav.sectors, nav.strategies]);

  // ---- Effective pair ------------------------------------------------------
  // Pair mode ON: the nav's FIFO entries (pick 1 = subject, pick 2 =
  // benchmark). OFF: the two most recent picks of the merged multi-select
  // (LEFT slugs in tick order, then the RIGHT theme).
  const pair: [PairIndustryRef | null, PairIndustryRef | null] = useMemo(() => {
    if (pairMode) {
      const toRef = (e: PairNavEntry | undefined): PairIndustryRef | null =>
        e ? { code: e.industryId, label: e.label } : null;
      return [toRef(pairEntries[0]), toRef(pairEntries[1])];
    }
    const ids: string[] = [];
    for (const slug of selectedIndustrySlugs) {
      const hit = industryBySlug.get(slug);
      if (hit) ids.push(hit.id);
    }
    const theme = nav.themeSlug ? industryBySlug.get(nav.themeSlug) : undefined;
    if (theme) ids.push(theme.id);
    const last2 = ids.slice(-2);
    const toRef = (id: string | undefined): PairIndustryRef | null =>
      id ? { code: id, label: industryById.get(id) ?? id } : null;
    return [toRef(last2[0]), toRef(last2[1])];
  }, [pairMode, pairEntries, selectedIndustrySlugs, nav.themeSlug, industryBySlug, industryById]);

  const [pick1, pick2] = pair;
  const subject = swapped ? pick2 : pick1;
  const benchmark = swapped ? pick1 : pick2;
  // Self-pair guard (a duplicated id would 400 the ratios endpoint).
  const safeBenchmark =
    benchmark && subject && benchmark.code === subject.code ? null : benchmark;

  const refLabel = (r: PairIndustryRef): string =>
    r.label === r.code ? r.code : `${r.label} (${r.code})`;

  // ---- The pair portfolio's SIGNAL tier (2026-10-18 placement: the
  // rule groups ABOVE the chart, the replayed history BELOW the plots;
  // the events also mark the chart) -------------------------------------
  // The canonical composite pair id — lexicographic 'A~B' (mirrors the
  // pipeline's fetch/inputs/_hedge_pairs.pair_code). null when the pair
  // is incomplete or degenerate.
  const pairCode = useMemo(
    () =>
      subject && safeBenchmark && subject.code !== safeBenchmark.code
        ? `${[subject.code, safeBenchmark.code].sort().join("~")}`
        : null,
    [subject, safeBenchmark],
  );
  const industryCodes = useMemo(
    () => (pairCode && subject && safeBenchmark
      ? [...new Set([subject.code, safeBenchmark.code])]
      : []),
    [pairCode, subject, safeBenchmark],
  );
  const [refreshKey, setRefreshKey] = useState(0);
  const [signalHistory, setSignalHistory] = useState<PortfolioHistorySignal[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [expandedDate, setExpandedDate] = useState<string | null>(null);
  // the coordinated SUB PORTFOLIO TYPE focus (the header dropdown):
  // null = all; narrows the curve markers, the history rows and the
  // expanded cell surface to ONE orchestrated sub rule
  const [subType, setSubType] = useState<string | null>(null);
  const [subTypes, setSubTypes] = useState<string[]>([]);

  // a pair switch changes the whole scope — reset the expansion and
  // the sub-type focus (the new portfolio may not carry it)
  useEffect(() => {
    setExpandedDate(null);
    setSubType(null);
  }, [pairCode]);

  // the reported sub types no longer carrying the selection (e.g. the
  // refresh dropped a sub) — fall back to all
  useEffect(() => {
    if (subType != null && !subTypes.includes(subType)) setSubType(null);
  }, [subType, subTypes]);

  useEffect(() => {
    if (!pairCode) {
      setSignalHistory([]);
      setHistoryLoading(false);
      return;
    }
    let cancelled = false;
    setHistoryLoading(true);
    // the PORTFOLIO EVENT history (2026-10-21): every coordinated
    // event of the pair with its member cell-level signals — BOTH
    // orchestrating families' rows ride the one table (hedge_pairs
    // pair-native + hp_rsi orchestrated), so the sub-type dropdown
    // spans them all through this single fetch
    fetchPortfolioSignalHistory(HP_SEC_TYPE, pairCode)
      .then((resp) => {
        if (cancelled) return;
        setSignalHistory(resp.events);
      })
      .catch(() => {
        if (!cancelled) setSignalHistory([]);
      })
      .finally(() => {
        if (!cancelled) setHistoryLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [pairCode, refreshKey]);

  // the sub-type focus narrows everything downstream: the curve
  // markers and the history table read the filtered rows
  const visibleHistory = useMemo(
    () =>
      subType == null
        ? signalHistory
        : signalHistory.filter((s) => s.signal_sub_type === subType),
    [signalHistory, subType],
  );

  const handleSignalsRefresh = () => {
    // the portfolio/rules/history fetches ride the live-data prefix —
    // without this the client LRU would serve them stale on the very
    // refresh that promises "bypass cache"
    invalidateCacheForPrefix("/api/live-data/trading-signals/");
    setRefreshKey((k) => k + 1);
  };

  // the chart's marker payload (identity-stable — the option memo keys
  // on it): the sub-type-filtered coordinated events, buy/sell/wait
  // through the shared marker language (wait = the grey diamond)
  const signalEvents = useMemo(
    () =>
      visibleHistory.map((s) => ({
        date: s.date,
        action: s.action,
        is_rejection: s.action === "wait",
      })),
    [visibleHistory],
  );

  return (
    <SecNavShell
      nav={nav}
      title="Hedge Pairs"
      backPath="/analysis/signals"
      backLabel="back to signals"
      subtitle={`${nav.headerLabel} — pick an industry pair in the nav (default 红利 vs 科技): both composite curves on two y-axes above, the pair's hedge ratio and the pair portfolio's signal rules + history below.`}
      showSearch={false}
      refreshTooltip="Refresh the classification trees (bypass cache)"
      errorPrefix="classification data"
      navSlot={
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
          pairModeAvailable
          pairMode={pairMode}
          onPairModeChange={setPairMode}
          pairEntries={pairEntries}
          onPairEntriesChange={setPairEntries}
        />
      }
    >
      {/* ---- Pair summary: roles + swap ---- */}
      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1.5, alignItems: "center", mb: 1.5 }}>
        <Typography variant="body2" sx={{ fontWeight: 600 }}>
          Pair
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {subject ? `subject ${refLabel(subject)}` : "subject — pick 1"}
        </Typography>
        <Tooltip title="Swap subject / benchmark roles">
          <IconButton
            size="small"
            aria-label="swap roles"
            onClick={() => setSwapped((v) => !v)}
          >
            <SwapHoriz fontSize="small" />
          </IconButton>
        </Tooltip>
        <Typography variant="body2" color="text.secondary">
          {safeBenchmark ? `benchmark ${refLabel(safeBenchmark)}` : "benchmark — pick 2"}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {pairMode
            ? "pair mode — a 3rd industry pick drops the oldest; pick order sets the roles"
            : "free multi-select — the two most recent picks chart"}
        </Typography>
      </Box>

      {/* ---- Hedge Pair Signals header + the section's one Refresh ---- */}
      <Paper variant="outlined" sx={{ px: 1.5, py: 1 }}>
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
            gap: 1.5,
            flexWrap: "wrap",
          }}
        >
          <Box sx={{ minWidth: 260 }}>
            <Typography variant="subtitle2">
              Hedge Pair Signals — the pair portfolio&apos;s rule groups + history
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {pairCode
                ? `${pairCode} — entry: both legs' capitulation witnesses pair within ~5 traded days (one leg's action → the other ≤5d); exits: harvest · laggard exhaustion · regime stop`
                : "pick two industries in the nav to load a pair portfolio's signals"}
            </Typography>
          </Box>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1,
              flexWrap: "wrap",
            }}
          >
            <TextField
              select
              size="small"
              label="Sub type"
              value={subType ?? ""}
              onChange={(e) => setSubType(e.target.value || null)}
              sx={{ minWidth: 220, "& .MuiOutlinedInput-input": { fontSize: "0.75rem", py: 0.5 } }}
            >
              <MenuItem value="">All sub types</MenuItem>
              {subTypes.map((s) => (
                <MenuItem key={s} value={s} sx={{ fontSize: "0.75rem" }}>
                  {signalSubWordLoose(s)}
                  <Box
                    component="span"
                    sx={{ ml: 1, fontSize: "0.6rem", color: "text.secondary" }}
                  >
                    {s}
                  </Box>
                </MenuItem>
              ))}
            </TextField>
            <RefreshButton
              onClick={handleSignalsRefresh}
              loading={historyLoading}
              label="Refresh"
              tooltip="Refresh the pair's portfolio signals + history (bypass cache)"
            />
          </Box>
        </Box>
      </Paper>

      {/* ---- The RULE GROUPS (above the chart) — the sub-type focus
              narrows the expanded cell surface ---- */}
      <PortfolioSignalRulesPanel
        codes={industryCodes}
        secType={HP_SEC_TYPE}
        reloadKey={refreshKey}
        subType={subType}
        onSubTypes={setSubTypes}
      />

      {/* ---- Upper plot: the PAIR PORTFOLIO chart — both legs'
              composite curves on two y-axes + the portfolio's signal
              events as buy/sell/wait markers ---- */}
      <PairCurvesChart
        industryA={subject}
        industryB={safeBenchmark}
        signalEvents={signalEvents}
      />

      {/* ---- Lower plot: the pair's industry × industry hedge ratio ---- */}
      <HedgeRatioPanel subject={subject} benchmark={safeBenchmark} />

      {/* ---- The replayed HISTORY (below the curve plots) — the
              sub-type-filtered PORTFOLIO events (one row per event
              day; the event records + the triggering cell-level
              signals, each on its own day, in the expansion) ---- */}
      <PortfolioSignalHistoryPanel
        events={visibleHistory}
        expandedDate={expandedDate}
        onExpandedDateChange={setExpandedDate}
      />
    </SecNavShell>
  );
}
