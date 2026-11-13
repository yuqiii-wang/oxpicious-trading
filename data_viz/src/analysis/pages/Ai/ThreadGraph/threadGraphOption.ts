/**
 * threadGraphOption — the Thread Graph view's ECharts option builder
 * (pure): the graph's own topology (the LangGraph observability
 * contract — START → plan → <steps> → result → END as
 * circles with status coloring: pending + not-run grey / in progress blue /
 * failed red / completed green) plus the selected-node highlight, and
 * the SPAWN SPURS: each spawned subagent branch as a smaller circle in
 * a spawn column to the RIGHT of and BELOW its parent — a solid spawn
 * edge and a dashed merge-back edge (the parent conversation gathered
 * the result) bent at RIGHT ANGLES through an invisible bend node at
 * the parent's x on the spawn's row, so the pair shares one L-shaped
 * two-way connector with an arrowhead at each end and a parent's spurs
 * fan out of one vertical trunk — the spawn goal labeled below its
 * circle (above it when the spawn's OWN spurs render, labelTop). Nested
 * spawn columns stair-step one spawn-to-spawn offset further RIGHT per
 * depth level (graphModel's SPAWN_TO_SPAWN_OFFSET). The plan steps
 * are the main lane's circles; their task rows and the plan node's
 * planning task render as the beneath-the-graph list (graphModel).
 * Wave spawn/merge paths and the spawn spur elbows bend at RIGHT ANGLES
 * through invisible bend
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
 * Edges run straight (no curveness) with flat-back right-pointing
 * triangle arrowheads — every edge is a horizontal or vertical leg,
 * right angles only: the lane sits on one row, and every spawn/merge
 * path bends through an invisible bend node; the inter-graph HANDOFF
 * edge keeps that style with only the dash added (graphModel's
 * X_CHAIN_GAP spans the segments). layout:"none" with the model's explicit
 * coordinates; ECharts auto-fits the content bounding box — positions
 * scale to the canvas while circles, labels and edge arrowheads keep
 * their fixed px (nodeScaleRatio 0 pins the circles at symbolSize px
 * under roam zoom too). THE DEFAULT VIEW IS UNCOMPRESSED: the spawn
 * subtrees FOLD by default (a parent click expands one level — the
 * builder draws the ▸N/▾N cue on every foldable parent), and the
 * series zoom never lets the effective scale land below graphModel's
 * FIT_FLOOR — the main path renders properly spaced; a chain wider
 * than the canvas pans (roam), the initial view anchored to the
 * chain's left edge. The user's roamed zoom/center ride back in on
 * every rebuild (ThreadGraphView's graphRoam listener) so the live
 * poll and node clicks never reset the pan. Node ids ride on the data
 * items (click params.data.id → the page's selection state + the
 * parent's fold toggle). Colors come
 * from the shared CSS-var tokens — never hard-coded hex — and flip
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
import {
  FIT_FLOOR,
  KIND_LABEL_PX,
  KIND_SYMBOL_SIZE,
} from "./graphModel";
import type {
  ThreadGraphModel,
  ThreadGraphNode,
  ThreadGraphNodeKind,
  ThreadNodeStatus,
} from "./graphModel";

/** The view-side state folded into the series option — the FROZEN
 *  view (zoom + content-coord center) the view locks per viewport
 *  change and replays verbatim across every rebuild. Recomputing the
 *  fit on each rebuild is what re-aligned the whole graph on every
 *  node click (an expansion reflows the layout → a new bbox → a
 *  re-fit); with the lock the view holds still and only the affected
 *  nodes re-position. The user's roamed zoom/center (read off the live
 *  model after each graphRoam) land in the same slot and win. */
export interface ThreadGraphViewHint {
  roam: { zoom: number; center: [number, number] } | null;
}

/** The roam-zoom cap — also the floor-raise ceiling the view applies;
 *  scaleLimit.max rides at least this high so the floor zoom is never
 *  silently clamped below the floor on very wide chains
 *  (nodeScaleRatio 0 keeps the circles fixed-px at any zoom, so a high
 *  zoom is safe). */
export const ZOOM_MAX = 8;

/** The DEFAULT view a viewport change locks in: the floor raise — the
 *  effective scale never lands below FIT_FLOOR (no compression of the
 *  main path; the spawn subtrees fold by default to keep the content
 *  shallow) — and an explicit center, ALWAYS materialized (never left
 *  to ECharts' auto-fit): a floor-zoomed view anchors to the chain's
 *  LEFT edge (START side — the chain reads left to right), a fitted
 *  view to the content bbox's center. Explicit zoom + center is what
 *  makes the view freezable across rebuilds. fitScale = Infinity when
 *  the canvas isn't measured yet → zoom 1, bbox center. */
export function defaultGraphView(
  model: ThreadGraphModel,
  fitScale: number,
  canvasWidth: number,
): { zoom: number; center: [number, number] } {
  const zoom = fitScale < FIT_FLOOR
    ? Math.min(FIT_FLOOR / Math.max(fitScale, 1e-6), ZOOM_MAX)
    : 1;
  const sigmaEff = fitScale * zoom;
  const { minX, maxX, minY, maxY } = model.contentBounds;
  const cy = (minY + maxY) / 2;
  const center: [number, number] = zoom > 1
    ? [minX + canvasWidth / (2 * sigmaEff) - 40, cy]
    : [(minX + maxX) / 2, cy];
  return { zoom, center };
}

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

/** Canvas size per node kind — imported from graphModel's footprint
 *  registry (the layout reserves room from the same table, so the
 *  drawn sizes can never drift from the reserved room): runner/step
 *  nodes carry the big circles, endpoints stay dots, OUTPUT nodes (a
 *  graph's final output seat) are the slightly-smaller diamonds,
 *  spawns the smaller spur circles, the supervisor the independent
 *  checker circle above the lane, captions/bends invisible. */
// (KIND_SYMBOL_SIZE lives in graphModel.ts — see the import above.)

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
    metrics: string;
    status: ThreadNodeStatus;
    /** DIRECT spawn count when the node is foldable (a parent with
     *  spawn spurs), null otherwise — the cue line + the tooltip's
     *  expand/fold hint read it. */
    childCount: number | null;
    /** Whether the node's spurs currently render (it is expanded). */
    expanded: boolean;
  };
}

export function buildThreadGraphOption(
  model: ThreadGraphModel,
  mode: ThemeMode,
  selectedId: string | null,
  view?: ThreadGraphViewHint,
  expanded: ReadonlySet<string> = new Set<string>(),
): EChartsOption {
  const c = axisColors(mode);

  // THE FROZEN VIEW — the option never recomputes a fit: the view
  // hands in the locked zoom/center (defaultGraphView, recomputed only
  // on viewport changes) or the user's roamed state, and the series
  // replays it verbatim. A rebuild (3s poll, node click, expansion
  // toggle, theme flip) re-renders the graph in place — the whole
  // view holds still, only the reflowed nodes move. A missing roam
  // (canvas not measured yet) falls back to ECharts' own auto-fit for
  // the first paint only.
  const zoom = view?.roam?.zoom ?? 1;
  const center: [number, number] | undefined = view?.roam?.center;

  const data: NodeDatum[] = model.nodes.map((node) => {
    const color = statusColor(node.status, mode);
    const isEndpoint = node.kind === "endpoint";
    const isBend = node.kind === "bend";
    const isCaption = node.kind === "caption";
    const selected = node.id === selectedId;
    // THE FOLD CUE — a foldable parent (it owns spawn spurs) carries a
    // second label line: ▸ N folded (click to expand), ▾ N expanded
    // (click to fold). The smallest level hides by default; the cue is
    // the affordance that says this circle toggles its subtree.
    const childCount = model.childCountByNodeId.get(node.id) ?? null;
    const isExpanded = childCount !== null && expanded.has(node.id);
    const foldCue = childCount === null
      ? ""
      : `\n{fold|${isExpanded ? "▾" : "▸"} ${childCount}}`;
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
      // A spawn (or its inherit-context twin, the fork — and the
      // abandoned old-chain stub) labels BELOW its small circle with
      // its goal only — no "spawn ·" prefix (the circle's size and
      // column already say spawn; the prefix cluttered every spur).
      // BELOW, not right: the goal
      // text is fixed-px while node positions scale down to fit, so a
      // right-side label crossed into the next column's circles; the
      // rows have vertical room (Y_STEP), the spawn column doesn't. A
      // spawn whose OWN spurs render flips the label ABOVE (labelTop,
      // set by the layout): its spawn trunk drops through where the
      // below-label would sit — the same flip a wave entry/exit makes.
      // The clicked spawn's full goal renders in the beneath-graph panel.
      ...(node.kind === "spawn" || node.kind === "fork"
          || node.kind === "abandoned"
        ? {
            label: {
              show: true,
              position: node.labelTop === true ? "top" : "bottom",
              distance: 6,
              formatter: node.kind === "abandoned"
                ? "old chain"
                : clip(node.sublabel ?? "", 20) + foldCue,
              align: "center",
              color: c.textColor,
              fontSize: 10,
              lineHeight: 13,
              width: KIND_LABEL_PX[node.kind].w,
              overflow: "truncate",
              rich: {
                fold: { color: c.textColor, fontSize: 10, opacity: 0.65 },
              },
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
                // columns closer together than a two-line label block,
                // so adjacent names collided into gibberish — the
                // counts and ctx ride the tooltip now. The width cap
                // stays inside the fitted column spacing (labels are
                // fixed-px, positions scale); full text in the tooltip.
                // Foldable parents carry the ▸N/▾N cue line under the
                // name (short — never widens the block).
                show: true,
                position: node.labelTop === true ? "top" : "bottom",
                distance: 8,
                formatter: clip(node.label, 12) + foldCue,
                align: "center",
                color,
                fontSize: 11,
                fontWeight: selected ? 700 : 500,
                lineHeight: 15,
                width: KIND_LABEL_PX.step.w,
                overflow: "truncate",
                rich: {
                  fold: { color: c.textColor, fontSize: 10, opacity: 0.65 },
                },
              },
            }
          : {
              label: {
                // Runner tags keep the two-line block (only plan/
                // execute/result exist — sparse, well spaced). Bends are
                // invisible corners — no label at all. A node flagged
                // labelTop carries its label ABOVE the circle (a
                // vertical wave spawn/merge leg drops/rises through where
                // the below-label would sit).
                show: !isEndpoint && !isBend,
                position: node.labelTop === true ? "top" : "bottom",
                distance: 8,
                // Runner labels are short tags; the sublabel (context id +
                // counts) is the long one — the width+truncate pair bounds
                // the two-line block inside the column spacing so adjacent
                // labels never run into each other; full text in the
                // tooltip. align centers the text WITHIN the box — the
                // default left-aligns it there, which shoves a runner's
                // short tag toward the previous step's label.
                formatter:
                  `${clip(node.label, 24)}\n` +
                  `{sub|${clip(node.sublabel ?? "", 14)}}`,
                align: "center",
                color,
                fontSize: 12,
                fontWeight: selected ? 700 : 500,
                lineHeight: 17,
                width: KIND_LABEL_PX.runner.w,
                overflow: "truncate",
                rich: { sub: { color: c.textColor, fontSize: 9 } },
              },
            }),
      info: {
        kind: node.kind,
        label: node.label,
        sublabel: node.sublabel ?? "",
        metrics: node.metrics ?? "",
        status: node.status,
        childCount,
        expanded: isExpanded,
      },
    };
  });

  // All edges run straight (no curveness): the spawn's spawn/merge pair
  // shares one vertical segment — solid down, dashed back — so the spur
  // reads as a two-way connector with an arrowhead at both ends.
  const links = model.edges.map((edge) => {
    const base = { source: edge.source, target: edge.target };
    if (edge.kind === "handoff") {
      // THE INTER-GRAPH EDGE — one graph's FINAL OUTPUT node to the
      // next graph's entry: the chain style (same weight, color and
      // flat-back arrowhead as the walked lane) with the DASH kept —
      // the one cue that this link crosses a graph boundary, spanning
      // the wider X_CHAIN_GAP between the two segments.
      return {
        ...base,
        lineStyle: {
          color: c.axisLineColor,
          width: 1.6,
          type: "dashed",
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
    if (edge.kind === "fork") {
      // THE FORK SPUR (2026-11-13): the arrowed leg into an
      // inherit-context child — HEAVIER than a spawn's 1.4 (and than
      // the chain's 1.6): the thick line is the inheritance cue, the
      // fork continued its parent's full context.
      return {
        ...base,
        lineStyle: { color: c.axisLineColor, width: 2.6 },
      };
    }
    if (edge.kind === "fork_lead") {
      // The fork elbow's first leg — the fork weight WITHOUT the
      // arrowhead (the corner is wiring, the bare-leg rule).
      return {
        ...base,
        symbol: ["none", "none"],
        lineStyle: { color: c.axisLineColor, width: 2.6 },
      };
    }
    if (edge.kind === "abandoned") {
      // THE DEMOTED OLD CHAIN (2026-11-13): fork → abandoned pre-fork
      // tail — dashed and thin, the visual opposite of the fork weight:
      // the drifted direction was left behind.
      return {
        ...base,
        lineStyle: {
          color: c.axisLineColor,
          width: 1.1,
          type: "dashed",
        },
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
        // the long texts (spawn goals, plan payloads, requests) live in
        // the clicked node's beneath-graph panel, not the tooltip.
        // Spawn goals clip shorter: 60 CJK chars is a poster, not a
        // tooltip on a 30px spur circle (now also where the step's
        // counts line lives, off the crowded canvas). A foldable
        // parent adds the expand/fold affordance line (kind-aware: a
        // fork parent folds forks).
        const subMax = info.kind === "spawn" || info.kind === "fork"
            || info.kind === "abandoned"
          ? 30
          : 60;
        const foldNoun = info.kind === "fork" ? "fork" : "spawn";
        const foldLine = info.childCount === null
          ? ""
          : `<div style="margin-top:3px;font-size:11px;opacity:.8">click to `
            + `${info.expanded ? "fold" : "expand"} · ${info.childCount}`
            + ` ${foldNoun}${info.childCount === 1 ? "" : "s"}</div>`;
        return (
          `<div style="font-weight:600">${clip(info.label, 60)}</div>` +
          (info.sublabel
            ? `<div style="opacity:.7;font-size:11px">${clip(info.sublabel, subMax)}</div>`
            : "") +
          // The node's elapsed + token consumption line (blank when
          // nothing ran).
          (info.metrics
            ? `<div style="margin-top:3px;font-size:11px">${info.metrics.replace(/^ · /, "")}</div>`
            : "") +
          foldLine +
          statusLine
        );
      },
    }),
    series: [
      {
        type: "graph",
        layout: "none",
        roam: true,
        scaleLimit: { min: 0.25, max: Math.max(ZOOM_MAX, zoom) },
        zoom,
        ...(center !== undefined ? { center } : {}),
        // nodeScaleRatio 0 — ECharts' default (0.6) grows node circles
        // sub-linearly under roam zoom ((z-1)·0.6+1), which would blow
        // the 44px circles up to ~78px at the floor zoom and break the
        // measured spacing. 0 keeps the circles at exactly their
        // symbolSize px at ANY zoom, matching the footprint registry.
        nodeScaleRatio: 0,
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
