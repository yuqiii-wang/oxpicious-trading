/**
 * AI Ask — best-effort plot-info derivation from a built ECharts option.
 *
 * Every BaseChart gets a functional AI Ask with zero per-chart code: this
 * module reads the fully-built option (series names/types, per-series
 * numeric stats, the category x-axis window) and merges in the chart
 * author's `AiAskSpec` semantics where provided. All extraction is
 * defensive — a shape we don't understand degrades to less info, never to
 * an exception.
 */
import type { EChartsOption } from "echarts";
import type {
  AiAskPlotInfo,
  AiAskSeriesInfo,
  AiAskSeriesStat,
  AiAskSpec,
} from "./types";

function toNumber(v: unknown): number | undefined {
  return typeof v === "number" && Number.isFinite(v) ? v : undefined;
}

interface CandleDims {
  open: number;
  close: number;
  low: number;
  high: number;
}

/**
 * Parse one candlestick item — `[open, close, low, high]` or the extended
 * `[x, open, close, low, high]` (leading category index). Returns undefined
 * for anything non-numeric.
 */
function candleDims(item: readonly unknown[]): CandleDims | undefined {
  const vals = item.map(toNumber);
  const four =
    vals.length >= 5
      ? vals.slice(1, 5)
      : vals.length === 4
        ? vals
        : null;
  if (!four || four.some((v) => v === undefined)) return undefined;
  const [open, close, low, high] = four as [number, number, number, number];
  return { open, close, low, high };
}

/** Numeric stats of one series' plotted data. */
function deriveSeriesStats(
  kind: string,
  data: readonly unknown[],
): AiAskSeriesStat | undefined {
  const pool: number[] = [];
  let first: number | undefined;
  let last: number | undefined;
  let count = 0;

  const eat = (item: unknown): void => {
    const n = toNumber(item);
    if (n === undefined) return;
    pool.push(n);
    if (first === undefined) first = n;
    last = n;
    count += 1;
  };

  for (const item of data) {
    if (typeof item === "number") {
      eat(item);
    } else if (Array.isArray(item)) {
      if (kind === "candlestick") {
        const dims = candleDims(item);
        if (!dims) continue;
        pool.push(dims.low, dims.high);
        if (first === undefined) first = dims.open;
        last = dims.close;
        count += 1;
      } else {
        // Array item (e.g. [x, y] pairs): contribute every numeric element,
        // boundaries from the first / last numeric of the first / last item.
        let itemFirst: number | undefined;
        let itemLast: number | undefined;
        for (const el of item) {
          const n = toNumber(el);
          if (n === undefined) continue;
          pool.push(n);
          if (itemFirst === undefined) itemFirst = n;
          itemLast = n;
        }
        if (itemFirst !== undefined) {
          if (first === undefined) first = itemFirst;
          last = itemLast;
          count += 1;
        }
      }
    } else if (
      typeof item === "object" &&
      item !== null &&
      "value" in item
    ) {
      eat((item as { value: unknown }).value);
    }
  }

  if (count === 0) return undefined;
  return {
    count,
    first,
    last,
    min: Math.min(...pool),
    max: Math.max(...pool),
  };
}

/** First/last labels of the first category x-axis carrying data. */
function deriveWindowFromAxes(
  option: EChartsOption,
): { start?: string; end?: string } {
  const axes = option.xAxis;
  const list = Array.isArray(axes) ? axes : axes ? [axes] : [];
  for (const axis of list) {
    // Structural view — the XAXisOption union hides `data` on the
    // value-axis members even after a type === "category" narrow.
    const a = axis as { type?: unknown; data?: unknown } | string | undefined;
    if (!a || typeof a === "string" || a.type !== "category") continue;
    const raw = a.data;
    if (!Array.isArray(raw) || raw.length === 0) continue;
    const start = raw[0];
    const end = raw[raw.length - 1];
    if (
      (typeof start === "string" || typeof start === "number") &&
      (typeof end === "string" || typeof end === "number")
    ) {
      return { start: String(start), end: String(end) };
    }
  }
  return {};
}

function seriesList(option: EChartsOption): readonly unknown[] {
  const s = option.series as unknown;
  if (Array.isArray(s)) return s;
  return s !== undefined ? [s] : [];
}

/** How one value axis renders its values, probed from the label formatter. */
interface AxisValueFormat {
  /** The formatter's output carries a "%" sign — series stats print as
   *  percent (unit "%" is set on the series). */
  percent: boolean;
  /** Multiplier mapping a plotted value to what the axis DISPLAYS — 100
   *  when the formatter does `(v * 100).toFixed(2) + "%"` over fraction-
   *  encoded data (the common repo pattern), 1 when values are already
   *  percent points. Stats are scaled by this so tags speak the same
   *  numbers the chart shows. */
  scale: number;
}

const AXIS_NOT_PERCENT: AxisValueFormat = { percent: false, scale: 1 };

/** First number in a formatted label, tolerant of symbols/separators. */
function parseLabelText(out: unknown): number | undefined {
  if (typeof out !== "string") return undefined;
  const m = out.match(/-?\d[\d,]*(?:\.\d+)?/);
  if (!m) return undefined;
  const n = parseFloat(m[0].replace(/,/g, ""));
  return Number.isFinite(n) ? n : undefined;
}

/** Probe one value axis by feeding a known value (1) through the chart's
 *  own formatter: the mapping it applies to that input is exactly the
 *  mapping the raw stats need to read like the chart. String templates
 *  insert the raw value, so a "%" there means percent with scale 1. */
function probeValueAxis(axis: unknown): AxisValueFormat {
  const a = axis as
    | { type?: unknown; axisLabel?: { formatter?: unknown } }
    | string
    | undefined;
  if (!a || typeof a === "string") return AXIS_NOT_PERCENT;
  if (a.type !== undefined && a.type !== "value" && a.type !== "log") {
    return AXIS_NOT_PERCENT;
  }
  const fmt = a.axisLabel?.formatter;
  if (typeof fmt === "string") {
    return fmt.includes("%") ? { percent: true, scale: 1 } : AXIS_NOT_PERCENT;
  }
  if (typeof fmt !== "function") return AXIS_NOT_PERCENT;
  try {
    const out = (fmt as (v: number) => unknown)(1);
    // No "%" in the label → not a percent axis, whatever the scaling is
    // (e.g. "(v / 1e8).toFixed(1) + \"亿\"" amount axes).
    if (typeof out !== "string" || !out.includes("%")) return AXIS_NOT_PERCENT;
    const shown = parseLabelText(out);
    if (shown === undefined || shown === 0) return AXIS_NOT_PERCENT;
    return { percent: true, scale: shown };
  } catch {
    return AXIS_NOT_PERCENT;
  }
}

/** Per-yAxisIndex percent formats of one option's value axes. */
function valueAxisFormats(opt: EChartsOption): AxisValueFormat[] {
  const axes = opt.yAxis as unknown;
  if (axes === undefined || axes === null) return [];
  const list = Array.isArray(axes) ? axes : [axes];
  return list.map(probeValueAxis);
}

/** Is this series a shade COMPANION (one of the pos/neg stacked fills the
 *  benchmark-centered shade builder emits alongside each industry's base
 *  curve)? Signature: stacked, draws no line (opacity 0), only fills an
 *  area — it re-derives its base's data, so it would just duplicate the
 *  tag. The base (invisible line, no area) is kept. */
function isShadeCompanion(s: {
  stack?: unknown;
  lineStyle?: unknown;
  areaStyle?: unknown;
}): boolean {
  const lineStyle = s.lineStyle as { opacity?: unknown } | undefined;
  return (
    s.stack !== undefined &&
    s.stack !== null &&
    lineStyle?.opacity === 0 &&
    !!s.areaStyle
  );
}

/** Search keywords cap — the online-search seed is one line; beyond this
 *  the extra terms are noise, not signal. */
const MAX_SEARCH_KEYWORDS = 8;

/** Collect the seed's search keywords: the author's explicit tags first,
 *  then auto-derived items of interest — instrument names/codes and
 *  series names (the plotted indicators). Case-insensitively deduped;
 *  anything already contained in the chart title (most titles read
 *  "code · name") is dropped so the seed never repeats itself. */
function deriveSearchKeywords(
  title: string,
  instruments: readonly { code: string; name?: string }[],
  series: readonly { name: string }[],
  explicit: readonly string[] | undefined,
): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  const titleLower = title.toLowerCase();
  const push = (raw: string | undefined): void => {
    if (!raw) return;
    const v = raw.trim();
    if (!v || /^Series \d+$/.test(v) || v.length > 24) return;
    const key = v.toLowerCase();
    if (seen.has(key) || titleLower.includes(key)) return;
    seen.add(key);
    out.push(v);
  };
  for (const kw of explicit ?? []) push(kw);
  for (const inst of instruments) {
    push(inst.name);
    push(inst.code);
  }
  for (const s of series) push(s.name);
  return out.slice(0, MAX_SEARCH_KEYWORDS);
}

/** Merge the auto-derived plot info with the chart author's spec. */
export function derivePlotInfo(input: {
  title?: string;
  subtitle?: string;
  option?: EChartsOption | null;
  /** Additional stacked charts in the same card (multi-chart cards) —
   *  their series join the derived list; the window comes from whichever
   *  option yields a category x-axis first. */
  extraOptions?: ReadonlyArray<EChartsOption | null | undefined>;
  spec?: AiAskSpec | null;
}): AiAskPlotInfo {
  const { title, subtitle, option, extraOptions, spec } = input;
  const titleText = title ?? spec?.intro?.slice(0, 60) ?? "Chart";

  try {
    const series: AiAskSeriesInfo[] = [];
    const kinds = new Set<string>();
    const options = [option, ...(extraOptions ?? [])];
    for (const opt of options) {
      const raw = opt ? seriesList(opt) : [];
      const axisFormats = opt ? valueAxisFormats(opt) : [];
      raw.forEach((s) => {
        if (typeof s !== "object" || s === null) return;
        const obj = s as {
          name?: unknown;
          type?: unknown;
          data?: unknown;
          yAxisIndex?: unknown;
          stack?: unknown;
          lineStyle?: unknown;
          areaStyle?: unknown;
        };
        if (isShadeCompanion(obj)) return;
        // Custom (renderItem) series carry bespoke data shapes contracted
        // with their own renderer — a leading category index, OHLC field
        // order, … — which generic stats would misread (they read as
        // noise, e.g. the prev-day OHLC bar's pinned 0.0 close). Not
        // tag-worthy data series.
        if (obj.type === "custom") return;
        const name =
          typeof obj.name === "string" && obj.name !== ""
            ? obj.name
            : `Series ${series.length + 1}`;
        const kind = typeof obj.type === "string" ? obj.type : "line";
        kinds.add(kind);
        const data = Array.isArray(obj.data) ? obj.data : [];
        const stats = deriveSeriesStats(kind, data);
        // Percent-encoded axis → rescale the raw stats into the units the
        // chart actually displays (percent points) and mark the series %.
        const fmt =
          axisFormats[
            typeof obj.yAxisIndex === "number" ? obj.yAxisIndex : 0
          ] ?? AXIS_NOT_PERCENT;
        if (stats && fmt.percent && fmt.scale !== 1) {
          for (const k of ["first", "last", "min", "max"] as const) {
            const v = stats[k];
            if (v !== undefined) stats[k] = v * fmt.scale;
          }
        }
        series.push({
          name,
          kind,
          stats,
          ...(fmt.percent ? { unit: "%" } : {}),
        });
      });
    }

    // Author-provided semantics win / fill gaps, matched by series name.
    const specSeries = spec?.series ?? [];
    for (const s of series) {
      const sem = specSeries.find((c) => c.name === s.name);
      if (!sem) continue;
      if (sem.unit) s.unit = sem.unit;
      if (sem.description) s.description = sem.description;
    }

    let derivedWindow: { start?: string; end?: string } = {};
    for (const opt of options) {
      if (!opt) continue;
      derivedWindow = deriveWindowFromAxes(opt);
      if (derivedWindow.start || derivedWindow.end) break;
    }
    const specWindow = spec?.window;
    const window = {
      start: specWindow?.start ?? derivedWindow.start,
      end: specWindow?.end ?? derivedWindow.end,
      granularity: specWindow?.granularity,
    };

    const kindLabel = [...kinds].sort().join(" + ") || "chart";
    const seriesNames = series.map((s) => s.name).join(", ");
    const span =
      window.start && window.end ? ` from ${window.start} to ${window.end}` : "";
    const autoIntro =
      `${kindLabel[0].toUpperCase()}${kindLabel.slice(1)} chart with ` +
      `${series.length} series (${seriesNames})${span}.`;

    const instruments = spec?.instruments ?? [];
    return {
      chart: {
        kind: kindLabel,
        title: titleText,
        subtitle,
        intro: spec?.intro ?? autoIntro,
      },
      scope: {
        instruments,
        industry: null,
        sector: null,
      },
      window,
      series,
      state: spec?.state,
      searchKeywords: deriveSearchKeywords(
        titleText, instruments, series, spec?.searchKeywords),
      notes: spec?.notes ?? [],
      product: spec?.product,
    };
  } catch {
    // Unrecognized option shape — degrade to the minimal payload.
    return {
      chart: {
        kind: "chart",
        title: titleText,
        subtitle,
        intro: spec?.intro ?? "",
      },
      scope: { instruments: spec?.instruments ?? [], industry: null, sector: null },
      window: spec?.window ?? {},
      series: [],
      state: spec?.state,
      searchKeywords: deriveSearchKeywords(
        titleText, spec?.instruments ?? [], [], spec?.searchKeywords),
      notes: spec?.notes ?? [],
      product: spec?.product,
    };
  }
}
