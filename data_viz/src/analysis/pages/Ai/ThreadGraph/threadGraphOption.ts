/**
 * threadGraphOption — the Thread Graph view's ECharts option builder
 * (pure): the graph's own topology (the LangGraph observability
 * contract — START → plan → <steps> → result → END as
 * circles with status coloring: pending grey / in progress blue / failed red /
 * completed green / skipped dim) plus the selected-node highlight, and
 * the FORK SPURS: each spawned subagent branch as a smaller circle
 * hanging VERTICALLY under its parent step — a solid spawn edge down
 * and a dashed merge-back edge up (the parent conversation gathered
 * the result), both STRAIGHT so the pair shares one vertical two-way
 * segment with an arrowhead at each end — labeled to the right with
 * the fork goal. The plan steps
 * are the main lane's circles; their task rows and the plan node's
 * planning task render as the beneath-the-graph list (graphModel).
 * Wave fork/merge paths bend at RIGHT ANGLES through invisible bend
 * nodes (kind "bend", size 0, no label/tooltip); a bent path's first
 * leg is a "bare" edge — the per-link symbol override suppresses the
 * arrowhead at the corner, the second leg keeps the right-triangle
 * arrowhead. Each
 * runner node's sublabel carries its shared context id and call counts
 * (one node one context).
 *
 * Edges run straight LangGraph-style (no curveness) with flat-back
 * right-pointing triangle arrowheads. layout:"none" with the model's
 * explicit coordinates; ECharts
 * auto-fits the content bounding box, so zoom stays neutral (a manual
 * fit would compound with the built-in one). Node ids ride on the data
 * items (click params.data.id → the page's selection state). Colors
 * come from the shared CSS-var tokens — never hard-coded hex — and flip
 * with the chart theme (mode threaded in from useChartThemeMode).
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import {
  axisColors,
  DOWN_COLOR,
  NEUTRAL_FILL,
  PALETTE_HI,
  SUBTITLE_COLOR,
  UP_COLOR,
} from "@/theme/chart-palette";
import type {
  ThreadGraphModel,
  ThreadGraphNode,
  ThreadGraphNodeKind,
  ThreadNodeStatus,
} from "./graphModel";

export function statusColor(
  status: ThreadNodeStatus,
  mode: ThemeMode,
): string {
  switch (status) {
    case "completed":
      return UP_COLOR;
    case "failed":
      return DOWN_COLOR;
    case "in_progress":
      return PALETTE_HI;
    case "skipped":
      return SUBTITLE_COLOR;
    case "pending":
      return NEUTRAL_FILL;
    default:
      return axisColors(mode).axisLineColor;
  }
}

const STATUS_LABEL: Record<ThreadNodeStatus, string> = {
  pending: "pending",
  in_progress: "in progress",
  completed: "completed",
  failed: "failed",
  skipped: "skipped",
  neutral: "",
};

function clip(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/** Canvas size per node kind — runner/step nodes carry the big circles,
 *  endpoints stay dots, forks are the smaller spur circles under their
 *  parent step, bends are invisible right-angle corners of wave paths. */
const KIND_SYMBOL_SIZE: Record<ThreadGraphNodeKind, number> = {
  endpoint: 14,
  runner: 52,
  step: 44,
  fork: 30,
  bend: 0,
};

/** Tooltip payload attached to each node datum (click params.data too). */
interface NodeDatum {
  id: string;
  name: string;
  x: number;
  y: number;
  symbol: string;
  symbolSize: number;
  itemStyle: Record<string, unknown>;
  label: Record<string, unknown>;
  info: {
    label: string;
    sublabel: string;
    status: ThreadNodeStatus;
  };
}

export function buildThreadGraphOption(
  model: ThreadGraphModel,
  mode: ThemeMode,
  selectedId: string | null,
): EChartsOption {
  const c = axisColors(mode);

  const data: NodeDatum[] = model.nodes.map((node) => {
    const color = statusColor(node.status, mode);
    const isEndpoint = node.kind === "endpoint";
    const isBend = node.kind === "bend";
    const selected = node.id === selectedId;
    return {
      id: node.id,
      name: node.label,
      x: node.x,
      y: node.y,
      symbol: "circle",
      symbolSize: KIND_SYMBOL_SIZE[node.kind],
      itemStyle: {
        color:
          node.status === "neutral"
            ? "transparent"
            : `${color}33`, // ~20% wash of the status color
        borderColor: color,
        borderWidth: selected ? 3.5 : isEndpoint ? 1.4 : 2.2,
        borderType:
          node.status === "pending" || node.status === "skipped"
            ? "dashed"
            : "solid",
        shadowBlur: selected ? 14 : 0,
        shadowColor: PALETTE_HI,
      },
      // A fork labels to the RIGHT of its small circle — one quiet line
      // with the fork goal. The label box must stay INSIDE the column
      // spacing (X_STEP): fork circles of adjacent columns sit at the
      // same y, so an over-wide label runs into the next column's fork
      // label — truncate at a width that fits between the circles; the
      // clicked fork's full goal renders in the beneath-graph panel.
      ...(node.kind === "fork"
        ? {
            label: {
              show: true,
              position: "right",
              distance: 7,
              formatter: `fork · ${clip(node.sublabel ?? "", 40)}`,
              color: c.textColor,
              fontSize: 10,
              lineHeight: 13,
              width: 130,
              overflow: "truncate",
            },
          }
        : {
            label: {
              // Bends are invisible corners — no label at all. A node
              // flagged labelTop carries its label ABOVE the circle
              // (a vertical wave fork/merge leg drops/rises through
              // where the below-label would sit).
              show: !isEndpoint && !isBend,
              position: node.labelTop === true ? "top" : "bottom",
              distance: 8,
              // Runner labels are short tags; the sublabel (context id +
              // counts) is the long one — the width+truncate pair bounds
              // the two-line block inside the column spacing so adjacent
              // step labels never run into each other; full text in the
              // tooltip.
              formatter:
                `${clip(node.label, 24)}\n` +
                `{sub|${clip(node.sublabel ?? "", 30)}}`,
              color,
              fontSize: 12,
              fontWeight: selected ? 700 : 500,
              lineHeight: 17,
              width: 150,
              overflow: "truncate",
              rich: { sub: { color: c.textColor, fontSize: 9 } },
            },
          }),
      info: {
        label: node.label,
        sublabel: node.sublabel ?? "",
        status: node.status,
      },
    };
  });

  // All edges run straight (no curveness): the fork's spawn/merge pair
  // shares one vertical segment — solid down, dashed back — so the spur
  // reads as a two-way connector with an arrowhead at both ends.
  const links = model.edges.map((edge) => {
    const base = { source: edge.source, target: edge.target };
    if (edge.kind === "bare") {
      // The first leg of a bent wave path: chain-styled but WITHOUT the
      // target arrowhead — the corner is an invisible bend node and
      // must not grow an arrow mid-path (per-link symbol overrides the
      // series-level triangle; the second leg carries it).
      return {
        ...base,
        symbol: ["none", "none"],
        lineStyle: { color: c.axisLineColor, width: 1.6 },
      };
    }
    if (edge.kind === "spawn") {
      return {
        ...base,
        lineStyle: { color: c.axisLineColor, width: 1.4 },
      };
    }
    if (edge.kind === "merge") {
      return {
        ...base,
        lineStyle: {
          color: c.axisLineColor,
          width: 1.2,
          type: "dashed",
        },
      };
    }
    return { ...base, lineStyle: { color: c.axisLineColor, width: 1.6 } };
  });

  return baseChartOption(mode, {
    legend: null,
    grid: null,
    tooltip: commonTooltip(mode, {
      trigger: "item",
      axisPointer: undefined,
      formatter: (params: unknown) => {
        const p = params as { data?: NodeDatum };
        const info = p.data?.info;
        // Bends (empty label) are invisible wiring — no tooltip.
        if (!info || info.label === "") return "";
        const color = statusColor(info.status, mode);
        const statusLine = info.status === "neutral"
          ? ""
          : `<div style="margin-top:4px"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${color};margin-right:5px"></span>${STATUS_LABEL[info.status]}</div>`;
        // NODE INFO ONLY: the node's tag + a one-line hint + status —
        // the long texts (fork goals, plan payloads, requests) live in
        // the clicked node's beneath-graph panel, not the tooltip.
        return (
          `<div style="font-weight:600">${clip(info.label, 60)}</div>` +
          (info.sublabel
            ? `<div style="opacity:.7;font-size:11px">${clip(info.sublabel, 60)}</div>`
            : "") +
          statusLine
        );
      },
    }),
    series: [
      {
        type: "graph",
        layout: "none",
        roam: true,
        scaleLimit: { min: 0.25, max: 3 },
        zoom: 1,
        // Flat-back right-pointing triangle arrowhead — ECharts' builtin
        // 'arrow' has a concave curvy back; the path must point right,
        // edge symbols rotate onto the edge direction.
        edgeSymbol: ["none", "path://M0,0L10,5L0,10Z"],
        edgeSymbolSize: 10,
        emphasis: { scale: 1.08 },
        // Custom fields on graph data items aren't in echarts' node-item
        // typing (repo precedent: streakChartOption's markArea datums).
        data: data as unknown as object[],
        links,
      },
    ],
  });
}

export type { ThreadGraphNode };
