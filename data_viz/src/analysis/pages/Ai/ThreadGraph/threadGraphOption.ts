/**
 * threadGraphOption — the Thread Graph view's ECharts option builder
 * (pure): the graph's own topology (the LangGraph observability
 * contract — START → plan → <steps> → result → END as
 * circles with status coloring: pending + not-run grey / in progress blue /
 * failed red / completed green) plus the selected-node highlight, and
 * the FORK SPURS: each spawned subagent branch as a smaller circle in
 * a fork column to the RIGHT of and BELOW its parent step — a solid
 * spawn edge down-right and a dashed merge-back edge up-left (the
 * parent conversation gathered the result), both STRAIGHT so the pair
 * shares one diagonal two-way segment with an arrowhead at each end —
 * labeled to the right with the fork goal. The plan steps
 * are the main lane's circles; their task rows and the plan node's
 * planning task render as the beneath-the-graph list (graphModel).
 * Wave fork/merge paths bend at RIGHT ANGLES through invisible bend
 * nodes (kind "bend", size 0, no label/tooltip); a bent path's first
 * leg is a "bare" edge — the per-link symbol override suppresses the
 * arrowhead at the corner, the second leg keeps the right-triangle
 * arrowhead. Each
 * runner node's sublabel carries its shared context id and call counts
 * (one node one context); STEP labels are the step name only — their
 * counts line rides the tooltip, since the auto-fit scale packs step
 * columns tighter than a two-line label block and adjacent names
 * collided.
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
    // pending AND skipped share the grey: neither ran — pending waits,
    // skipped was settled not-run (a failed path's remaining nodes).
    case "skipped":
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
  skipped: "not run",
  neutral: "",
};

function clip(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/** Canvas size per node kind — runner/step nodes carry the big circles,
 *  endpoints stay dots, OUTPUT nodes (a graph's final output seat) are
 *  the slightly-smaller squares-after-the-lane (diamonds read as
 *  terminals; the chain's handoff producer), forks are the smaller spur
 *  circles under their parent step, the supervisor is the independent
 *  checker circle above the lane (step-sized — a peer of the lane
 *  nodes, not a spur), captions are invisible circles wearing the chain
 *  segment titles, bends are invisible right-angle corners of wave
 *  paths. */
const KIND_SYMBOL_SIZE: Record<ThreadGraphNodeKind, number> = {
  endpoint: 14,
  runner: 52,
  output: 40,
  step: 44,
  fork: 30,
  supervisor: 44,
  caption: 0,
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
    kind: ThreadGraphNodeKind;
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
    const isCaption = node.kind === "caption";
    const selected = node.id === selectedId;
    return {
      id: node.id,
      name: node.label,
      x: node.x,
      y: node.y,
      // The output seats are DIAMONDS — the terminal shape of a graph's
      // final output node, distinct from the lane's circles.
      symbol: node.kind === "output" ? "diamond" : "circle",
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
      // A fork labels to the RIGHT of its small circle with its goal
      // only — no "fork ·" prefix (the circle's size and column already
      // say fork; the prefix cluttered every spur). The label box must
      // stay INSIDE the fork-column clearance (X_STEP_AFTER_FORKS −
      // X_FORK): fork circles of adjacent step columns share the same
      // rows, so an over-wide label runs into the next column's fork
      // label — truncate at a width that fits (15 + 7 + 120 = 142 <
      // 158); the clicked fork's full goal renders in the beneath-graph
      // panel.
      ...(node.kind === "fork"
        ? {
            label: {
              show: true,
              position: "right",
              distance: 7,
              formatter: clip(node.sublabel ?? "", 24),
              color: c.textColor,
              fontSize: 10,
              lineHeight: 13,
              width: 120,
              overflow: "truncate",
            },
          }
        : isCaption
          ? {
              // The chain segment's floating title — the graph TYPE
              // (plan / custom / result) centered above its lane, muted.
              label: {
                show: true,
                position: "top",
                distance: 4,
                formatter: node.label,
                align: "center",
                color: c.textColor,
                fontSize: 12,
                fontWeight: 600,
                opacity: 0.75,
              },
            }
          : node.kind === "step"
          ? {
              label: {
                // NAME ONLY, one line: the auto-fit scale puts step
                // columns closer together than a two-line counts block,
                // so adjacent labels collided into gibberish — the
                // counts and ctx ride the tooltip now.
                show: true,
                position: node.labelTop === true ? "top" : "bottom",
                distance: 8,
                formatter: clip(node.label, 16),
                align: "center",
                color,
                fontSize: 11,
                fontWeight: selected ? 700 : 500,
                lineHeight: 15,
                width: 110,
                overflow: "truncate",
              },
            }
          : {
              label: {
                // Runner tags keep the two-line block (only plan/
                // execute/result exist — sparse, well spaced). Bends are
                // invisible corners — no label at all. A node flagged
                // labelTop carries its label ABOVE the circle (a
                // vertical wave fork/merge leg drops/rises through where
                // the below-label would sit).
                show: !isEndpoint && !isBend,
                position: node.labelTop === true ? "top" : "bottom",
                distance: 8,
                // Runner labels are short tags; the sublabel (context id +
                // counts) is the long one — the width+truncate pair bounds
                // the two-line block inside the column spacing so adjacent
                // step labels never run into each other; full text in the
                // tooltip. align centers the text WITHIN the box — the
                // default left-aligns it there, which shoves a runner's
                // short tag toward the previous step's label.
                formatter:
                  `${clip(node.label, 24)}\n` +
                  `{sub|${clip(node.sublabel ?? "", 30)}}`,
                align: "center",
                color,
                fontSize: 12,
                fontWeight: selected ? 700 : 500,
                lineHeight: 17,
                width: 130,
                overflow: "truncate",
                rich: { sub: { color: c.textColor, fontSize: 9 } },
              },
            }),
      info: {
        kind: node.kind,
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
    if (edge.kind === "handoff") {
      // THE SPECIAL INTER-GRAPH EDGE — one graph's FINAL OUTPUT node to
      // the next graph's entry: thicker, dashed, in the accent color,
      // with arrowheads at BOTH ends — the chain's two graphs read as
      // two GRAPHS, never one lane of regular nodes.
      return {
        ...base,
        symbol: [
          "path://M10,0L0,5L10,10Z",
          "path://M0,0L10,5L0,10Z",
        ],
        symbolSize: 11,
        lineStyle: {
          color: PALETTE_HI,
          width: 3,
          type: "dashed",
          opacity: 0.9,
        },
      };
    }
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
        // Fork goals clip shorter: 60 CJK chars is a poster, not a
        // tooltip on a 30px spur circle (now also where the step's
        // counts line lives, off the crowded canvas).
        const subMax = info.kind === "fork" ? 30 : 60;
        return (
          `<div style="font-weight:600">${clip(info.label, 60)}</div>` +
          (info.sublabel
            ? `<div style="opacity:.7;font-size:11px">${clip(info.sublabel, subMax)}</div>`
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
