/**
 * News Text States chart options — FIVE SEMANTIC SUB CHARTS, one chart
 * per business logic of how quantified news metrics indicate the
 * underlying security. Same semantics group into one chart; different
 * semantics never share axes:
 *
 *   buildPriceContextOption  — the underlying's close (context only,
 *                              what the news metrics are read against)
 *   buildMoodOption          — MOOD: how the corpus feels (persistent
 *                              ema10 + shock ema3 in σ on the left axis,
 *                              raw blended level [-5,+5] on the right —
 *                              one semantic family, one chart, two unit
 *                              axes). Validated: mood is state, not
 *                              direction; the persistent line is
 *                              contrarian at the monthly tail.
 *   buildTalkOption          — TALK CROSS-CHECK: kw_market tone z vs the
 *                              mood z reference (both σ, one axis) —
 *                              agreement validates the LLM read,
 *                              disagreement flags ambiguity (ρ=0.675
 *                              baseline).
 *   buildParticipationOption — PARTICIPATION: abnormal-coverage AF bars
 *                              (±1 risk flag, validated negative forward
 *                              IC) + signed article counts (right axis).
 *   buildRawMoodOption       — RAW MOOD: the merged-in Text Sentiment
 *                              Metrics layer (analysis.text_metrics_daily,
 *                              LLM-only, article-date): Tetlock abnormal
 *                              sentiment z + its ma5 smoothing, the
 *                              Heston-Sinha flow z, and the kw_tone
 *                              lexicon overlay — all deviations from the
 *                              corpus's own trailing 60-article-day
 *                              history, one shared σ axis (kw_tone
 *                              −1..+1 rides the same scale). Same-day
 *                              validation: z co-moves ~0.45 with the
 *                              index day-return, next day ~none. NULL on
 *                              sector scopes (no raw rows).
 *
 * EVERY scope renders this same five-chart split (2026-11-22 legacy
 * cleanup — the industry event-day single-chart collapse is gone):
 * industry rows carry event-day aggregates only (their σ curve columns
 * are NULL), so on an industry scope the level/tone/count series
 * populate on event days while the σ-band charts stay empty until the
 * pipeline fills them.
 *
 * PERF CONTRACT (the mousemove/tooltip slowness study, 2026-11-22):
 *   - sampling: "lttb" on every dense line (1.6k points downsample to
 *     the pixel grid before paint),
 *   - triggerLineEvent: false (no per-segment hit-testing on mousemove —
 *     the dominant mousemove cost in ECharts line series),
 *   - silent: true on context series (close never participates in
 *     hover/emphasis),
 *   - one small canvas per chart: a tooltip repaint invalidates its own
 *     ~200px band instead of the whole 1183×700 mega-canvas,
 *   - no cross-grid axisPointer link: the mega-option's linked pointer
 *     re-rendered every band on each mousemove.
 * Pure builders — complete options, no stale-series merges (notMerge).
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import {
  axisColors,
  commonDataZoom,
  commonGrid,
  DOWN_COLOR,
  MA20_COLOR,
  MA252_COLOR,
  MA5_COLOR,
  MA60_COLOR,
  MUTED_PALETTE,
  NEUTRAL_FILL,
  SKEW_ABOVE_COLOR,
  SKEW_BELOW_COLOR,
  SKEW_NEUTRAL_COLOR,
  UP_COLOR,
} from "@/theme/chart-palette";
import type {
  NewsTextStatesChartRow,
  NewsTextStatesRecentLevelRow,
} from "@shared/types";

const ABNORMAL = 1.0; // the flow_af abnormal-coverage threshold (the pipeline's gate)

/** Perf flags for a dense line series (see the module perf contract). */
const LINE_PERF = {
  sampling: "lttb",
  triggerLineEvent: false,
} as const;

function afColor(af: number): string {
  if (af >= ABNORMAL) return SKEW_ABOVE_COLOR;
  if (af <= -ABNORMAL) return SKEW_BELOW_COLOR;
  return SKEW_NEUTRAL_COLOR;
}

/**
 * Strip the slider dataZoom entry from a built option — the stacked
 * panel's time slider lives ONLY on the top chart; the sibling charts
 * keep the inside-zoom (their zoom windows broadcast through the page's
 * shared dataZoom handler). Returns a SHALLOW COPY carrying the filtered
 * zoom list (the builders already produce fresh complete options).
 */
export function stripZoomSlider(option: EChartsOption): EChartsOption {
  if (!Array.isArray(option.dataZoom)) return option;
  return {
    ...option,
    dataZoom: option.dataZoom.filter((d) => d.type !== "slider"),
  };
}

// ---------------------------------------------------------------------------
//  Chart 1 — PRICE CONTEXT (what the metrics are read against)
// ---------------------------------------------------------------------------

export function buildPriceContextOption(
  rows: NewsTextStatesChartRow[],
  mode: ThemeMode,
): EChartsOption {
  const c = axisColors(mode);
  return baseChartOption(mode, {
    legend: null,
    tooltip: commonTooltip(mode),
    grid: { ...commonGrid({ left: 64, right: 24 }), top: 12, bottom: 30 },
    dataZoom: commonDataZoom({ xAxisIndex: 0 }),
    xAxis: {
      type: "category",
      data: rows.map((r) => r.date),
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisTick: { show: false },
      axisLabel: { color: c.textColor, fontSize: 9 },
    },
    yAxis: {
      type: "value", scale: true,
      axisLabel: { color: c.textColor, fontSize: 9 },
      splitLine: { lineStyle: { color: c.splitLineColor } },
    },
    series: [{
      name: "Close", type: "line", data: rows.map((r) => r.close),
      symbol: "none", lineStyle: { color: NEUTRAL_FILL, width: 1 },
      silent: true, // context — never participates in hover
      ...LINE_PERF,
    }],
  });
}

// ---------------------------------------------------------------------------
//  Chart 2 — MOOD (persistent + shock in σ; raw level on the right axis)
// ---------------------------------------------------------------------------

export function buildMoodOption(
  rows: NewsTextStatesChartRow[],
  mode: ThemeMode,
): EChartsOption {
  const c = axisColors(mode);
  return baseChartOption(mode, {
    legend: {
      data: ["Mood · persistent (ema10)", "Mood · shock (ema3)", "Mood · level"],
      top: 0,
    },
    tooltip: commonTooltip(mode),
    grid: { ...commonGrid({ left: 56, right: 56 }), top: 40, bottom: 30 },
    dataZoom: commonDataZoom({ xAxisIndex: 0 }),
    xAxis: {
      type: "category",
      data: rows.map((r) => r.date),
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisTick: { show: false },
      axisLabel: { color: c.textColor, fontSize: 9 },
    },
    yAxis: [
      { // σ — the state reading
        type: "value", scale: true,
        axisLabel: { color: c.textColor, fontSize: 9 },
        splitLine: { lineStyle: { color: c.splitLineColor } },
        name: "σ", nameTextStyle: { color: c.textColor, fontSize: 9 },
      },
      { // raw level — the evidence (separate unit axis, same family)
        type: "value", min: -5, max: 5,
        axisLabel: { color: c.textColor, fontSize: 9 },
        splitLine: { show: false },
        name: "level", nameTextStyle: { color: c.textColor, fontSize: 9 },
      },
    ],
    series: [
      {
        name: "Mood · persistent (ema10)", type: "line",
        yAxisIndex: 0,
        data: rows.map((r) => r.sent_ema10),
        symbol: "none", lineStyle: { color: MA60_COLOR, width: 2 },
        connectNulls: true,
        ...LINE_PERF,
        markLine: {
          silent: true, symbol: "none",
          lineStyle: { type: "dashed", color: c.axisLineColor, width: 1 },
          label: {
            formatter: "±1σ", color: c.textColor, fontSize: 9,
            position: "insideEndTop",
          },
          data: [{ yAxis: 1 }, { yAxis: -1 }],
        },
      },
      {
        name: "Mood · shock (ema3)", type: "line",
        yAxisIndex: 0,
        data: rows.map((r) => r.sent_ema3),
        symbol: "none", lineStyle: { color: MA20_COLOR, width: 1 },
        connectNulls: true,
        ...LINE_PERF,
      },
      {
        name: "Mood · level", type: "line",
        yAxisIndex: 1,
        data: rows.map((r) => r.sent_level),
        symbol: "none", lineStyle: { color: MA5_COLOR, width: 1 },
        areaStyle: { color: MA5_COLOR, opacity: 0.12 },
        connectNulls: true,
        ...LINE_PERF,
      },
    ],
  });
}

// ---------------------------------------------------------------------------
//  Chart 3 — TALK CROSS-CHECK (tone z vs mood z — agreement/disagreement)
// ---------------------------------------------------------------------------

export function buildTalkOption(
  rows: NewsTextStatesChartRow[],
  mode: ThemeMode,
): EChartsOption {
  const c = axisColors(mode);
  return baseChartOption(mode, {
    legend: {
      data: ["Talk · tone (kw)", "Mood · z (reference)"],
      top: 0,
    },
    tooltip: commonTooltip(mode),
    grid: { ...commonGrid({ left: 56, right: 24 }), top: 40, bottom: 30 },
    dataZoom: commonDataZoom({ xAxisIndex: 0 }),
    xAxis: {
      type: "category",
      data: rows.map((r) => r.date),
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisTick: { show: false },
      axisLabel: { color: c.textColor, fontSize: 9 },
    },
    yAxis: {
      type: "value", scale: true,
      axisLabel: { color: c.textColor, fontSize: 9 },
      splitLine: { lineStyle: { color: c.splitLineColor } },
      name: "σ", nameTextStyle: { color: c.textColor, fontSize: 9 },
    },
    series: [
      {
        name: "Talk · tone (kw)", type: "line",
        data: rows.map((r) => r.lex_tone_z),
        symbol: "none",
        lineStyle: { color: MA252_COLOR, width: 1.5 },
        connectNulls: true,
        ...LINE_PERF,
        markLine: {
          silent: true, symbol: "none",
          lineStyle: { type: "dashed", color: c.axisLineColor, width: 1 },
          label: {
            formatter: "±0.75", color: c.textColor, fontSize: 9,
            position: "insideEndTop",
          },
          data: [{ yAxis: 0.75 }, { yAxis: -0.75 }],
        },
      },
      {
        name: "Mood · z (reference)", type: "line",
        data: rows.map((r) => r.sent_z),
        symbol: "none",
        lineStyle: { color: MA60_COLOR, width: 1, type: "dashed", opacity: 0.7 },
        connectNulls: true,
        ...LINE_PERF,
      },
    ],
  });
}

// ---------------------------------------------------------------------------
//  Chart 4 — PARTICIPATION (AF abnormality risk flag + signed counts)
// ---------------------------------------------------------------------------

export function buildParticipationOption(
  rows: NewsTextStatesChartRow[],
  mode: ThemeMode,
): EChartsOption {
  const c = axisColors(mode);
  return baseChartOption(mode, {
    legend: {
      data: ["Flow · AF", "Flow · pos", "Flow · neg"],
      top: 0,
    },
    tooltip: commonTooltip(mode),
    grid: { ...commonGrid({ left: 56, right: 56 }), top: 40, bottom: 30 },
    dataZoom: commonDataZoom({ xAxisIndex: 0 }),
    xAxis: {
      type: "category",
      data: rows.map((r) => r.date),
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisTick: { show: false },
      axisLabel: { color: c.textColor, fontSize: 9 },
    },
    yAxis: [
      { // AF abnormality
        type: "value", scale: true,
        axisLabel: { color: c.textColor, fontSize: 9 },
        splitLine: { lineStyle: { color: c.splitLineColor } },
        name: "AF", nameTextStyle: { color: c.textColor, fontSize: 9 },
      },
      { // signed counts (supporting detail)
        type: "value", scale: true, min: 0,
        axisLabel: { color: c.textColor, fontSize: 9 },
        splitLine: { show: false },
        name: "count", nameTextStyle: { color: c.textColor, fontSize: 9 },
      },
    ],
    series: [
      {
        name: "Flow · AF", type: "bar",
        yAxisIndex: 0,
        data: rows.map((r) => ({
          value: r.flow_af,
          itemStyle: {
            color: r.flow_af == null ? SKEW_NEUTRAL_COLOR : afColor(r.flow_af),
          },
        })),
        barWidth: "60%",
        markLine: {
          silent: true, symbol: "none",
          lineStyle: { type: "dashed", color: DOWN_COLOR, width: 1, opacity: 0.7 },
          label: {
            formatter: "abnormal", color: c.textColor, fontSize: 9,
            position: "insideEndTop",
          },
          data: [{ yAxis: ABNORMAL }, { yAxis: -ABNORMAL }],
        },
      },
      {
        name: "Flow · pos", type: "line",
        yAxisIndex: 1,
        data: rows.map((r) => r.flow_pos),
        symbol: "none", lineStyle: { color: UP_COLOR, width: 1, opacity: 0.8 },
        sampling: "lttb",
        triggerLineEvent: false,
      },
      {
        name: "Flow · neg", type: "line",
        yAxisIndex: 1,
        data: rows.map((r) => r.flow_neg),
        symbol: "none", lineStyle: { color: DOWN_COLOR, width: 1, opacity: 0.8 },
        sampling: "lttb",
        triggerLineEvent: false,
      },
    ],
  });
}

// ---------------------------------------------------------------------------
//  Chart 5 — RAW MOOD (the merged-in Text Sentiment Metrics layer:
//  LLM-only Tetlock z / flow z / lexicon tone from
//  analysis.text_metrics_daily — deviations from the corpus's own
//  trailing 60-article-day history; the raw sentiment LEVEL never
//  plots)
// ---------------------------------------------------------------------------

export function buildRawMoodOption(
  rows: NewsTextStatesChartRow[],
  mode: ThemeMode,
): EChartsOption {
  const c = axisColors(mode);
  return baseChartOption(mode, {
    legend: {
      data: ["Raw · z (LLM)", "Raw · z ma5", "Raw · flow z", "Raw · kw tone"],
      top: 0,
    },
    tooltip: commonTooltip(mode),
    grid: { ...commonGrid({ left: 56, right: 24 }), top: 40, bottom: 30 },
    dataZoom: commonDataZoom({ xAxisIndex: 0 }),
    xAxis: {
      type: "category",
      data: rows.map((r) => r.date),
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisTick: { show: false },
      axisLabel: { color: c.textColor, fontSize: 9 },
    },
    yAxis: {
      type: "value", scale: true,
      axisLabel: { color: c.textColor, fontSize: 9 },
      splitLine: { lineStyle: { color: c.splitLineColor } },
      name: "σ", nameTextStyle: { color: c.textColor, fontSize: 9 },
    },
    series: [
      {
        name: "Raw · z (LLM)", type: "line",
        data: rows.map((r) => r.sentiment_z),
        symbol: "none",
        lineStyle: { color: MUTED_PALETTE[0], width: 1.1, opacity: 0.55 },
        connectNulls: true,
        ...LINE_PERF,
        markLine: {
          silent: true, symbol: "none",
          lineStyle: { type: "dashed", color: c.axisLineColor, width: 1 },
          label: {
            formatter: "0", color: c.textColor, fontSize: 9,
            position: "insideEndTop",
          },
          data: [{ yAxis: 0 }],
        },
      },
      {
        name: "Raw · z ma5", type: "line",
        data: rows.map((r) => r.sentiment_ma5),
        symbol: "none",
        lineStyle: { color: MUTED_PALETTE[0], width: 1.8 },
        connectNulls: true,
        ...LINE_PERF,
      },
      {
        name: "Raw · flow z", type: "line",
        data: rows.map((r) => r.flow_z),
        symbol: "none",
        lineStyle: { color: MUTED_PALETTE[2], width: 1.2, opacity: 0.8 },
        connectNulls: true,
        ...LINE_PERF,
      },
      {
        name: "Raw · kw tone", type: "line",
        data: rows.map((r) => r.kw_tone),
        symbol: "none",
        lineStyle: { color: MUTED_PALETTE[3], width: 1.1, opacity: 0.7, type: "dashed" },
        connectNulls: true,
        ...LINE_PERF,
      },
    ],
  });
}

// ---------------------------------------------------------------------------
//  Cross-sectional recent levels (the bottom overview bar chart)
// ---------------------------------------------------------------------------

/** The strong-negative cut (the validated reversal-event level; the
 *  list above the chart uses the same number) and its symmetric
 *  strong-positive mirror — both tails render their own color. */
const STRONG_NEG_CUT = -1.5;
const STRONG_POS_CUT = 1.5;

/**
 * buildRecentLevelsOption — the CROSS-SECTION bar chart: every sector /
 * industry scope's latest event-day reading in the recent window, one
 * bar per scope sorted most-negative first. Scopes at/below the
 * strong-neg cut (−1.5) render in the strong negative color, scopes
 * at/above the strong-pos cut (+1.5) in the strong positive color; all
 * other scopes in the light neutral fill. Dashed markLines pin both
 * cuts.
 */
export function buildRecentLevelsOption(
  rows: NewsTextStatesRecentLevelRow[],
  mode: ThemeMode,
): EChartsOption {
  const c = axisColors(mode);
  // most negative first — the highlighted bars read left-to-right
  const sorted = [...rows].sort((a, b) => a.sent_level - b.sent_level);
  return baseChartOption(mode, {
    legend: null,
    tooltip: commonTooltip(mode, {
      formatter: (params: unknown) => {
        const p = Array.isArray(params) ? params[0] : params;
        const d = (p as { data?: { meta?: NewsTextStatesRecentLevelRow } })
          ?.data?.meta;
        if (!d) return "";
        const kindLabel = d.scope_kind === 3 ? "sector" : "industry";
        return [
          `<b>${d.scope_label}</b> <span style="opacity:0.65">${d.scope_code} · ${kindLabel}</span>`,
          `level ${d.sent_level.toFixed(2)} · ${d.date}`,
          `${d.n_scored} scored (+${d.flow_pos}/−${d.flow_neg})`
            + (d.low_coverage ? " · thin" : ""),
        ].join("<br/>");
      },
    }),
    grid: { ...commonGrid({ left: 48, right: 24 }), top: 24, bottom: 72 },
    dataZoom: commonDataZoom({ xAxisIndex: 0 }),
    xAxis: {
      type: "category",
      // canonical Chinese display names (raw code when unmapped)
      data: sorted.map((r) => r.scope_label),
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor, fontSize: 9, rotate: 45, interval: 0,
      },
      axisTick: { alignWithLabel: true },
    },
    yAxis: {
      type: "value",
      name: "level",
      nameTextStyle: { color: c.textColor, fontSize: 9 },
      axisLabel: { color: c.textColor, fontSize: 9 },
      splitLine: { lineStyle: { color: c.splitLineColor } },
    },
    series: [
      {
        name: "Level", type: "bar",
        barMaxWidth: 14,
        data: sorted.map((r) => ({
          value: r.sent_level,
          itemStyle: {
            color: r.sent_level <= STRONG_NEG_CUT
              ? DOWN_COLOR
              : r.sent_level >= STRONG_POS_CUT
                ? UP_COLOR
                : NEUTRAL_FILL,
            opacity: r.sent_level <= STRONG_NEG_CUT || r.sent_level >= STRONG_POS_CUT
              ? 0.95
              : 0.45,
          },
          meta: r,
        })),
        markLine: {
          silent: true, symbol: "none",
          lineStyle: { type: "dashed", color: DOWN_COLOR, width: 1, opacity: 0.7 },
          label: {
            formatter: "strong-neg −1.5", color: c.textColor, fontSize: 9,
            position: "insideEndTop",
          },
          data: [
            { yAxis: STRONG_NEG_CUT },
            {
              yAxis: STRONG_POS_CUT,
              lineStyle: { type: "dashed", color: UP_COLOR, width: 1, opacity: 0.7 },
              label: {
                formatter: "strong-pos +1.5", color: c.textColor, fontSize: 9,
                position: "insideEndTop",
              },
            },
          ],
        },
      },
    ],
  });
}
