/**
 * Quantitative chart palette — ported from _plot_commons.py so ECharts colors
 * match the matplotlib PNG outputs exactly.
 *
 * The actual color VALUES live in `colors.css` (single source of truth). This
 * module resolves them at runtime via `getComputedStyle` so that canvas-
 * rendered ECharts (which cannot consume CSS variables directly) stay in sync
 * with the CSS. Every `cssVar()` call carries a fallback equal to the CSS
 * value, so the palette remains correct even if the stylesheet has not loaded
 * yet at module-eval time.
 */
import type { ThemeMode } from "@/store/filters";

/**
 * Resolve a CSS custom property to its current value.
 * Returns the fallback when the variable is unset or the DOM is unavailable
 * (e.g. during SSR / unit tests).
 */
function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  // getComputedStyle returns "" for unknown vars and the raw token (incl. the
  // leading "--") only if mis-declared; accept anything that looks like a color.
  return v || fallback;
}

// ----------------------------------------------------------------------------
// Chart series colors
// ----------------------------------------------------------------------------
export const UP_COLOR = cssVar("--chart-up", "#27ae60"); // price-up green (also RZ margin fill)
export const DOWN_COLOR = cssVar("--chart-down", "#c0392b"); // price-down red (also RQ margin fill)
export const PALETTE_HI = cssVar("--chart-hi", "#2980b9"); // rebased-close line blue
export const NEUTRAL_FILL = cssVar("--chart-neutral-fill", "#95a5a6"); // neutral gray for price-area fill
export const MA5_COLOR = cssVar("--chart-ma5", "#e91e63"); // MA5 magenta
export const MA20_COLOR = cssVar("--chart-ma20", "#f39c12"); // MA20 orange
export const MA60_COLOR = cssVar("--chart-ma60", "#8e44ad"); // MA60 purple
export const MA120_COLOR = cssVar("--chart-ma120", "#7f8c8d"); // MA120 gray
export const MA255_COLOR = cssVar("--chart-ma255", "#16a085"); // MA255 teal

// Quant / IV-smile colors
export const IV_BLUE = cssVar("--chart-iv-blue", "#1F77B4"); // standard tableau blue
export const ATM_GRAY = cssVar("--chart-atm-gray", "#7F7F7F"); // neutral gray for ATM vertical line
export const MARKER_EDGE_GRAY = cssVar("--chart-marker-edge-gray", "#444444");

// Valuation — PE ratio (distinct from Close/IV_BLUE)
export const PE_COLOR = cssVar("--chart-pe", "#e377c2"); // tableau pink — stands apart from blue/orange/purple MA lines

// Spot / max pain / OI-weighted
export const SPOT_COLOR = cssVar("--chart-spot", "#2C3E50");

// Bollinger band envelope (±k×σ around an MA). Used on the MA-Spread page
// to draw the upper/lower band lines and the faint fill between them.
export const BOLL_BAND_COLOR = cssVar("--chart-boll-band", "#3498db"); // soft blue
export const BOLL_BAND_FILL = cssVar("--chart-boll-fill", "#3498db");  // same hue, opacity applied at use site

// Corporate-action event markers (dividends / splits)
export const DIVIDEND_COLOR = cssVar("--chart-dividend", "#f1c40f"); // gold — dividend marker
export const SPLIT_COLOR = cssVar("--chart-split", "#16a085"); // teal — split/conversion marker

// ----------------------------------------------------------------------------
// Muted multi-series palette (Tableau-10 desaturated — for multi-expiry/date)
// ----------------------------------------------------------------------------
export const MUTED_PALETTE = [
  cssVar("--chart-muted-1", "#4C78A8"), // muted blue
  cssVar("--chart-muted-2", "#F58518"), // muted orange
  cssVar("--chart-muted-3", "#54A24B"), // muted green
  cssVar("--chart-muted-4", "#E45756"), // muted red
  cssVar("--chart-muted-5", "#72B7B2"), // muted teal
  cssVar("--chart-muted-6", "#B279A2"), // muted purple
  cssVar("--chart-muted-7", "#FF9DA6"), // muted pink
  cssVar("--chart-muted-8", "#9D755D"), // muted brown
];

// ----------------------------------------------------------------------------
// UI text tokens
// ----------------------------------------------------------------------------
/** Card subtitle / muted label gray (used in DOM styles). */
export const SUBTITLE_COLOR = cssVar("--chart-subtitle", "#7A8190");
/** Fallback inline gray (autocomplete hints, secondary axis names). */
export const MUTED_INLINE_COLOR = cssVar("--chart-muted-inline", "#888888");

// ----------------------------------------------------------------------------
// Axis / grid / tooltip theming (light + dark)
// ----------------------------------------------------------------------------
export interface AxisColors {
  axisLineColor: string;
  splitLineColor: string;
  textColor: string;
  tooltipBg: string;
}

/**
 * Shared legend config — positions legend at top-right with enough clearance
 * so it never overlaps y-axis names/labels. Callers should pair this with
 * `commonGrid()` to ensure the plot area starts below the legend.
 *
 * The legend is positioned 4px from the top edge of the chart container,
 * and the grid should have top >= 30 to leave room for the legend height
 * (~20px) plus the y-axis name (~12px).
 */
export function commonLegend(
  mode: ThemeMode,
  extra: Partial<import("echarts").LegendComponentOption> = {},
): import("echarts").LegendComponentOption {
  const c = axisColors(mode);
  return {
    type: "scroll",
    top: 4,
    right: 8,
    textStyle: { color: c.textColor, fontSize: 9 },
    itemWidth: 10,
    itemHeight: 6,
    itemGap: 15,
    ...extra,
  };
}

/**
 * Shared grid config — positions the plot area with enough margin to
 * accommodate a legend at the top-right and axes on both sides.
 *
 * Default: left=56, right=56, top=32, bottom=32
 * The top=32 ensures the legend (~20px at top) + y-axis name (~12px)
 * sit comfortably above the plot area without overlapping.
 */
export function commonGrid(
  overrides: Partial<import("echarts").GridComponentOption> = {},
): import("echarts").GridComponentOption {
  return {
    left: 56,
    right: 56,
    top: 32,
    bottom: 32,
    ...overrides,
  };
}

/**
 * Resolve the axis/grid/tooltip color set for the given theme mode. Replaces
 * the `axisColors()` / `getAxisColors()` helpers that were duplicated across
 * ~8 chart components.
 */
export function axisColors(mode: ThemeMode): AxisColors {
  if (mode === "dark") {
    return {
      axisLineColor: cssVar("--chart-axis-line-dark", "#3A445C"),
      splitLineColor: cssVar("--chart-split-line-dark", "#1F2740"),
      textColor: cssVar("--chart-text-dark", "#9AA4B8"),
      tooltipBg: cssVar("--chart-tooltip-bg-dark", "#1A2238"),
    };
  }
  return {
    axisLineColor: cssVar("--chart-axis-line-light", "#9AA4B8"),
    splitLineColor: cssVar("--chart-split-line-light", "#E1E5EC"),
    textColor: cssVar("--chart-text-light", "#5A6273"),
    tooltipBg: cssVar("--chart-tooltip-bg-light", "#FFFFFF"),
  };
}

// ----------------------------------------------------------------------------
// Repo / debt baseline colors
// ----------------------------------------------------------------------------
export const OMO_RATE_COLOR = IV_BLUE;
export const REPO_START_COLOR = UP_COLOR;
export const REPO_END_COLOR = DOWN_COLOR;
export const CUMULATIVE_COLOR = MUTED_PALETTE[5];

// SHIBOR tenors (column, label, color)
export const SHIBOR_SERIES = [
  { col: "shibor_o_n", label: "O/N", color: MUTED_PALETTE[0] },
  { col: "shibor_1w", label: "1W", color: MUTED_PALETTE[1] },
  { col: "shibor_1m", label: "1M", color: MUTED_PALETTE[2] },
  { col: "shibor_3m", label: "3M", color: MUTED_PALETTE[3] },
  { col: "shibor_6m", label: "6M", color: MUTED_PALETTE[4] },
  { col: "shibor_1y", label: "1Y", color: MUTED_PALETTE[5] },
] as const;

// ChinaBond tenors
export const CHINABOND_SERIES = [
  { col: "cb_1y", label: "1Y", color: MUTED_PALETTE[0] },
  { col: "cb_5y", label: "5Y", color: MUTED_PALETTE[2] },
  { col: "cb_10y", label: "10Y", color: MUTED_PALETTE[4] },
  { col: "cb_30y", label: "30Y", color: MUTED_PALETTE[6] },
] as const;

// PBoC LPR tenors (monthly announcement; 1Y + 5Y+)
export const LPR_SERIES = [
  { col: "lpr_1y", label: "1Y LPR", color: MUTED_PALETTE[0] },
  { col: "lpr_5y", label: "5Y+ LPR", color: MUTED_PALETTE[1] },
] as const;

export const MAX_PAIN_COLOR = MA60_COLOR;
export const OI_WEIGHTED_COLOR = MA20_COLOR;

// Underlying display names (for options dashboard)
export const UNDERLYING_LABELS: Record<string, string> = {
  "159901": "深证100ETF",
  "159915": "创业板ETF",
  "159919": "沪深300ETF",
  "159922": "中证500ETF",
};

// Constants matching plot_szse_options.py
export const CONTRACT_SIZE = 10000;
export const PRICE_SCALE = 1000.0; // 厘 → yuan
export const OPT_SCALE = 10000.0; // 元/张 → 元/份
export const RISK_FREE_RATE = 0.02;
