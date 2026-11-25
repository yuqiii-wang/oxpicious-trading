/**
 * News Text States (新闻文本状态) — Composites detail page
 * (/analysis/composites/news-text-states): the validated daily
 * news-text state panel (text.news_text_daily, built by
 * analysis.news_text_daily). Scope picking (2026-11-22 nav rework — the
 * dropdown is gone): a Market / Industry / Sector mode toggle — Market
 * keeps the two market scopes (market_all / market_recap) as toggle
 * chips; Industry mounts the shared sec classification nav in its
 * industry mode (the industry-ENTITY trees — sector → industry LEFT,
 * strategy → theme RIGHT, the same shape as the Analysis Summary
 * industry scope / the Sentiments pages): one single-select
 * industry/theme chip pick IS the panel's scope (entity ids ARE the
 * text.news_text_daily industry_id scope codes) and loads the
 * event-day panel; the Industry/Theme row's "All (N)" chip selects that
 * row's L1 AGGREGATE — the kind-3 sector/strategy scope, the same
 * filter the Sector-mode chip gives — instead of clearing the pick,
 * and follows the row being browsed while it is active; Sector mounts
 * the SAME nav in its sector mode
 * (mode="sector" — the shared L1-aggregate variant: the L1 Sector /
 * Strategy chip rows only, no exchange row, ONE L1 chip pick in either
 * column IS the scope through onSectorChange): the picked id IS the
 * sector scope (kind 3 — the industry aggregates rolled up the taxonomy
 * to that L1 parent; both trees' L1 ids are valid scope codes), the
 * first scope with data pre-picked so the panel reads immediately.
 *
 * LAYOUT (the 2026-11-22 sub-chart split, single parent box): FIVE
 * SEMANTIC CHARTS in ONE outlined panel, one per business logic of how
 * quantified news metrics indicate the underlying —
 *   1 Price context     the security's close (what the metrics read against)
 *   2 Mood              persistent/shock σ state + raw level (one family)
 *   3 Talk cross-check  kw tone z vs mood z (agreement = validated,
 *                       disagreement = ambiguity flag)
 *   4 Participation     abnormal-coverage AF risk flag + signed counts
 *   5 Raw mood          the merged-in Text Sentiment Metrics layer
 *                       (2026-11-22 page merge): the LLM-only Tetlock z
 *                       + ma5, flow z, and kw_tone from
 *                       analysis.text_metrics_daily — deviations from
 *                       the corpus's own trailing 60-article-day
 *                       history; NULL on sector scopes (no raw rows)
 * EVERY scope renders this same market split (2026-11-22 legacy cleanup —
 * the industry event-day single-chart collapse is gone); industry/sector
 * rows carry event-day aggregates only (σ curve columns NULL), so on an
 * industry/sector scope the level/tone/count series populate on event
 * days while the σ-band charts stay empty until the pipeline fills
 * them. The daily coverage / event-day tables live on the News Audit
 * page (the coverage migration — this page's own events section was
 * removed 2026-11-22).
 *
 * BOTTOM SECTIONS (market-mode only — industry/sector modes render the
 * focused panel alone): the cross-section bar overview (every
 * sector/industry scope's latest reading in the recent 20d, most-negative
 * first — bars at/below the −1.5 strong-neg cut in the strong negative
 * color, bars at/above the +1.5 strong-pos cut in the strong positive
 * color, the rest light neutral) and the strong-negative event LIST
 * (recent 120d).
 *
 * The panel is assistant INFO — every chip carries the research finding
 * it bridges (Garcia regime conditioning, Heston-Sinha horizon
 * asymmetry, CFSD contrarian tail, Isakin dispersion discount); the
 * human decides.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  Paper,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import type * as echarts from "echarts";
import type { IndustryNode, NewsTextStatesChartResponse } from "@shared/types";
import {
  BaseChart,
  useChartThemeMode,
  useChartData,
} from "@/shared/charts/base-chart";
import { syncTipTo } from "@/shared/charts/tooltipSync";
import {
  fetchMovAveSpreadStrategyThemes,
  fetchMovAveSpreadThemes,
} from "@/lib/api-client";
import {
  fetchNewsTextStatesChart,
  fetchNewsTextStatesDisagreement,
  fetchNewsTextStatesRecentLevels,
  fetchNewsTextStatesScopes,
  fetchNewsTextStatesStrongNegEvents,
} from "@/lib/api-client/analysis-news-text-states";
import SecClassificationNav from "@/shared/components/sec-classification/SecClassificationNav";
import { useSecNav } from "@/shared/components/sec-nav";
import {
  buildMoodOption,
  buildParticipationOption,
  buildPriceContextOption,
  buildRawMoodOption,
  buildRecentLevelsOption,
  buildTalkOption,
  stripZoomSlider,
} from "./marketStateChartOption";
import {
  deriveDisagreementState,
  deriveTextStates,
  StateChipsRow,
} from "./stateChips";

interface ScopeKey {
  kind: number;
  code: string;
}

/** Panel mode — "market" (the two market scopes as toggle chips) vs
 *  "industry" (the sec classification nav's tree mode: one
 *  single-select industry/theme chip pick IS the scope) vs "sector"
 *  (the same nav's sector mode: one L1 sector/strategy chip pick IS
 *  the kind-3 scope — the industry aggregates rolled up the taxonomy
 *  to that L1 parent). */
type PanelMode = "market" | "industry" | "sector";

/** The market scopes' code is fixed by the pipeline ('000001'). */
const MARKET_SCOPE_CODE = "000001";

/** Industry-mode nav trees — the industry-ENTITY classification (sector →
 *  industry LEFT, strategy → theme RIGHT; the same trees the Analysis
 *  Summary industry scope and the Market Sentiments pages use). Entity ids
 *  ARE the panel's industry_id scope codes, so a chip pick resolves the
 *  fetch scope directly. */
const THEMES_SOURCES = {
  industry: {
    themes: (exchange: string | null) => fetchMovAveSpreadThemes("industry", exchange),
    strategyThemes: (exchange: string | null) =>
      fetchMovAveSpreadStrategyThemes("industry", exchange),
  },
};

/** One bare sub chart + its semantic caption inside the parent panel. */
function SubChart({
  caption,
  note,
  option,
  loading,
  error,
  height,
  onReady,
  onEvents,
}: {
  caption: string;
  note: string;
  option: echarts.EChartsOption | null;
  loading: boolean;
  error: string | null;
  height: number;
  onReady: (inst: echarts.ECharts) => void;
  onEvents: Record<string, (params: unknown) => void>;
}) {
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 0.25 }}>
      <Box sx={{ display: "flex", alignItems: "baseline", gap: 1 }}>
        <Typography variant="caption" sx={{ fontWeight: 700, fontSize: "0.8rem" }}>
          {caption}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {note}
        </Typography>
      </Box>
      <BaseChart
        variant="bare"
        loading={loading}
        error={error ? `Failed to load news text states: ${error}` : null}
        option={option}
        emptyText="No panel rows in scope yet — run analysis.news_text_daily"
        height={height}
        onReady={onReady}
        onEvents={onEvents}
      />
    </Box>
  );
}

export default function NewsTextStatesPage() {
  const themeMode = useChartThemeMode();
  const [mode, setMode] = useState<PanelMode>("market");
  const [marketKind, setMarketKind] = useState<number>(0);
  const [industryCode, setIndustryCode] = useState<string | null>(null);
  const [sectorCode, setSectorCode] = useState<string | null>(null);
  // The Industry/Theme row's "All (N)" chip — the sector/strategy L1
  // AGGREGATE scope (kind 3, the same filter the Sector-mode chip gives),
  // not a cleared pick. Null = no All pick (an industry/theme chip or
  // nothing is active).
  const [allScopeL1, setAllScopeL1] = useState<string | null>(null);

  // Industry-mode classification nav (single-select — the chart endpoint
  // takes ONE scope; the picked industry/theme chip sets industryCode).
  const nav = useSecNav({
    themesSources: THEMES_SOURCES,
    defaultSecType: "industry",
    dataLabel: "news-text-states",
  });

  // The fetch scope — null in industry mode until a chip is picked (the
  // chart fetch DEFERS: an industry panel never falls back to market
  // data; the All chip defers too until a sector is expanded); sector
  // mode behaves the same until a sector chip is active.
  const scope = useMemo<ScopeKey | null>(() => {
    if (mode === "market") return { kind: marketKind, code: MARKET_SCOPE_CODE };
    if (mode === "sector") return sectorCode ? { kind: 3, code: sectorCode } : null;
    if (industryCode) return { kind: 2, code: industryCode };
    return allScopeL1 ? { kind: 3, code: allScopeL1 } : null;
  }, [mode, marketKind, industryCode, sectorCode, allScopeL1]);

  // Chip pick → scope code: resolve the picked L2 industry / theme slug to
  // its industry_id (the unified classification id space — see types.ts).
  // A null slug is a pick CLEAR (a chip re-click or a nav-internal reset —
  // the All chip never flows through here, it reports onAllPick below): it
  // drops the chip pick but leaves an active All aggregate alone (a clear
  // can only be chip-bound when a chip was picked, i.e. no All scope).
  const handleIndustryScopePick = (slug: string | null) => {
    nav.handleIndustryChange(slug);
    let node: IndustryNode | undefined;
    if (slug) {
      for (const s of nav.sectors) {
        const hit = s.industries.find((i) => i.industry_slug === slug);
        if (hit) { node = hit; break; }
      }
      setAllScopeL1(null);
    }
    setIndustryCode(node ? node.industry_id : null);
  };
  const handleThemeScopePick = (slug: string | null) => {
    nav.handleThemeChange(slug);
    let node: IndustryNode | undefined;
    if (slug) {
      for (const st of nav.strategies) {
        const hit = st.industries.find((i) => i.industry_slug === slug);
        if (hit) { node = hit; break; }
      }
      setAllScopeL1(null);
    }
    setIndustryCode(node ? node.industry_id : null);
  };

  // The row's "All (N)" chip → the row's OWN L1 aggregate (kind 3): the
  // industry row reports its active sector, the theme row its active
  // strategy (both trees' L1 ids are valid kind-3 scope codes). Replaces
  // any chip pick and clears both nav pick channels.
  const handleAllPick = (l1Id: string) => {
    nav.handleIndustryChange(null);
    nav.handleThemeChange(null);
    setIndustryCode(null);
    setAllScopeL1(l1Id);
  };

  // An All pick FOLLOWS the browsed row: switching the expanded sector /
  // strategy while an All aggregate is active re-targets it to the new L1
  // (the highlighted All chip is the one under the newly expanded row).
  // Collapsing the row (null id) keeps the last aggregate.
  const handleNavSectorChange = (id: string | null) => {
    nav.handleSectorChange(id);
    if (id) setAllScopeL1((cur) => (cur != null ? id : cur));
  };
  const handleNavStrategyChange = (id: string | null) => {
    nav.handleStrategyChange(id);
    if (id) setAllScopeL1((cur) => (cur != null ? id : cur));
  };

  // The picked entity's node (for the industry subtitle label).
  const industryNode = useMemo(() => {
    if (!industryCode) return null;
    for (const s of nav.sectors) {
      const hit = s.industries.find((i) => i.industry_id === industryCode);
      if (hit) return hit;
    }
    for (const st of nav.strategies) {
      const hit = st.industries.find((i) => i.industry_id === industryCode);
      if (hit) return hit;
    }
    return null;
  }, [nav.sectors, nav.strategies, industryCode]);

  const { data: scopes } = useChartData(
    () => fetchNewsTextStatesScopes(), []);
  const { data: ambiguityRows } = useChartData(
    () => fetchNewsTextStatesDisagreement(120), []);
  const { data: strongNeg } = useChartData(
    () => fetchNewsTextStatesStrongNegEvents(120, 20), []);
  const {
    data: recentLevels, loading: levelsLoading, error: levelsError,
  } = useChartData(
    () => fetchNewsTextStatesRecentLevels(20), []);
  const { data, loading, error } = useChartData<NewsTextStatesChartResponse | null>(
    () => (scope ? fetchNewsTextStatesChart(scope.kind, scope.code) : Promise.resolve(null)),
    [scope?.kind, scope?.code]);

  // Sector scopes (the scopes list's sector-kind rows — kind 3, the
  // industry aggregates rolled up the taxonomy to the L1 parent sector):
  // they seed the default pick (first scope with data); the chips
  // themselves are the nav trees' L1 rows, labels resolved from the trees
  // (raw code fallback).
  const sectorScopes = useMemo(
    () => (scopes?.rows ?? []).filter((s) => s.scope_kind === 3),
    [scopes]);
  // Display label: the scopes row's canonical Chinese name from the
  // classification (stats.sec_classification via the API), then the nav
  // trees, then the raw code.
  const sectorLabel = (code: string) => {
    const row = sectorScopes.find((s) => s.scope_code === code);
    if (row && row.scope_label !== code) return row.scope_label;
    for (const s of [...nav.sectors, ...nav.strategies]) {
      if (s.sector_id === code) return s.sector_label;
    }
    return row?.scope_label ?? code;
  };

  // Sector default: on entering sector mode (or when the scopes first
  // land there) with no pick yet, pre-pick the first sector scope with
  // data — the panel reads immediately (the market chips' kind-0 default
  // idiom). A deliberate pick STAYS even when it has no kind-3 rows (the
  // chart's empty state says so) — no auto-bounce off the user's chip.
  useEffect(() => {
    if (mode !== "sector") return;
    if (sectorCode) return;
    setSectorCode(sectorScopes[0]?.scope_code ?? null);
  }, [mode, sectorCode, sectorScopes]);

  const rows = useMemo(() => data?.rows ?? [], [data]);

  // THE TIME SLIDER = the top chart's ECharts dataZoom slider (the only
  // slider entry in the panel); its window broadcasts to the other three
  // sub charts through the shared dataZoom handler below. Chips and the
  // readings strip always reflect the LATEST day (the current state —
  // the slider scopes the plotted history, not the state readout).
  const visibleRows = rows;

  // Market toggle day-counts (the scopes list's market-kind rows —
  // industry picks carry the nav trees' own member counts, sector picks
  // their own chip row).
  const marketRows = useMemo(() => {
    const m = new Map<number, number>();
    for (const s of scopes?.rows ?? []) {
      if (s.scope_kind === 0 || s.scope_kind === 1) m.set(s.scope_kind, s.rows);
    }
    return m;
  }, [scopes]);

  // ONE layout for every scope (market + industry — no per-kind split):
  const hasRows = visibleRows.length > 0;
  const priceOption = useMemo(
    () => (hasRows ? buildPriceContextOption(visibleRows, themeMode) : null),
    [hasRows, visibleRows, themeMode],
  );
  // the top chart carries the ONLY slider; the rest inside-zoom only
  const moodOption = useMemo(
    () => (hasRows ? stripZoomSlider(buildMoodOption(visibleRows, themeMode)) : null),
    [hasRows, visibleRows, themeMode],
  );
  const talkOption = useMemo(
    () => (hasRows ? stripZoomSlider(buildTalkOption(visibleRows, themeMode)) : null),
    [hasRows, visibleRows, themeMode],
  );
  const flowOption = useMemo(
    () => (hasRows ? stripZoomSlider(buildParticipationOption(visibleRows, themeMode)) : null),
    [hasRows, visibleRows, themeMode],
  );
  // The merged-in raw layer (Text Sentiment Metrics): empty on sector
  // scopes — the SQL binds raw_industry_id NULL there, so all four
  // series come back NULL and only the axis/frame render.
  const rawMoodOption = useMemo(
    () => (hasRows ? stripZoomSlider(buildRawMoodOption(visibleRows, themeMode)) : null),
    [hasRows, visibleRows, themeMode],
  );

  const states = useMemo(() => deriveTextStates(visibleRows), [visibleRows]);
  const ambiguity = useMemo(
    () => deriveDisagreementState(ambiguityRows?.rows ?? []),
    [ambiguityRows],
  );

  // Cross-section overview — every sector/industry scope's latest
  // reading in the recent 20d window, most-negative first; bars at/below
  // the strong-neg cut (−1.5) carry the strong negative color, bars
  // at/above the strong-pos cut (+1.5) the strong positive color, the
  // rest the light neutral fill.
  const recentLevelRows = useMemo(
    () => recentLevels?.rows ?? [], [recentLevels]);
  const recentLevelsOption = useMemo(
    () => (recentLevelRows.length > 0
      ? buildRecentLevelsOption(recentLevelRows, themeMode)
      : null),
    [recentLevelRows, themeMode]);

  // Shared cross-chart wiring: (a) the dataZoom broadcast — a zoom in
  // ANY sub chart dispatches the same [start, end] window to the other
  // four; (b) the hover crosshair sync — updateAxisPointer on one chart
  // shows the same date's tooltip on the other four, each rendering its
  // own series (the syncTipTo manual mechanism: echarts.connect's
  // per-series resolution breaks on null rows). zoomSyncing/syncingRef
  // guard the echo loops.
  const chartRefs = useRef<Array<echarts.ECharts | null>>([
    null, null, null, null, null,
  ]);
  const zoomSyncing = useRef(false);
  const tipSyncing = useRef(false);
  const handleDataZoom = (idx: number) => (params: unknown) => {
    if (zoomSyncing.current) return;
    const p = params as {
      start?: number; end?: number;
      batch?: Array<{ start?: number; end?: number }>;
    };
    const dz = p.batch?.[0] ?? p;
    if (dz.start == null || dz.end == null) return;
    zoomSyncing.current = true;
    try {
      chartRefs.current.forEach((inst, j) => {
        if (j !== idx && inst != null && !inst.isDisposed()) {
          inst.dispatchAction({
            type: "dataZoom", start: dz.start, end: dz.end,
          });
        }
      });
    } finally {
      zoomSyncing.current = false;
    }
  };
  const handleAxisPointer = (idx: number) => (params: unknown) => {
    const from = chartRefs.current[idx];
    chartRefs.current.forEach((to, j) => {
      if (j !== idx) syncTipTo(to, params, tipSyncing, from);
    });
  };
  const chartOnReady = (idx: number) => (inst: echarts.ECharts) => {
    chartRefs.current[idx] = inst;
  };
  const chartEvents = (idx: number) => ({
    dataZoom: handleDataZoom(idx),
    updateAxisPointer: handleAxisPointer(idx),
  });

  const scopeSubtitle = mode === "sector"
    ? `${sectorCode ?? ""}${sectorCode ? ` · ${sectorLabel(sectorCode)}` : ""} — sector event-day grain (close: member-industry composite mean)`
    : mode === "industry"
      ? industryCode
        ? `${industryCode}${industryNode
            ? ` · ${industryNode.industry_label.split("  ")[0] ?? industryNode.industry_label}`
            : ""} — industry event-day grain (close: industry composite)`
        : allScopeL1
          ? `${allScopeL1} · ${sectorLabel(allScopeL1)} — sector aggregate (the row's All chip) — event-day grain (close: member-industry composite mean)`
          : ""
      : marketKind === 1
        ? "market_recap — broad-market recap subset (close: SSE Composite)"
        : "market_all — every digested non-fake article (close: SSE Composite)";

  const last = states.latest;
  const lastLine = last
    ? `level ${last.sent_level?.toFixed(2) ?? "—"} · z ${last.sent_z?.toFixed(2) ?? "—"}`
    + ` · ema10 ${last.sent_ema10?.toFixed(2) ?? "—"} · Δ ${last.sent_delta?.toFixed(2) ?? "—"}`
    + ` · AF ${last.flow_af?.toFixed(1) ?? "—"} (+${last.flow_pos ?? 0}/−${last.flow_neg ?? 0})`
    + ` · wk5 z ${states.weeklyZ?.toFixed(2) ?? "—"}`
    + (last.disp_origin != null ? ` · disp ${last.disp_origin.toFixed(2)}` : "")
    + ` · tone ${last.lex_tone_z?.toFixed(2) ?? "—"}`
    : "";

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 2, flexWrap: "wrap" }}>
        <Typography variant="h5" sx={{ fontWeight: 700 }}>
          News Text States (新闻文本状态)
        </Typography>
        {/* Scope mode — Market (the two market scopes as toggle chips),
            Industry or Sector (the classification nav below). */}
        <ToggleButtonGroup
          value={mode}
          exclusive
          size="small"
          onChange={(_, v: PanelMode | null) => { if (v) setMode(v); }}
        >
          <ToggleButton value="market"
            sx={{ px: 1.5, py: 0.25, fontSize: "0.72rem", textTransform: "none" }}>
            Market
          </ToggleButton>
          <ToggleButton value="industry"
            sx={{ px: 1.5, py: 0.25, fontSize: "0.72rem", textTransform: "none" }}>
            Industry
          </ToggleButton>
          <ToggleButton value="sector"
            sx={{ px: 1.5, py: 0.25, fontSize: "0.72rem", textTransform: "none" }}>
            Sector
          </ToggleButton>
        </ToggleButtonGroup>
        {mode === "market" && (
          <ToggleButtonGroup
            value={marketKind}
            exclusive
            size="small"
            onChange={(_, v: number | null) => { if (v != null) setMarketKind(v); }}
          >
            <ToggleButton value={0}
              sx={{ px: 1.5, py: 0.25, fontSize: "0.72rem", textTransform: "none" }}>
              market_all{marketRows.get(0) != null ? ` (${marketRows.get(0)}d)` : ""}
            </ToggleButton>
            <ToggleButton value={1}
              sx={{ px: 1.5, py: 0.25, fontSize: "0.72rem", textTransform: "none" }}>
              market_recap{marketRows.get(1) != null ? ` (${marketRows.get(1)}d)` : ""}
            </ToggleButton>
          </ToggleButtonGroup>
        )}
      </Box>

      {/* Industry mode: the sec classification nav in its tree mode —
          single-select, the picked L2 industry / theme chip's industry_id IS
          the fetch scope. Sector mode: the SAME nav with mode="sector" (the
          shared L1-aggregate variant): only the Sector / Strategy chip rows
          render and ONE L1 pick in either column lands on onSectorChange —
          sectorCode IS the kind-3 scope (the nav resolves the picked id's
          column from the trees internally). Only mode / sectorId /
          onSectorChange differ between the two mounts; the sector pick keeps
          its own state (sectorCode) so the modes' selections stay
          independent, and the tree-mode-only props are simply ignored in
          sector mode. */}
      {(mode === "industry" || mode === "sector") && (
        <>
          <SecClassificationNav
            mode={mode === "sector" ? "sector" : "tree"}
            sectors={nav.sectors}
            strategies={nav.strategies}
            sectorId={mode === "sector" ? sectorCode : nav.sectorId}
            onSectorChange={mode === "sector" ? setSectorCode : handleNavSectorChange}
            industrySlug={nav.industrySlug}
            onIndustryChange={handleIndustryScopePick}
            exchange={nav.exchange}
            onExchangeChange={nav.handleExchangeChange}
            strategyId={nav.strategyId}
            themeSlug={nav.themeSlug}
            onStrategyChange={handleNavStrategyChange}
            onThemeChange={handleThemeScopePick}
            onAllPick={handleAllPick}
            loading={nav.loading}
            sx={{ mb: 0 }}
          />
          {nav.error && (
            <Alert severity="error" variant="filled">
              Failed to load classification: {nav.error}
            </Alert>
          )}
        </>
      )}

      {scope && (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
          <StateChipsRow states={states} ambiguity={ambiguity} />
          <Typography variant="body2" color="text.secondary">
            {lastLine || "No defined state yet — the panel needs ≥3 scored articles on a day."}
          </Typography>
          {mode === "market" && marketKind === 1 && (
            <Typography variant="caption" color="text.secondary">
              Official-recap channel: state-media tone historically acts
              with a delay (day-0 moves are already priced — Xu 2025);
              read this scope&apos;s level as lagging confirmation, not a
              same-day trigger.
            </Typography>
          )}
        </Box>
      )}

      {mode === "industry" && !industryCode && !allScopeL1 ? (
        <Paper variant="outlined" sx={{ p: 4, textAlign: "center" }}>
          <Typography variant="body2" color="text.secondary">
            Pick an industry chip (or a strategy theme, or a row&apos;s All
            chip for the whole sector/strategy) in the nav above — its
            news-text state panel loads here.
          </Typography>
        </Paper>
      ) : mode === "sector" && !sectorCode ? (
        <Paper variant="outlined" sx={{ p: 4, textAlign: "center" }}>
          <Typography variant="body2" color="text.secondary">
            No sector scope selected — the sector chips fill in once the
            pipeline has written kind-3 rows.
          </Typography>
        </Paper>
      ) : (
        <Paper variant="outlined" sx={{ p: 1.5, display: "flex", flexDirection: "column", gap: 1.5 }}>
          <Box>
            <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
              News-text state panel
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {scopeSubtitle} — five semantic charts over one shared time
              window (slider on the top chart; hover crosshair syncs the
              date across all)
            </Typography>
          </Box>
          <SubChart
            caption="1 · Price context"
            note="the underlying's close — what the news metrics are read against (time slider here)"
            option={priceOption}
            loading={loading}
            error={error}
            height={170}
            onReady={chartOnReady(0)}
            onEvents={chartEvents(0)}
          />
          <SubChart
            caption="2 · Mood (情绪状态) — state, not direction"
            note="persistent ema10 / shock ema3 (σ, left) vs raw blended level (right) — contrarian at the monthly tail"
            option={moodOption}
            loading={loading}
            error={error}
            height={240}
            onReady={chartOnReady(1)}
            onEvents={chartEvents(1)}
          />
          <SubChart
            caption="3 · Talk cross-check (话语交叉验证)"
            note="kw tone (solid) vs mood z (dashed) — agreement validates the LLM read, divergence flags ambiguity"
            option={talkOption}
            loading={loading}
            error={error}
            height={180}
            onReady={chartOnReady(2)}
            onEvents={chartEvents(2)}
          />
          <SubChart
            caption="4 · Participation (参与度) — abnormal coverage is the risk flag"
            note="AF bars (±1 abnormal) + signed article counts — validated negative forward IC at 1-10d"
            option={flowOption}
            loading={loading}
            error={error}
            height={200}
            onReady={chartOnReady(3)}
            onEvents={chartEvents(3)}
          />
          <SubChart
            caption="5 · Raw mood (原始情绪层) — the merged text-metrics layer, LLM-only"
            note="Tetlock z + ma5 (σ) / flow z / kw tone (−1..+1) — deviations from the corpus's own trailing 60d; empty on sector scopes"
            option={rawMoodOption}
            loading={loading}
            error={error}
            height={200}
            onReady={chartOnReady(4)}
            onEvents={chartEvents(4)}
          />
        </Paper>
      )}

      {/* Cross-section overview — every sector/industry scope's latest
          reading in the recent 20d window (market-mode only, alongside the
          strong-neg LIST below; industry/sector modes render the focused
          panel alone). The fetch stays mounted so toggling back to Market
          re-renders the chart without a refetch. */}
      {mode === "market" && (
        <Box>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 0.5 }}>
            Sector &amp; industry levels (recent 20d, latest reading)
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1 }}>
            One bar per sector/industry scope — its most recent event-day
            level in the window, most-negative first. Red bars sit at/below
            the strong-negative cut (−1.5, the validated reversal-event
            level — a watch item, not a trigger); green bars sit at/above
            the strong-positive cut (+1.5); grey bars are the rest of the
            cross-section. Scopes with no event day in the window are
            absent. Zoom the slider to read labels.
          </Typography>
          <BaseChart
            variant="bare"
            loading={levelsLoading}
            error={levelsError ? `Failed to load recent levels: ${levelsError}` : null}
            option={recentLevelsOption}
            emptyText="No sector/industry readings in the recent window — run analysis.news_text_daily"
            height={300}
          />
        </Box>
      )}

      {mode === "market" && (
        <Box>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 0.5 }}>
            Strong-negative industry events (recent 120d, level ≤ −1.5)
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1 }}>
            The validated reversal shape: strong-negative industry event
            days were followed by positive 5-day industry returns
            (+1.8..+7.4% across AI / SEMI / COMMS in the 2023+ study; 6-13
            events per scope — suggestive, not proven). The full daily
            coverage / event-day tables live on the News Audit page
            (Coverage audit view). A fresh event here is a watch item, not
            a trigger.
          </Typography>
          {(strongNeg?.rows.length ?? 0) === 0
            ? (
              <Typography variant="body2" color="text.secondary">
                No strong-negative industry events in the window.
              </Typography>
            )
            : (
              <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
                {strongNeg!.rows.map((r) => (
                  <Box key={`${r.date}-${r.industry_id}`}
                    sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
                    <Typography variant="body2" sx={{ minWidth: 88 }}>
                      {r.date}
                    </Typography>
                    <Typography variant="body2" sx={{ minWidth: 170, fontWeight: 600 }}>
                      {r.industry_label !== r.industry_id
                        ? `${r.industry_label} (${r.industry_id})`
                        : r.industry_id}
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      level {r.sent_level.toFixed(2)} · {r.n_scored} scored
                      (+{r.flow_pos}/−{r.flow_neg})
                    </Typography>
                  </Box>
                ))}
              </Box>
            )}
        </Box>
      )}
    </Box>
  );
}
