/**
 * kg.service — READ over the knowledge hypergraph (llm.kg_entities /
 * llm.kg_edges, DDL database/sql/llm/13_kg_hypergraph.sql, seeds
 * database/sql/llm/static/knowledge_graphs/) for the Config app's
 * Knowledge Graph tab.
 *
 * READ-ONLY: the graph is a static seed — rows land only via the SQL
 * seed files, so the tab displays and filters only (the same contract
 * as the Keywords tab). The UI asks for ONE namespace at a time (the
 * kg_namespace code — namespace scopes the subgraph, and a hyperedge
 * never spans namespaces); edge member ids resolve to names against the
 * SAME namespace's entities here, so the client receives ready-to-draw
 * named edges. 10-min TTL via cachedRows — the seeds change only on a
 * git-reviewed apply, and the tab's reload button re-issues the fetch
 * (a server restart drops the cache).
 */
import { querySqlView } from "../lib/sql-views.js";
import { cachedRows } from "./classification-cache.js";
import type { QueryResultRow } from "pg";

/** The shipped kg_namespace codes (the DDL header + the deep-agent
 *  kit's _NAMESPACE_PARAM are the vocabulary record — kg_namespace is
 *  deliberately NOT a label_dim domain until the first KG writer ships
 *  its three-mirror registration). */
export const KG_NAMESPACES = [
  {
    code: 0,
    label: "DB semantics",
    hint: "one entity per table/view, edges for how tables relate",
  },
  {
    code: 1,
    label: "Prompts",
    hint: "one entity per cataloged skill/plan prompt, edges for how they relate",
  },
] as const;

export type KgNamespaceCode = (typeof KG_NAMESPACES)[number]["code"];

export function isKgNamespace(v: unknown): v is KgNamespaceCode {
  return KG_NAMESPACES.some((n) => n.code === v);
}

export interface KgEntityRow {
  entityId: number;
  name: string;
  entityType: string;
  description: string | null;
  tags: string[];
}

/** One hyperedge with its members RESOLVED to names, head-first
 *  (members[0] = subject). An id that somehow misses the entity map
 *  (a writer bug) degrades that one member to name null — never a
 *  failed request. */
export interface KgEdgeRow {
  edgeId: number;
  relation: string;
  subRelation: string | null;
  weight: number | null;
  members: Array<{ entityId: number; name: string | null; entityType: string | null }>;
}

interface EntityDbRow extends QueryResultRow {
  entity_id: string | number;
  name: string;
  entity_type: string;
  description: string | null;
  tags: string[] | null;
}

interface EdgeDbRow extends QueryResultRow {
  edge_id: string | number;
  relation: string;
  sub_relation: string | null;
  entity_ids: Array<string | number>;
  weight: number | null;
}

function listKgEntities(namespace: KgNamespaceCode): Promise<KgEntityRow[]> {
  return cachedRows(`kg:entities:${namespace}`, () =>
    querySqlView<EntityDbRow>("data_viz/api/services/kg/entities", {
      namespace,
    }).then((rows) =>
      rows.map((r) => ({
        entityId: Number(r.entity_id),
        name: r.name,
        entityType: r.entity_type,
        description: r.description,
        tags: r.tags ?? [],
      })),
    ),
  );
}

function listKgEdges(namespace: KgNamespaceCode): Promise<KgEdgeRow[]> {
  return cachedRows(`kg:edges:${namespace}`, async () => {
    const [edgeRows, entities] = await Promise.all([
      querySqlView<EdgeDbRow>("data_viz/api/services/kg/edges", { namespace }),
      listKgEntities(namespace),
    ]);
    const byId = new Map(entities.map((e) => [e.entityId, e]));
    return edgeRows.map((r) => ({
      edgeId: Number(r.edge_id),
      relation: r.relation,
      subRelation: r.sub_relation,
      weight: r.weight === null ? null : Number(r.weight),
      members: (r.entity_ids ?? []).map((id) => {
        const entity = byId.get(Number(id));
        return {
          entityId: Number(id),
          name: entity?.name ?? null,
          entityType: entity?.entityType ?? null,
        };
      }),
    }));
  });
}

/** One namespace's whole display graph — entities plus name-resolved
 *  edges (the GET /api/config/kg payload). */
export interface KgGraph {
  namespace: KgNamespaceCode;
  entities: KgEntityRow[];
  edges: KgEdgeRow[];
}

export async function listKgGraph(namespace: KgNamespaceCode): Promise<KgGraph> {
  const [entities, edges] = await Promise.all([
    listKgEntities(namespace),
    listKgEdges(namespace),
  ]);
  return { namespace, entities, edges };
}

/** One logged kg_search chain (llm.kg_queries — the AI monitor's chain
 *  log) drawn as a subgraph: the chain's edges resolved against the
 *  LIVE graph (inner join — edges the graph no longer has drop out,
 *  matching the replay's own skip semantics), in depth order, with the
 *  distinct member entities for the client's node set. NOT cached via
 *  cachedRows — the chain log is runtime state; the route is
 *  no-store end to end. */
export interface KgChain {
  namespace: KgNamespaceCode;
  chainId: string;
  entities: KgEntityRow[];
  edges: KgEdgeRow[];
}

interface ChainEdgeDbRow extends QueryResultRow {
  depth: string | number;
  edge_id: string | number;
  relation: string;
  sub_relation: string | null;
  entity_ids: Array<string | number>;
  weight: number | null;
}

export async function getKgChain(
  namespace: KgNamespaceCode,
  chainId: string,
): Promise<KgChain> {
  const [chainRows, entities] = await Promise.all([
    querySqlView<ChainEdgeDbRow>("data_viz/api/services/kg/chain_edges", {
      namespace,
      chainId,
    }),
    listKgEntities(namespace),
  ]);
  const byId = new Map(entities.map((e) => [e.entityId, e]));
  const edges: KgEdgeRow[] = chainRows.map((r) => ({
    edgeId: Number(r.edge_id),
    relation: r.relation,
    subRelation: r.sub_relation,
    weight: r.weight === null ? null : Number(r.weight),
    members: (r.entity_ids ?? []).map((id) => {
      const entity = byId.get(Number(id));
      return {
        entityId: Number(id),
        name: entity?.name ?? null,
        entityType: entity?.entityType ?? null,
      };
    }),
  }));
  // Only the entities the chain actually touches — the node set of the
  // expanded row's subgraph.
  const used = new Set(edges.flatMap((e) => e.members.map((m) => m.entityId)));
  return {
    namespace,
    chainId,
    entities: entities.filter((e) => used.has(e.entityId)),
    edges,
  };
}
