/**
 * signalOverlayOption — the pure option builder of the Recent
 * Movements code-trend's CURVE overlay panel (SignalOverlayPanel).
 *
 *  buildSignalCurveOption — TWO stacked grids. Top: the CONTINUOUS
 *  depth-response view, curve(s) = mu_center + interp(fit_curve, s)
 *  per regime (analysis_signals.signal_curves, latest snapshot) over
 *  the Gaussian depth score s — the x-axis vocabulary is the FAMILY's:
 *  mov_rsi plots s = Phi^-1(1-q) of the day's percentile-rank tail
 *  probability with 1..25% band-edge guides, while bollinger (NO
 *  percentile logic — the excess σ-depth over the bands replaced it)
 *  plots s = |z| directly with its FIXED 2 / 2.5 / 3σ bars as major
 *  guides and the 0.6745σ episode-band edge; the two families share
 *  the same mu(s) machinery and axis, only the guide/band vocabulary
 *  differs. Each cell's own operating point (s_center, mu_center) is
 *  a dot — and the EMPIRICAL band curve (band_curve) drawn dashed
 *  beside it: mov_rsi per-1%-band, bollinger per-quarter-σ-band
 *  episode-weighted mean responses. The fitted shape is the POOLED
 *  episode-weighted ISOTONIC over the pass's tail-episode days,
 *  drawn ONLY within the cell's measured range [s_lo, s_hi] — no
 *  extrapolation; significance-gated flat at mu_center when the
 *  pool's |fit_t| < 2 (a non-significant shape is band noise).
 *  Bottom: the HORIZON PATH — the cell's sign-aligned mean forward
 *  change per leg (next day / 3d / 5d / 10d / 20d) beside the
 *  0.10/0.15/0.50/0.15/0.10 blend (mu_center) they feed, so the top
 *  grid's one number is readable against the path it summarizes.
 *
 *  BOTH sides are the SAME formulation, mirrored: the engine measures
 *  s from each side's own tail and sign-aligns the blended response
 *  (bottom as-is, top negated), so the curve reads "expected move in
 *  the side's own direction" — rebound for buy, decline for sell. The
 *  labels are one per-side word over one shared template
 *  (SIDE_RESPONSE_LABELS), not two different quantities. The engine
 *  pools the shape PER INDEX CLASS (stats.sec_classification
 *  industry_id — the code's own family), direction-gated by the
 *  pool's weighted Spearman (the buy side forced nondecreasing) and
 *  significance-gated (|fit_t| < 2 draws FLAT at mu_center); a flat
 *  line means "no measured depth response".
 *
 *  The top-left LEGEND KEY labels the two line styles inline — a solid
 *  sample for the fitted shape, a dashed sample for the empirical
 *  band mean (the per-regime legend entries toggle both together).
 */
import type { EChartsOption, GraphicComponentOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import { axisColors, commonGrid } from "@/theme/chart-palette";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import {
  REGIME_ACCENT_COLORS,
  REGIME_LABELS,
} from "@/shared/charts/regimeBands";
import type { MarketRegime } from "@shared/types";
import type { SignalCurve } from "@/lib/api-client";

/** The Gaussian depth score s = Phi^-1(1 - q) at the standard tail
 * buckets (deterministic math constants — the curve's x axis; the
 * grid mirrors CURVE_TAIL_PCTS — the curve is fitted over the
 * top/bottom 25% band). */
export const TAIL_S_BY_PCT: Array<{ pct: number; s: number }> = [
  { pct: 25, s: 0.6745 },
  { pct: 20, s: 0.8416 },
  { pct: 15, s: 1.0364 },
  { pct: 10, s: 1.2816 },
  { pct: 5, s: 1.6449 },
  { pct: 3, s: 1.8808 },
  { pct: 2, s: 2.0537 },
  { pct: 1, s: 2.3263 },
];

/** The curve panel's x window per family (s of the 25% tail .. past the
 *  family's deepest bar: mov_rsi past the 1% BAND CENTER 2.58;
 *  bollinger past its 3σ bar to the 0.1% Gaussian tail 3.09). */
const S_WINDOW_BY_TYPE: Record<string, { min: number; max: number }> = {
  mov_rsi: { min: 0.64, max: 2.62 },
  bollinger: { min: 0.64, max: 3.1 },
};

/** Phi^-1(p) — Acklam's rational approximation (|err| < 1.15e-9),
 *  plenty for plotting the 1%-band centers (scipy has no browser
 *  build). */
function probitInv(p: number): number {
  const a = [-39.69683028665376, 220.9460984245205, -275.9285104469687,
    138.3577518672690, -30.66479806614716, 2.506628277459239];
  const b = [-54.47609879822406, 161.5858368580409, -155.6989798598866,
    66.80131188771972, -13.28068155288572];
  const c = [-0.007784894002430293, -0.3223964580411365, -2.400758277161838,
    -2.549732539343734, 4.374664141464968, 2.938163982698783];
  const d = [0.007784695709041462, 0.3224671290700398, 2.445134137142996,
    3.754408661907416];
  const pl = 0.02425;
  let q: number;
  if (p < pl) {
    q = Math.sqrt(-2 * Math.log(p));
    return (((((c[0]! * q + c[1]!) * q + c[2]!) * q + c[3]!) * q + c[4]!) * q + c[5]!) /
      ((((d[0]! * q + d[1]!) * q + d[2]!) * q + d[3]!) * q + 1);
  }
  if (p > 1 - pl) {
    q = Math.sqrt(-2 * Math.log(1 - p));
    return -(((((c[0]! * q + c[1]!) * q + c[2]!) * q + c[3]!) * q + c[4]!) * q + c[5]!) /
      ((((d[0]! * q + d[1]!) * q + d[2]!) * q + d[3]!) * q + 1);
  }
  q = p - 0.5;
  const r = q * q;
  return (((((a[0]! * r + a[1]!) * r + a[2]!) * r + a[3]!) * r + a[4]!) * r + a[5]!) * q /
    (((((b[0]! * r + b[1]!) * r + b[2]!) * r + b[3]!) * r + b[4]!) * r + 1);
}

/** The s position of a 1% band's center: band k spans q in
 *  ((k-1)%, k%], its center q = (k-0.5)/100. */
function bandCenterS(k: number): number {
  return probitInv(1 - (k - 0.5) / 100);
}

/** The 1%-stride band EDGES (q = k% → s) — the interval grid the
 *  empirical curve is read against; MAJOR_EDGE_PCTS keep their labels
 *  (1-10% every band — the ladder where the reversion step and the
 *  continuation plateau live — plus the classic 15/20/25), the rest
 *  are faint unlabeled lines. */
const MAJOR_EDGE_PCTS = new Set([
  1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 20, 25,
]);
const BAND_EDGES = Array.from({ length: 25 }, (_, i) => {
  const k = i + 1;
  return { k, s: probitInv(1 - k / 100), major: MAJOR_EDGE_PCTS.has(k) };
});
/** Band-center s → k (exact-match lookup for the tooltip). */
const BAND_CENTER_TO_K = new Map(
  Array.from({ length: 25 }, (_, i) => [bandCenterS(i + 1).toFixed(6), i + 1]),
);


/** Linear interpolation of a sorted [s, offset] table — the stored
 *  fit_curve payload's sampler (clamps at the table edges; draw
 *  ranges stay inside the payload's coverage by construction). */
function interpFit(fit: Array<[number, number]>, s: number): number {
  if (fit.length === 0) return 0;
  if (s <= fit[0]![0]) return fit[0]![1];
  if (s >= fit[fit.length - 1]![0]) return fit[fit.length - 1]![1];
  for (let i = 1; i < fit.length; i++) {
    if (s <= fit[i]![0]) {
      const [x0, y0] = fit[i - 1]!;
      const [x1, y1] = fit[i]!;
      const f = (s - x0) / (x1 - x0 || 1);
      return y0 + f * (y1 - y0);
    }
  }
  return fit[fit.length - 1]![1];
}

/** curve(s) = mu_center + interp(fit_curve, s) — the cell's fitted
 *  response at depth s (fraction, sign-aligned). The fit_curve JSON
 *  is the pooled episode-weighted isotonic shape, offsets relative to
 *  s_center; a gated cell stores all-zero offsets (flat at
 *  mu_center). */
export function curveMu(curve: SignalCurve, s: number): number {
  const fit = Object.entries(curve.fit_curve ?? {})
    .map(([k, v]) => [Number(k), v] as [number, number])
    .sort((p1, p2) => p1[0] - p2[0]);
  return curve.mu_center + interpFit(fit, s);
}

/** The sign-aligned response's per-side word — one shared
 *  "expected ... %" template, the side's OWN direction (buy expects a
 *  rebound, sell a decline); the quantity behind both is the same
 *  mu(s). */
const SIDE_RESPONSE_LABELS: Record<"bottom" | "top", string> = {
  bottom: "expected rebound",
  top: "expected decline",
};

// ---------------------------------------------------------------------------
//  CURVE view
// ---------------------------------------------------------------------------

/** Display label per signal TAG (signal_type — the indicator family
 *  behind the curves). New families add an entry here and surface in
 *  the panel's tag selector automatically (unknown types fall back to
 *  the raw code). */
export const SIGNAL_TYPE_LABELS: Record<string, string> = {
  mov_rsi: "RSI",
  bollinger: "Bollinger",
};

/** The family-default window the panel pre-selects (mov_rsi the
 *  classic Wilder 14; bollinger the classic Bollinger 20). */
export const DEFAULT_WINDOW_BY_TYPE: Record<string, number> = {
  mov_rsi: 14,
  bollinger: 20,
};

/** The window dropdown's family word ("RSI 14" / "Boll 20"). */
export const WINDOW_LABEL_BY_TYPE: Record<string, string> = {
  mov_rsi: "RSI",
  bollinger: "Boll",
};

/** bollinger's fixed bars on the s axis — the σ multipliers its
 *  thresholds live at (the family replaced the percentile axis with
 *  the excess σ-depth over these bands; they are drawn as major
 *  guides). */
const BOLL_STD_BARS = [2, 2.5, 3];

/** The family's unit on the "depth s" axis name: bollinger's s IS the
 *  σ-depth |z| (its bars are 2/2.5/3σ); mov_rsi's s is the probit of
 *  the tail-rank probability (its guides are the % band edges).
 *  Unknown families fall back to the bare "depth s". */
const DEPTH_UNIT_BY_TYPE: Record<string, string> = {
  mov_rsi: "Φ⁻¹",
  bollinger: "σ",
};

/** The horizon-path bar grid's categories — the cell's mean forward
 *  change per leg (next day / 3d / 5d / 10d / 20d) beside the
 *  0.10/0.15/0.50/0.15/0.10 blend (mu_center) they feed, so the
 *  tooltip's one number stops being opaque. */
const HORIZON_CATEGORIES = [
  "next day", "3d", "5d", "10d", "20d", "blend",
] as const;

/** The horizon path of one cell in percent — [mu_1d, mu_3d, mu_5d,
 *  mu_10d, mu_20d, mu_center]; null legs render as a missing bar. */
function horizonPath(cv: SignalCurve): Array<number | null> {
  return [
    cv.mu_1d, cv.mu_3d, cv.mu_5d, cv.mu_10d, cv.mu_20d, cv.mu_center,
  ].map((v) => (v == null ? null : Number((v * 100).toFixed(4))));
}

/** "+0.42%" / "−1.20%" / "—" for a fraction leg. */
function fmtLeg(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(2)}%`;
}

export function buildSignalCurveOption(
  curves: SignalCurve[],
  signalType: string,
  rsiWindow: number,
  side: "bottom" | "top",
  mode: ThemeMode,
): EChartsOption {
  const c = axisColors(mode);
  // bollinger's x axis is the σ-depth itself (s = |z|): its empirical
  // band_curve keys are quarter-σ deeper edges, its bars the fixed σ
  // multipliers — everything renders on the same s axis, only the
  // guide/band vocabulary differs from mov_rsi's percentile grid.
  const isBoll = signalType === "bollinger";
  const win = S_WINDOW_BY_TYPE[signalType] ?? S_WINDOW_BY_TYPE.mov_rsi!;
  const S_MIN = win.min;
  const S_MAX = win.max;
  const S_STEP = 0.02;
  const cell = curves.filter(
    (cv) =>
      cv.signal_type === signalType &&
      cv.rsi_window === rsiWindow &&
      cv.side === side,
  );
  // the interval grid: mov_rsi — a faint line at EVERY 1% band edge
  // (q = 1%..25%), the classic tail pcts labeled; bollinger — its fixed
  // σ bars (2 / 2.5 / 3σ) + the episode-band edge 0.6745σ as MAJOR
  // labeled guides, the intervening quarter-σ edges faint unlabeled
  const guides = (
    isBoll
      ? [
          { s: 0.6745, label: "band" },
          ...BOLL_STD_BARS.map((k) => ({ s: k, label: `${k}σ` })),
          ...[1.0, 1.25, 1.5, 1.75, 2.25, 2.75].map((s) => ({
            s, label: null,
          })),
        ]
      : BAND_EDGES.map(({ k, s, major }) => ({
          s, label: major ? `${k}%` : null,
        }))
  ).map(({ s, label }) => ({
    xAxis: s,
    lineStyle: {
      color: c.splitLineColor,
      type: "dashed" as const,
      width: label != null ? 1.2 : 0.5,
      opacity: label != null ? 1 : 0.45,
    },
    label:
      label != null
        ? {
            formatter: label,
            position: "insideTopRight" as const,
            fontSize: 9,
            color: c.textColor,
          }
        : { show: false },
  }));
  // the EMPIRICAL band dot's x: mov_rsi — the 1% band's center
  // q = (k−0.5)/100 → s; bollinger — the quarter-σ band's center
  // (key − 0.125)σ
  const bandCenter = (key: string | number): number =>
    isBoll ? Number(key) - 0.125 : bandCenterS(Number(key));
  const regimeName = (cv: SignalCurve) =>
    REGIME_LABELS[cv.regime_state as MarketRegime] ?? cv.regime_state;
  const byRegime = new Map(
    cell.map((cv) => [regimeName(cv), cv] as const),
  );
  const lineSeries = cell.map((cv) => {
    // the fitted shape draws ONLY over the cell's measured range
    // [s_lo, s_hi] — beyond it the shape is unmeasured (no
    // extrapolation; the empirical band dots carry the picture)
    const lo = Math.max(S_MIN, cv.s_lo);
    const hi = Math.min(S_MAX, cv.s_hi);
    const pts: Array<[number, number]> = [];
    for (let s = lo; s <= hi + 1e-9; s += S_STEP) {
      pts.push([Number(s.toFixed(3)), Number((curveMu(cv, s) * 100).toFixed(4))]);
    }
    if (pts.length === 0 || pts[pts.length - 1]![0] < hi - 1e-9) {
      pts.push([Number(hi.toFixed(3)), Number((curveMu(cv, hi) * 100).toFixed(4))]);
    }
    return {
      name: regimeName(cv),
      type: "line" as const,
      xAxisIndex: 0,
      yAxisIndex: 0,
      data: pts,
      showSymbol: false,
      lineStyle: { width: 1.6, color: REGIME_ACCENT_COLORS[cv.regime_state as MarketRegime] },
      itemStyle: { color: REGIME_ACCENT_COLORS[cv.regime_state as MarketRegime] },
      // the cell's own operating point: mu at its empirical depth center
      markPoint: {
        symbol: "circle",
        symbolSize: 7,
        label: { show: false },
        data: [
          {
            coord: [
              cv.s_center,
              Number((curveMu(cv, cv.s_center) * 100).toFixed(4)),
            ],
            itemStyle: {
              color: REGIME_ACCENT_COLORS[cv.regime_state as MarketRegime],
            },
          },
        ],
      },
      markLine: { silent: true, symbol: "none", data: guides },
    };
  });
  // the EMPIRICAL band curve (band_curve): dashed thin line + dots at
  // each band's center. mov_rsi — the 1%-stride tail bands; bollinger —
  // the quarter-σ stride bands. Same series name as the fit so one
  // legend entry toggles both — the dots ARE the data the fitted line
  // summarizes.
  const bandSeries = cell.map((cv) => {
    const data = Object.entries(cv.band_curve ?? {})
      .map(([k, v]) => [bandCenter(k), Number(v) * 100])
      .sort((p1, p2) => p1[0]! - p2[0]!);
    return {
      name: regimeName(cv),
      type: "line" as const,
      xAxisIndex: 0,
      yAxisIndex: 0,
      data,
      showSymbol: true,
      symbolSize: 4,
      connectNulls: true,
      lineStyle: {
        width: 1,
        type: "dashed" as const,
        opacity: 0.7,
        color: REGIME_ACCENT_COLORS[cv.regime_state as MarketRegime],
      },
      itemStyle: {
        color: REGIME_ACCENT_COLORS[cv.regime_state as MarketRegime],
        opacity: 0.7,
      },
    };
  });
  const barSeries = cell.map((cv) => ({
    name: regimeName(cv),
    type: "bar" as const,
    xAxisIndex: 1,
    yAxisIndex: 1,
    data: horizonPath(cv),
    itemStyle: { color: REGIME_ACCENT_COLORS[cv.regime_state as MarketRegime] },
    barMaxWidth: 13,
  }));
  // the LEGEND KEY (top-left, right of the y-axis name, left of the
  // regime legend): the depth grid's two line styles labeled inline —
  // the solid fitted μ(s) and the dashed empirical band mean — one row
  // each, echoing the real series styles (width / dash) in neutral
  // text color.
  const legendKey: GraphicComponentOption[] = [
    {
      type: "line",
      left: 150,
      top: 9,
      shape: { x1: 0, y1: 0, x2: 14, y2: 0 },
      style: { stroke: c.textColor, lineWidth: 1.6 },
    },
    {
      type: "text",
      left: 169,
      top: 3.5,
      style: { text: "fitted μ(s) (measured range)", fill: c.textColor, fontSize: 9 },
    },
    {
      type: "line",
      left: 150,
      top: 22,
      shape: { x1: 0, y1: 0, x2: 14, y2: 0 },
      style: { stroke: c.textColor, lineWidth: 1, opacity: 0.7, lineDash: [4, 3] },
    },
    {
      type: "text",
      left: 169,
      top: 16.5,
      style: { text: "empirical 1%-band mean", fill: c.textColor, fontSize: 9 },
    },
  ];
  return baseChartOption(mode, {
    grid: [
      commonGrid({ top: 34, height: "46%" }),
      commonGrid({ top: "63%", bottom: 32 }),
    ],
    tooltip: commonTooltip(mode, {
      formatter: (params) => {
        const arr = Array.isArray(params) ? params : [params];
        if (arr.length === 0 || !arr[0]) return "";
        const p = arr[0] as (typeof arr)[number] & { seriesType?: string };
        if (p.seriesType === "bar") {
          // the horizon grid: one row per regime at that leg
          const rows = arr
            .map((q) => {
              const v = typeof q.value === "number" ? q.value : null;
              return `${q.marker}${q.seriesName} ${fmtLeg(v == null ? null : v / 100)}`;
            })
            .join("<br/>");
          return `<b>${String(p.axisValue ?? "")}</b><br/>${rows}`;
        }
        // the depth grid: the empirical band dots and the fitted line
        // rows, each labeled for what it is
        const withS = arr.find((q) => Array.isArray(q.data)) as
          | { data: [number, number] }
          | undefined;
        if (!withS) return "";
        const [s] = withS.data;
        const nearest = TAIL_S_BY_PCT.reduce((a, b) =>
          Math.abs(b.s - s) < Math.abs(a.s - s) ? b : a,
        );
        // the band-dot interval label: mov_rsi keys the 1..25 pct
        // bands by their exact center s; bollinger keys the quarter-σ
        // bands by their deeper edge (center = edge − 0.125)
        const bandNote = (x: number): string | null => {
          if (isBoll) {
            const edge = Math.round((x + 0.125) * 1000) / 1000;
            const lo = Math.round((edge - 0.25) * 100) / 100;
            if (Math.abs(edge * 1000 - Math.round(edge * 1000)) > 1e-6) {
              return null;
            }
            return `band (${lo}, ${edge}]σ`;
          }
          const bandK = BAND_CENTER_TO_K.get(x.toFixed(6));
          return bandK != null ? `band (${bandK - 1},${bandK}]%` : null;
        };
        const rows = arr
          .map((q) => {
            if (!Array.isArray(q.data)) return "";
            const [x, v] = q.data as [number, number];
            const cv = byRegime.get(String(q.seriesName));
            const note = bandNote(x);
            if (note != null) {
              // an empirical band dot — its OWN interval, exact
              return `${q.marker}${q.seriesName} <b>${v >= 0 ? "+" : ""}${v.toFixed(2)}%</b> ` +
                `<span style="opacity:0.72">empirical ${note}</span>`;
            }
            const path = cv
              ? `<br/><span style="opacity:0.72">next ${fmtLeg(cv.mu_1d)} · 3d ${fmtLeg(cv.mu_3d)} · 5d ${fmtLeg(cv.mu_5d)} · 10d ${fmtLeg(cv.mu_10d)} · 20d ${fmtLeg(cv.mu_20d)}</span>`
              : "";
            const flatNote = cv && Math.abs(cv.fit_t) < 2
              ? ` <span style="opacity:0.72">(flat — no significant depth response, fit_t ${cv.fit_t.toFixed(2)})</span>`
              : "";
            return `${q.marker}${q.seriesName} fitted <b>${v >= 0 ? "+" : ""}${v.toFixed(2)}%</b>${flatNote}${path}`;
          })
          .filter(Boolean)
          .join("<br/>");
        const sNote = isBoll
          ? `≈ ${s.toFixed(2)}σ depth`
          : `≈ ${nearest.pct}% tail`;
        return (
          `<b>depth s = ${s.toFixed(2)} (${sNote})</b> ` +
          `<span style="opacity:0.72">· ${SIDE_RESPONSE_LABELS[side]}</span><br/>${rows}`
        );
      },
    }),
    xAxis: [
      {
        type: "value",
        gridIndex: 0,
        min: S_MIN,
        max: S_MAX,
        // the family's unit rides ON the axis name ("depth s (σ)" /
        // "depth s (Φ⁻¹)"), centered under the tick labels — at the
        // right axis end (nameLocation end) the longer name clipped
        // at the canvas edge
        name: DEPTH_UNIT_BY_TYPE[signalType] != null
          ? `depth s (${DEPTH_UNIT_BY_TYPE[signalType]})`
          : "depth s",
        nameLocation: "middle",
        nameGap: 22,
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: { color: c.textColor, fontSize: 9 },
      },
      {
        type: "category",
        gridIndex: 1,
        data: [...HORIZON_CATEGORIES],
        name: "horizon (cell means)",
        nameLocation: "middle",
        nameGap: 20,
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisTick: { show: false },
        axisLabel: { color: c.textColor, fontSize: 9 },
      },
    ],
    yAxis: [
      {
        type: "value",
        gridIndex: 0,
        name: `${SIDE_RESPONSE_LABELS[side]} %`,
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => `${v.toFixed(1)}%`,
        },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
      {
        type: "value",
        gridIndex: 1,
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => `${v.toFixed(1)}%`,
        },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
    ],
    series: [...lineSeries, ...bandSeries, ...barSeries],
    graphic: { elements: legendKey },
  });
}
