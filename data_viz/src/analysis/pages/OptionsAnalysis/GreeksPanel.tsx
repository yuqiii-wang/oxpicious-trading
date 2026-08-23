/**
 * Greeks panel — shows Delta, Theta, Gamma, Vega, Rho for option contracts.
 *
 * Displays per-expiry Greek values vs moneyness (strike/spot) for CALL and PUT,
 * grouped by expiry month.
 */
import React from "react";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import { useStore } from "@/store/filters";
import type { OptionsRow } from "@shared/types";
import {
  PRICE_SCALE,
  axisColors,
  commonGrid,
  commonLegend,
  expiryBlueColor,
  skewLineColor,
  SKEW_LINE_WIDTH,
} from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import { renderReactElement, tooltipComponents } from "@/lib/react-tooltip-renderer";
import type { EChartsOption } from "echarts";

interface Props {
  rows: OptionsRow[];
  selectedDate: string;
  greekKey: "delta" | "theta" | "gamma" | "vega" | "rho";
}

const GREEK_LABELS: Record<string, string> = {
  delta: "Delta (Δ)",
  theta: "Theta (Θ)",
  gamma: "Gamma (Γ)",
  vega: "Vega (ν)",
  rho: "Rho (ρ)",
};

const GREEK_UNITS: Record<string, string> = {
  delta: "",
  theta: " per day",
  gamma: " per 1% price",
  vega: " per 1% IV",
  rho: " per 1% rate",
};

/** Greeks with an industry-standard positioning metric (line rendered). */
type GreekMetricKey = "delta" | "gamma" | "vega";

interface GreekMetricMeta {
  /** Short tag for the chart line label, e.g. "dPCR". */
  tag: string;
  /** Neutral (no-tilt) anchor of the metric. */
  neutral: number;
  /** ECharts series name (excluded from the visible legend). */
  seriesName: string;
  /** Formula line for the tooltip. */
  formula: string;
  /** Interpretation of the current value for the tooltip. */
  direction: (v: number) => string;
}

// Mirrors the backend pair-level metrics (analyze/options/compute/
// greek_delta.py / greek_gamma.py / greek_vega.py) — see those modules
// for the industry anchors (PCR refinement / GEX dealer-sign convention /
// 25Δ risk-reversal OI mirror).
const GREEK_METRIC_META: Record<GreekMetricKey, GreekMetricMeta> = {
  delta: {
    tag: "dPCR",
    neutral: 0.5,
    seriesName: "Greek dPCR",
    formula: "dpcr = Σ OI·|Δ| (puts) / Σ OI·|Δ| (all) — whole chain",
    direction: (v) =>
      v > 0.5 + 1e-4
        ? "put-side directional exposure dominates (bearish / hedged book)"
        : v < 0.5 - 1e-4
          ? "call-side directional exposure dominates (bullish book)"
          : "balanced directional book",
  },
  gamma: {
    tag: "GammaBal",
    neutral: 0,
    seriesName: "Greek Gamma Bal",
    formula:
      "bal = (Σ OI·Γ calls − Σ OI·Γ puts) / Σ OI·Γ (all) — whole chain, GEX sign convention",
    direction: (v) =>
      v > 1e-4
        ? "call OI dominates where gamma lives — long-gamma regime (vol suppression / pin)"
        : v < -1e-4
          ? "put OI dominates — short-gamma regime (moves amplify)"
          : "balanced call/put gamma",
  },
  vega: {
    tag: "VegaBal",
    neutral: 0,
    seriesName: "Greek Vega Bal",
    formula:
      "bal = (Σ OI·ν calls − Σ OI·ν puts) / Σ OI·ν — OTM wings 0<|Δ|<0.5 only",
    direction: (v) =>
      v > 1e-4
        ? "upside vol demand (calls) — OI mirror of a positive risk reversal"
        : v < -1e-4
          ? "downside vol demand (crash hedges) — OI mirror of a negative risk reversal"
          : "balanced wing vol demand",
  },
};

function buildGreekOption(
  snap: OptionsRow[],
  greekKey: "delta" | "theta" | "gamma" | "vega" | "rho",
  dateStr: string,
): EChartsOption {
  const themeMode = useStore.getState().themeMode;
  const c = axisColors(themeMode);
  const textColor = c.textColor;
  const splitColor = c.splitLineColor;

  if (snap.length === 0) {
    return {
      backgroundColor: "transparent",
      title: {
        text: `${GREEK_LABELS[greekKey]}  (${dateStr || "—"})\n[No data]`,
        left: "center",
        top: "center",
        textStyle: { color: textColor, fontSize: 11, fontWeight: 400 },
      },
    };
  }

  const valid = snap.filter(
    (r) => r[greekKey] != null && Number.isFinite(r[greekKey] as number),
  );

  if (valid.length === 0) {
    return {
      backgroundColor: "transparent",
      title: {
        text: `${GREEK_LABELS[greekKey]}  (${dateStr})\n[No valid ${greekKey} values]`,
        left: "center",
        top: "center",
        textStyle: { color: textColor, fontSize: 11, fontWeight: 400 },
      },
    };
  }

  const S = snap[0].underlying_close / PRICE_SCALE;

  const expiryMonths = Array.from(new Set(valid.map((r) => r.expiry_month))).sort(
    (a, b) => parseInt(a.replace("月", "")) - parseInt(b.replace("月", "")),
  );

  const series: EChartsOption["series"] = [];
  const nExpiries = expiryMonths.length;

  expiryMonths.forEach((em, ei) => {
    const sub = valid
      .filter((r) => r.expiry_month === em)
      .sort((a, b) => a.strike_price - b.strike_price);
    const calls = sub.filter((r) => r.option_type === "CALL");
    const puts = sub.filter((r) => r.option_type === "PUT");
    const color = expiryBlueColor(ei, nExpiries);

    if (calls.length > 0) {
      series.push({
        type: "scatter",
        name: `C ${em}`,
        symbolSize: 5,
        itemStyle: { color },
        data: calls.map((r) => ({
          value: [r.strike_price / PRICE_SCALE / S, r[greekKey] as number],
          strike: r.strike_price / PRICE_SCALE,
          optionType: "CALL",
          expiry: em,
          date: snap[0]?.date,
        })),
      });
      series.push({
        type: "line",
        name: `C ${em} (line)`,
        showSymbol: false,
        smooth: false,
        lineStyle: { color, width: 1.2, opacity: 0.85 },
        data: calls.map((r) => [r.strike_price / PRICE_SCALE / S, r[greekKey] as number]),
        z: 2,
        tooltip: { show: false },
      });
    }
    if (puts.length > 0) {
      series.push({
        type: "line",
        name: `P ${em}`,
        showSymbol: true,
        symbol: "diamond",
        symbolSize: 5,
        smooth: false,
        lineStyle: {
          color,
          width: 1.2,
          type: "dashed",
          opacity: 0.85,
        },
        itemStyle: { color },
        data: puts.map((r) => ({
          value: [r.strike_price / PRICE_SCALE / S, r[greekKey] as number],
          strike: r.strike_price / PRICE_SCALE,
          optionType: "PUT",
          expiry: em,
          date: snap[0]?.date,
        })),
        z: 2,
      });
    }
  });

  // Compute y-range
  const greekValues = valid.map((r) => r[greekKey] as number);
  const yMin = Math.min(...greekValues);
  const yMax = Math.max(...greekValues);
  const yPad = (yMax - yMin) * 0.15 || 1;

  // Zero line
  series.push({
    type: "line",
    name: "Zero",
    showSymbol: false,
    data: [
      [0.7, 0],
      [1.3, 0],
    ],
    lineStyle: { color: splitColor, type: "dashed", width: 1, opacity: 0.5 },
    silent: true,
    z: 1,
    tooltip: { show: false },
  });

  // Greek positioning metric — a SINGLE vertical line per panel, mirroring
  // the backend pair-level metrics (analyze/options/compute/greek_*.py).
  // Each greek answers a DIFFERENT question about the open book:
  //   delta — direction: delta-weighted put/call ratio dpcr (PCR
  //           refinement; 0.5 = balanced directional book)
  //   gamma — convexity regime: GEX-style call-minus-put gamma balance
  //           (dealer-sign convention; 0 = balanced)
  //   vega  — vol-demand direction: the same balance on the OTM wings
  //           (open-interest mirror of the 25Δ risk reversal; 0 = balanced)
  // theta/rho have no industry-standard positioning skew — no metric line.
  // The vertical line is drawn at the OI-weighted mean moneyness of the
  // COMBINED active CALL+PUT book (same anchor as the IV smile's
  // skewness line); the label/tooltip report the per-greek metric.
  const ySpan = [yMin - yPad, yMax + yPad];

  const metricMeta: GreekMetricMeta | null =
    greekKey === "delta" || greekKey === "gamma" || greekKey === "vega"
      ? GREEK_METRIC_META[greekKey]
      : null;

  // Metric over ACTIVE contracts (expiry_date >= date), true OI weights
  // (zero OI = zero vote), matching the backend conventions.
  const active = valid.filter((r) => r.expiry_date >= dateStr);

  const computeMetric = (): number | null => {
    if (!metricMeta) return null;
    let callAmt = 0;
    let putAmt = 0;
    for (const r of active) {
      const g = r[greekKey] as number | null;
      if (g == null || !Number.isFinite(g)) continue;
      if (greekKey === "vega") {
        // OTM wings only: calls 0 < Δ < 0.5, puts −0.5 < Δ < 0.
        const d = r.delta;
        if (d == null || !Number.isFinite(d)) continue;
        if (r.option_type === "CALL" && !(d > 0 && d < 0.5)) continue;
        if (r.option_type === "PUT" && !(d > -0.5 && d < 0)) continue;
      }
      const amt = Math.max(0, r.open_interest || 0) * Math.abs(g);
      if (r.option_type === "CALL") callAmt += amt;
      else putAmt += amt;
    }
    const total = callAmt + putAmt;
    if (!(total > 0)) return null;
    return greekKey === "delta" ? putAmt / total : (callAmt - putAmt) / total;
  };

  const metric = computeMetric();

  // Combined OI-weighted mean moneyness for the single line position.
  const totalOi = valid.reduce((s, r) => s + Math.max(1, r.open_interest), 0);
  const combinedCentroid =
    totalOi > 0
      ? valid.reduce(
          (s, r) => s + Math.max(1, r.open_interest) * (r.strike_price / PRICE_SCALE / S),
          0,
        ) / totalOi
      : 1.0;

  const AXIS_MIN = 0.7;
  const AXIS_MAX = 1.3;
  const INSET = 0.015;
  const lineX = Math.min(AXIS_MAX - INSET, Math.max(AXIS_MIN + INSET, combinedCentroid));
  const offLeft = combinedCentroid < AXIS_MIN + INSET;
  const offRight = combinedCentroid > AXIS_MAX - INSET;

  const metricLabel =
    metric != null ? (metric >= 0 ? `+${metric.toFixed(3)}` : metric.toFixed(3)) : "—";
  // Color by tilt relative to the metric's neutral (neutral maps to
  // skewLineColor's 1.0 anchor: below → blue, above → red).
  const metricColor = skewLineColor(
    metric != null && metricMeta ? metric - metricMeta.neutral + 1 : 1,
  );
  const arrowPrefix = offLeft ? "◀ " : "";
  const arrowSuffix = offRight ? " ▶" : "";

  const metricTooltipLines = metricMeta
    ? [
        `<b>${greekKey.toUpperCase()} Positioning Metric</b>`,
        `${metricMeta.tag} = ${metricLabel}`,
        `<div style="opacity:0.7">${
          metric != null
            ? metricMeta.direction(metric)
            : "insufficient OI on the relevant contracts"
        }</div>`,
        `<div style="opacity:0.7;margin-top:2px">${metricMeta.formula}</div>`,
        `<div style="opacity:0.6;margin-top:2px">line @ OI-wtd mean moneyness = ${combinedCentroid.toFixed(3)}${
          offLeft ? " (true off left, clamped)" : offRight ? " (true off right, clamped)" : ""
        }</div>`,
      ]
    : [];

  if (metricMeta) {
    series.push({
      type: "line",
      name: metricMeta.seriesName,
      showSymbol: false,
      data: [
        [lineX, ySpan[0]],
        [lineX, ySpan[1]],
      ],
      lineStyle: { color: metricColor, type: "solid", width: SKEW_LINE_WIDTH, opacity: 0.85 },
      silent: false,
      emphasis: { lineStyle: { width: 2.5, opacity: 1 } },
      z: 1,
      tooltip: {
        show: true,
        backgroundColor: c.tooltipBg,
        borderColor: splitColor,
        textStyle: { color: textColor, fontSize: 11 },
        formatter: () => metricTooltipLines.join("<br/>"),
      },
      label: {
        show: true,
        formatter: `${arrowPrefix}${metricMeta.tag}=${metricLabel}${arrowSuffix}`,
        color: textColor,
        fontSize: 9,
        fontWeight: 600,
        position: "top",
        distance: 4,
      },
    });
  }

  const visibleLegendData = series
    .filter((s) => {
      const n = (s as { name?: string }).name ?? "";
      return n !== "Zero" && !n.startsWith("Greek ");
    })
    .map((s) => (s as { name: string }).name);

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 50, right: 12, top: 36, bottom: 36 }),
    title: {
      text: `${GREEK_LABELS[greekKey]}  (${dateStr})  S=${fmtNum(S)}元`,
      left: "left",
      textStyle: { color: textColor, fontSize: 11, fontWeight: 600 },
    },
    tooltip: {
      trigger: "axis",
      axisPointer: {
        type: "cross",
        snap: true,
        lineStyle: { color: textColor, type: "dashed", opacity: 0.5 },
        label: {
          color: textColor,
          fontSize: 9,
          formatter: (params: unknown) => {
            const v = (params as { value: number }).value;
            return `Moneyness: ${fmtNum(v)}`;
          },
        },
      },
      backgroundColor: c.tooltipBg,
      borderColor: splitColor,
      textStyle: { color: textColor, fontSize: 11 },
      formatter: (p: unknown) => {
        const params = Array.isArray(p) ? p : [p];
        const validParams = params.filter((param) => {
          const val = param as { value?: number | number[] };
          const arr = Array.isArray(val.value) ? val.value : [val.value ?? 0, 0];
          return Number.isFinite(arr[1]);
        });
        if (validParams.length === 0) return "";
        const first = validParams[0] as { value?: number | number[] };
        const firstArr = Array.isArray(first.value) ? first.value : [first.value ?? 0, 0];
        const moneyness = firstArr[0] as number;
        const grouped = new Map<string, { call?: typeof validParams[0]; put?: typeof validParams[0] }>();
        validParams.forEach((param) => {
          const p = param as {
            value?: number | number[];
            data?: { strike?: number; optionType?: string; expiry?: string };
          };
          const extra = p.data;
          if (!extra) return;
          const key = `${extra.expiry}_${extra.strike}`;
          if (!grouped.has(key)) grouped.set(key, {});
          const g = grouped.get(key)!;
          if (extra.optionType === "CALL") g.call = param;
          else g.put = param;
        });
        const children: React.ReactNode[] = [];
        children.push(React.createElement(tooltipComponents.Bold, null, `Moneyness: ${fmtNum(moneyness)}`));
        grouped.forEach((g) => {
          const call = g.call as {
            seriesName?: string;
            value?: number | number[];
            data?: { strike?: number; expiry?: string; date?: string };
            marker?: string;
          };
          const put = g.put as {
            seriesName?: string;
            value?: number | number[];
            data?: { strike?: number; expiry?: string; date?: string };
            marker?: string;
          };
          const strike = call?.data?.strike ?? put?.data?.strike;
          const expiry = call?.data?.expiry ?? put?.data?.expiry;
          children.push(React.createElement("br"));
          children.push(React.createElement("br"));
          children.push(React.createElement(tooltipComponents.Bold, null, `${expiry} · K=${fmtNum(strike)}`));
          if (call) {
            const arr = Array.isArray(call.value) ? call.value : [call.value ?? 0, 0];
            const v = arr[1] as number;
            children.push(React.createElement("br"));
            children.push(React.createElement(React.Fragment, null,
              call.marker ?? "",
              " ",
              React.createElement(tooltipComponents.Bold, null, "CALL"),
              `: ${GREEK_LABELS[greekKey]}=${fmtNum(v, 4)}${GREEK_UNITS[greekKey]}`,
            ));
          }
          if (put) {
            const arr = Array.isArray(put.value) ? put.value : [put.value ?? 0, 0];
            const v = arr[1] as number;
            children.push(React.createElement("br"));
            children.push(React.createElement(React.Fragment, null,
              put.marker ?? "",
              " ",
              React.createElement(tooltipComponents.Bold, null, "PUT"),
              `: ${GREEK_LABELS[greekKey]}=${fmtNum(v, 4)}${GREEK_UNITS[greekKey]}`,
            ));
          }
        });
        return renderReactElement(React.createElement(React.Fragment, null, children));
      },
    },
    legend: commonLegend(themeMode, { top: 14, data: visibleLegendData }),
    xAxis: {
      type: "value",
      min: 0.7,
      max: 1.3,
      name: "Moneyness (Strike/Spot)",
      nameLocation: "middle",
      nameGap: 24,
      nameTextStyle: { color: textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: textColor } },
      axisLabel: { color: textColor, fontSize: 9 },
      splitLine: { lineStyle: { color: splitColor, type: "dashed", opacity: 0.4 } },
    },
    yAxis: {
      type: "value",
      name: GREEK_LABELS[greekKey],
      nameLocation: "middle",
      nameGap: 40,
      nameTextStyle: { color: textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: textColor } },
      axisLabel: { color: textColor, fontSize: 9 },
      splitLine: { lineStyle: { color: splitColor, type: "dashed", opacity: 0.4 } },
      min: yMin - yPad,
      max: yMax + yPad,
    },
    series,
  };
}

export default function GreeksPanel({ rows, selectedDate, greekKey }: Props) {
  const snap = rows.filter((r) => r.date === selectedDate);
  const option = buildGreekOption(snap, greekKey, selectedDate);

  return (
    <ChartCard
      title={GREEK_LABELS[greekKey]}
      subtitle={`${GREEK_LABELS[greekKey]} vs Moneyness · Blue gradient (dark=near expiry, light=far) · CALL (solid) / PUT (dashed)${GREEK_UNITS[greekKey]}`}
      height={420}
    >
      <EChart option={option} height={400} />
    </ChartCard>
  );
}