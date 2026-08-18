/**
 * Prev-Day OHLC bar for the Market Movements top plot.
 *
 * Renders ONE classic OHLC BAR (not a candlestick — no filled body) at a
 * single x-axis category prepended BEFORE the 09:30 tick:
 *
 *        │            ← high (top of the vertical stem)
 *     ───┤            ← open  (LEFT tick)
 *        │
 *        ├───         ← close (RIGHT tick — ALWAYS at 0.0%)
 *        │            ← low   (bottom of the stem)
 *
 * Y-space: FRACTIONS vs the entry's own prev-day close, so the bar shares
 * the top plot's "% change vs prev close" y-axis — yesterday's close sits
 * exactly at 0.0, the SAME reference today's intraday ticks are measured
 * against. The 0.0 pin is POSITION-ONLY: the bar's COLOR comes from the
 * prev day's OWN open→close direction in RAW prices (green when close >=
 * open, red when close < open) — never from comparing anything to 0.0.
 *
 * Rendered THICK (stem/ticks lineWidth 3, 13px tick width) — this style is
 * local to this Market Movements plot and does not affect other charts.
 *
 * Selection hierarchy (driven by clicks on the middle/bottom plots):
 *   clicked member index → that index's OHLC bar
 *   else clicked industry → industry bar = MEAN of member %s (equal-weight,
 *     identical semantics to industry_price_pct — each member's open/close-1,
 *     high/close-1, low/close-1 are averaged; industry close% = 0 by
 *     construction)
 *   else (nothing clicked) → the benchmark's own OHLC bar (DEFAULT)
 *
 * Industry aggregation is done in % space (NOT raw-price space): indices
 * have wildly different price levels (4000 vs 1000), so a raw mean of
 * opens would let high-priced indices dominate. Mean-of-%s matches the
 * page's existing equal-weight aggregation exactly.
 */
import type { CustomSeriesOption } from "echarts";
import { UP_COLOR, DOWN_COLOR } from "@/theme/chart-palette";
import type {
  PrevDayOhlcEntry,
  PrevDayOhlcMember,
  PrevDayOhlcResponse,
} from "@shared/types";

/** x-axis category label of the prepended prev-day tick: "MM-DD" of the
 *  prev trading day (e.g. "08-17"). Falls back to "Prev" when unknown. */
export function prevDayTickLabel(entry: { date: string } | null): string {
  if (!entry?.date) return "Prev";
  return entry.date.length >= 10 ? entry.date.slice(5, 10) : entry.date;
}

/** One OHLC bar fully resolved for plotting (all FRACTIONS, close = 0). */
export interface PrevDayOhlcBar {
  /** Display name (benchmark / industry label / index name). */
  label: string;
  /** Prev trading day (YYYY-MM-DD) — shown in the tooltip. */
  date: string;
  openPct: number;
  highPct: number;
  lowPct: number;
  closePct: number;
  /** Direction of the prev day's OWN open→close move, decided in RAW
   *  prices at build time (true = close >= open → green). Independent of
   *  the closePct = 0.0 position pin — do NOT re-derive the color from
   *  the 0.0 line. */
  upDay: boolean;
}

function _pct(v: number | null, close: number | null): number | null {
  if (v == null || close == null || close === 0) return null;
  return v / close - 1;
}

/** Convert one raw OHLC entry to the % space (close → 0.0). Null when any
 *  field is missing. */
export function toOhlcBar(
  label: string,
  entry: PrevDayOhlcEntry | null,
): PrevDayOhlcBar | null {
  if (!entry) return null;
  const o = _pct(entry.open, entry.close);
  const h = _pct(entry.high, entry.close);
  const l = _pct(entry.low, entry.close);
  if (o == null || h == null || l == null) return null;
  return {
    label,
    date: entry.date,
    openPct: o,
    highPct: h,
    lowPct: l,
    closePct: 0,
    // Color decided from the RAW prev-day open vs close — never from the
    // 0.0 pin (open/close are non-null here because o/h/l all resolved).
    upDay: entry.close >= entry.open,
  };
}

/** Industry bar = MEAN of member %s (equal-weight, % space). Each member
 *  contributes open/close-1, high/close-1, low/close-1 (its own close as
 *  base); close% = 0 by construction. Null when no member has full OHLC. */
export function aggregateIndustryOhlcBar(
  label: string,
  members: PrevDayOhlcMember[],
): PrevDayOhlcBar | null {
  const bars = members
    .map((m) => toOhlcBar(m.code_name || m.code, m))
    .filter((b): b is PrevDayOhlcBar => b !== null);
  if (bars.length === 0) return null;
  const mean = (f: (b: PrevDayOhlcBar) => number) =>
    bars.reduce((s, b) => s + f(b), 0) / bars.length;
  const openPctMean = mean((b) => b.openPct);
  return {
    label,
    date: members[0].date,
    openPct: openPctMean,
    highPct: mean((b) => b.highPct),
    lowPct: mean((b) => b.lowPct),
    closePct: 0,
    // The aggregate bar's own open→close direction: its "close" is 0 by
    // construction, so its open→close is decreasing iff mean open% > 0 —
    // the equal-weight aggregate of the members' raw open→close moves.
    upDay: openPctMean <= 0,
  };
}

/** Resolve the ACTIVE prev-day bar by selection hierarchy:
 *  member index > industry > benchmark (default). */
export function resolvePrevDayBar(
  resp: PrevDayOhlcResponse | null,
  opts: {
    benchmarkName: string;
    selectedMemberCode: string | null;
    selectedIndustryId: string | null;
    industryLabelById: Map<string, string>;
  },
): PrevDayOhlcBar | null {
  if (!resp) return null;

  // 1) Clicked member index (bottom plot) → that index's own bar.
  if (opts.selectedMemberCode) {
    const m = resp.members.find((x) => x.code === opts.selectedMemberCode);
    if (m) {
      const bar = toOhlcBar(m.code_name || m.code, m);
      if (bar) return bar;
    }
  }

  // 2) Clicked industry (middle plot) → equal-weight mean-of-%s bar.
  if (opts.selectedIndustryId) {
    const members = resp.members.filter(
      (x) => x.industry_id === opts.selectedIndustryId,
    );
    if (members.length > 0) {
      const label =
        opts.industryLabelById.get(opts.selectedIndustryId) ??
        opts.selectedIndustryId;
      const bar = aggregateIndustryOhlcBar(label, members);
      if (bar) return bar;
    }
  }

  // 3) Default: the benchmark's own bar.
  return toOhlcBar(opts.benchmarkName, resp.benchmark);
}

/** Legend/series name of the prev-day OHLC custom series (shared by the
 *  series builder and the option's legend data so they never drift). */
export function prevDayOhlcSeriesName(label: string): string {
  return `Prev-Day OHLC · ${label}`;
}

/** Build the ECharts CUSTOM series that draws the OHLC bar at x category
 *  index 0 (the prepended prev-day tick).
 *
 *  Data item: [xIdx=0, highPct, lowPct, openPct, closePct] — classic OHLC
 *  bar rendering: vertical stem low→high, LEFT tick at open, RIGHT tick at
 *  close (0.0). */
export function buildOhlcBarSeries(
  bar: PrevDayOhlcBar,
  tickWidthPx = 13,
): CustomSeriesOption {
  // Color from the prev day's OWN open→close direction (raw prices) —
  // close stays pinned at 0.0 for POSITION only, never for color.
  const color = bar.upDay ? UP_COLOR : DOWN_COLOR;
  return {
    name: prevDayOhlcSeriesName(bar.label),
    type: "custom",
    data: [[0, bar.highPct, bar.lowPct, bar.openPct, bar.closePct]],
    z: 12,
    silent: false,
    // Params/api are contextually typed by CustomSeriesOption.
    renderItem: (params, api) => {
      const x = api.coord([api.value(0), 0])[0];
      const yOf = (v: number) => api.coord([params.dataIndex, v])[1];
      const highY = yOf(Number(api.value(1)));
      const lowY = yOf(Number(api.value(2)));
      const openY = yOf(Number(api.value(3)));
      const closeY = yOf(Number(api.value(4)));
      // THICK strokes — Market Movements plot only (this builder is not
      // shared with any other chart).
      const stroke = { stroke: color, lineWidth: 3 };
      return {
        type: "group",
        children: [
          // Vertical stem: low → high.
          {
            type: "line",
            shape: { x1: x, y1: highY, x2: x, y2: lowY },
            style: stroke,
          },
          // LEFT tick at open.
          {
            type: "line",
            shape: { x1: x - tickWidthPx, y1: openY, x2: x, y2: openY },
            style: stroke,
          },
          // RIGHT tick at close (always 0.0%).
          {
            type: "line",
            shape: { x1: x, y1: closeY, x2: x + tickWidthPx, y2: closeY },
            style: stroke,
          },
        ],
      };
    },
  };
}

/** Tooltip HTML for the prev-day tick (idx === 0).
 *
 *  TOOLTIP-ONLY REBASING: unlike the bar's y-space (fractions vs the
 *  prev day's close, close pinned at 0.0 to share the plot's y-axis),
 *  the tooltip numbers are rebased to the prev day's OPEN — open shows
 *  0.00% by definition, H/L/C show x/open − 1. Conversion from the
 *  bar's close-based fractions: x/open = (1 + xPct) / (1 + openPct) − 1
 *  (openPct > −1 always, since open > 0). Only this tooltip is rebased;
 *  the bar rendering keeps the plot's "% vs prev close" axis base. */
export function formatPrevDayOhlcTooltip(bar: PrevDayOhlcBar): string {
  const pct = (v: number, sign = true) =>
    (sign && v >= 0 ? "+" : "") + (v * 100).toFixed(2) + "%";
  // Same rule as the bar strokes: prev day's own open→close direction.
  const color = bar.upDay ? UP_COLOR : DOWN_COLOR;
  const row = (name: string, v: number) =>
    `<div>${name}: <b style="color:${color}">${pct(v)}</b></div>`;
  const rel = (v: number) => (1 + v) / (1 + bar.openPct) - 1;
  return (
    `<div style="font-weight:600">${bar.label} · prev day ${bar.date}</div>` +
    `<div style="margin-top:2px;opacity:0.85">OHLC vs prev open</div>` +
    row("O", 0) +
    row("H", rel(bar.highPct)) +
    row("L", rel(bar.lowPct)) +
    row("C", rel(bar.closePct))
  );
}
