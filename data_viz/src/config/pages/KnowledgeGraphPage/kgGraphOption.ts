/**
 * kgGraphOption — the Knowledge Graph tab's ECharts option builder
 * (pure): ONE namespace's subgraph as a force-directed canvas — one
 * circle per entity, colored by entity_type (the legend toggles a type
 * in/out of the canvas), sized by degree. Each hyperedge draws as a
 * STAR from its head (entity_ids[0] = subject → one arrow per object;
 * cardinality-2 rows degenerate to the plain arrow), so the canvas
 * stays a plain graph while the tooltip always carries the full
 * ordered member list. Edge color carries the weight's sign
 * (+ green / − red / null neutral axis grey) and its width the
 * magnitude.
 *
 * Force layout (no model coordinates — the subgraph's shape is the
 * data's, not a runtime chain like the Thread Graph's). Node ids ride
 * on the data items; the tooltip formatter reads the attached `info`
 * payload (threadGraphOption precedent — custom fields on graph data
 * items aren't in echarts' node-item typing). Colors come from the
 * shared CSS-var tokens — never hard-coded hex — and flip with the
 * chart theme (mode threaded in from useChartThemeMode).
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import {
  axisColors,
  commonLegend,
  DOWN_COLOR,
  MUTED_PALETTE,
  UP_COLOR,
} from "@/theme/chart-palette";
import type { KgEdge, KgEntity } from "@/lib/api-client/config";

function clip(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/** Node tooltip/click payload attached to each datum. */
interface KgNodeInfo {
  kind: "node";
  name: string;
  entityType: string;
  description: string | null;
  tags: string[];
  degree: number;
}

/** Edge tooltip payload — the hyperedge's full ordered member list. */
interface KgEdgeInfo {
  kind: "edge";
  relation: string;
  subRelation: string | null;
  weight: number | null;
  members: string[];
}

/** Distinct entity types in seed order, one MUTED_PALETTE hue each. */
function typeCategories(entities: KgEntity[]): Array<{ name: string; color: string }> {
  const types = [...new Set(entities.map((e) => e.entityType))].sort();
  return types.map((name, i) => ({
    name,
    color: MUTED_PALETTE[i % MUTED_PALETTE.length],
  }));
}

export function buildKgGraphOption(
  entities: KgEntity[],
  edges: KgEdge[],
  mode: ThemeMode,
): EChartsOption {
  const c = axisColors(mode);
  const categories = typeCategories(entities);
  const typeIdx = new Map(categories.map((cat, i) => [cat.name, i]));

  // Degree = how many edges the entity rides (a hyperedge counts once
  // per membership) — the node's canvas size.
  const degree = new Map<number, number>();
  for (const edge of edges) {
    for (const m of edge.members) {
      degree.set(m.entityId, (degree.get(m.entityId) ?? 0) + 1);
    }
  }

  const data = entities.map((e) => {
    const color = categories[typeIdx.get(e.entityType) ?? 0]?.color ?? c.axisLineColor;
    const d = degree.get(e.entityId) ?? 0;
    return {
      id: String(e.entityId),
      name: e.name,
      symbolSize: Math.min(44, 14 + Math.sqrt(d) * 6),
      category: typeIdx.get(e.entityType) ?? 0,
      itemStyle: {
        color: `${color}33`, // ~20% wash — border carries the hue
        borderColor: color,
        borderWidth: 1.6,
      },
      label: {
        show: true,
        position: "bottom" as const,
        distance: 4,
        formatter: clip(e.name, 22),
        color: c.textColor,
        fontSize: 9,
        lineHeight: 12,
        width: 110,
        overflow: "truncate",
      },
      info: {
        kind: "node",
        name: e.name,
        entityType: e.entityType,
        description: e.description,
        tags: e.tags,
        degree: d,
      } satisfies KgNodeInfo,
    };
  });

  // One arrow per non-head member — the star fan-out of the hyperedge.
  const links = edges.flatMap((edge) => {
    const [head, ...objects] = edge.members;
    if (head == null) return [];
    const color =
      edge.weight == null
        ? c.axisLineColor
        : edge.weight >= 0
          ? UP_COLOR
          : DOWN_COLOR;
    const width = edge.weight == null ? 1.1 : 1 + Math.abs(edge.weight) * 2;
    const info: KgEdgeInfo = {
      kind: "edge",
      relation: edge.relation,
      subRelation: edge.subRelation,
      weight: edge.weight,
      members: edge.members.map((m) => m.name ?? `#${m.entityId}`),
    };
    return objects.map((o) => ({
      source: String(head.entityId),
      target: String(o.entityId),
      lineStyle: {
        color,
        width,
        opacity: 0.55,
        curveness: 0.12,
      },
      info,
    }));
  });

  return baseChartOption(mode, {
    legend: commonLegend(mode, { data: categories.map((cat) => cat.name) }),
    grid: null,
    tooltip: commonTooltip(mode, {
      trigger: "item",
      axisPointer: undefined,
      formatter: (params: unknown) => {
        const p = params as {
          dataType?: string;
          data?: { info?: KgNodeInfo | KgEdgeInfo };
        };
        const info = p.data?.info;
        if (info == null) return "";
        if (info.kind === "node") {
          return (
            `<div style="font-weight:600">${clip(info.name, 60)}</div>` +
            `<div style="opacity:.7;font-size:11px">${info.entityType} · ${info.degree} edge${info.degree === 1 ? "" : "s"}</div>` +
            (info.description
              ? `<div style="margin-top:4px">${clip(info.description, 120)}</div>`
              : "") +
            (info.tags.length > 0
              ? `<div style="margin-top:4px;opacity:.7;font-size:11px">${clip(info.tags.join(" · "), 80)}</div>`
              : "")
          );
        }
        const head = info.members[0] ?? "?";
        const rest = info.members.slice(1).join(", ");
        return (
          `<div style="font-weight:600">${info.relation}` +
          (info.subRelation ? ` <span style="opacity:.6;font-weight:400">(${info.subRelation})</span>` : "") +
          `</div>` +
          `<div style="margin-top:4px;font-size:11px">${clip(head, 40)} → ${clip(rest, 90)}</div>` +
          (info.weight != null
            ? `<div style="margin-top:4px;opacity:.7;font-size:11px">weight ${info.weight.toFixed(2)}</div>`
            : "")
        );
      },
    }),
    series: [
      {
        type: "graph",
        layout: "force",
        roam: true,
        scaleLimit: { min: 0.25, max: 3 },
        categories: categories.map((cat) => ({
          name: cat.name,
          // The legend swatch reads the CATEGORY's color — without it the
          // legend falls back to ECharts' default palette and stops
          // matching the per-node MUTED_PALETTE hues.
          itemStyle: { color: cat.color },
        })),
        force: {
          repulsion: 260,
          edgeLength: [60, 140],
          gravity: 0.12,
          // The seed graph is static — settle the layout synchronously,
          // no continuous node jitter (the graph's only "animation").
          layoutAnimation: false,
        },
        // Flat-back right-pointing triangle arrowhead, rotated onto the
        // edge direction (the Thread Graph's shape — ECharts' builtin
        // 'arrow' has a concave back).
        edgeSymbol: ["none", "path://M0,0L10,5L0,10Z"],
        edgeSymbolSize: 9,
        emphasis: { scale: 1.08 },
        labelLayout: { hideOverlap: true },
        data: data as unknown as object[],
        links: links as unknown as object[],
      },
    ],
  });
}
