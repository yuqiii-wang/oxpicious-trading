/**
 * Knowledge Graph tab — the knowledge hypergraph (llm.kg_entities /
 * llm.kg_edges, DDL database/sql/llm/13_kg_hypergraph.sql, seeds
 * database/sql/llm/static/knowledge_graphs/) as one force-directed
 * canvas per NAMESPACE — the namespace filter is the tab's only
 * server-facing control: a namespace IS one source corpus's subgraph
 * (0 DB semantics — one entity per table/view | 1 prompts — one entity
 * per cataloged skill/plan prompt; the kg_namespace codes mirror the
 * deep-agent kit's kg_search/kg_expand until label_dim registers the
 * domain with the first KG writer). Entities render as circles colored
 * by entity_type (the chart legend toggles a type), sized by degree;
 * each hyperedge draws head-first (subject → one arrow per object) and
 * the tooltip carries the full ordered member list + relation.
 *
 * The SEARCH BAR narrows the canvas client-side (the graph is static
 * seed data — no server param): an entity matches when its name,
 * entity_type, description or any tag contains the query
 * (case-insensitive); the option rebuilds over the matched subgraph —
 * an edge survives only with its HEAD entity (members[0] = subject;
 * objects trim to survivors so no arrow points at a dropped node).
 *
 * The TABLE-SCOPE DROPDOWN (2026-11-11) previews the deep-agent run's
 * FORCED-TABLE-SCOPE with the SAME rule the KG serving layer applies
 * (llm_agents/kg service): the ticked names keep their entities
 * (schema-qualified or bare name, case-insensitive), then every
 * NON-table entity riding an edge whose OTHER members all kept (the
 * reading sql_views/DB views, the typing label domains), and the edges
 * prune to the survivors — what the canvas shows here is exactly what
 * a run forced to these tables would search. The picker is the SHARED
 * KgTableScopeSelect (the same nested schema → tables tick hierarchy
 * the AI summary Generate dialog mounts), fed by THIS namespace's
 * loaded table entities so the dropdown always mirrors the canvas;
 * ns1 is the prompt catalog (no tables — the serving layer exempts
 * it), so the dropdown disables there. The scope narrows FIRST, the
 * search filters within it.
 *
 * The PAGINATION (2026-11-13) splits the FILTERED entity list into
 * 20-per-page windows — the canvas renders one window at a time (the
 * force layout degrades past a few dozen nodes). The window is the
 * LAST narrowing: scope first, search within it, then the page slice;
 * an edge survives only with its HEAD entity on the page (objects
 * trim to page survivors, >= 2 members remain — the search rule), so
 * no arrow points off-page. The page resets to 1 whenever the
 * namespace, table scope or search changes.
 *
 * READ-ONLY like the Keywords tab: the graph is a static seed — rows
 * land only via the SQL seed files, so the tab displays and filters
 * only (editing means editing the owning seed file and re-applying).
 * Data: GET /api/config/kg?namespace=N (plain fetch — the shared
 * client cache must never serve a pre-save state on these pages; the
 * server holds a 10-min TTL that the Reload button refreshes through).
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  FormControl,
  IconButton,
  InputAdornment,
  InputLabel,
  MenuItem,
  Pagination,
  Select,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import Clear from "@mui/icons-material/Clear";
import Refresh from "@mui/icons-material/Refresh";
import Search from "@mui/icons-material/Search";
import {
  fetchKgGraph,
  KG_NAMESPACES,
  type KgEdge,
  type KgEntity,
  type KgGraphResponse,
} from "@/lib/api-client/config";
import {
  BaseChart,
  useChartThemeMode,
  useChartData,
} from "@/shared/charts/base-chart";
import KgTableScopeSelect, {
  type KgSchemaGroup,
} from "@/shared/components/kg-table-scope";
import { buildKgGraphOption } from "./kgGraphOption";

/** The searched view of one namespace: the filtered subgraph the
 * canvas renders + how many entities matched. */
interface KgView {
  entities: KgGraphResponse["entities"];
  edges: KgEdge[];
  query: string;
}

/** Entities per page window — the canvas renders one window at a time. */
const PAGE_SIZE = 20;

/** The client-side FORCED-TABLE-SCOPE derivation — the serving rule's
 * twin: the ticked names keep their entities, non-table riders of
 * all-else-kept edges come along, edges prune to survivors. Returns
 * null when nothing is ticked (no narrowing). */
function scopeNarrow(
  data: Pick<KgGraphResponse, "entities" | "edges">,
  selection: string[],
): Pick<KgGraphResponse, "entities" | "edges"> | null {
  const tokenSet = new Set(
    selection.map((t) => t.trim().toLowerCase()).filter((t) => t !== ""),
  );
  if (tokenSet.size === 0) return null;
  const kept = new Map<number, KgEntity>();
  for (const e of data.entities) {
    const name = e.name.toLowerCase();
    const dot = name.lastIndexOf(".");
    const bare = dot >= 0 ? name.slice(dot + 1) : name;
    if (tokenSet.has(name) || tokenSet.has(bare)) kept.set(e.entityId, e);
  }
  for (const edge of data.edges) {
    const unknown = edge.members.filter((m) => !kept.has(m.entityId));
    if (unknown.length !== 1) continue;
    const rider = unknown[0];
    if (rider.entityType !== null && rider.entityType !== "table") {
      const full = data.entities.find((e) => e.entityId === rider.entityId);
      if (full !== undefined) kept.set(full.entityId, full);
    }
  }
  return {
    entities: data.entities.filter((e) => kept.has(e.entityId)),
    edges: data.edges.filter(
      (e) =>
        e.members.length > 0 &&
        e.members.every((m) => kept.has(m.entityId)),
    ),
  };
}

/** The client-side subgraph filter: name / type / description / tag
 * substring (case-insensitive); edges keep their head-first shape —
 * an edge survives only with its subject, objects trim to survivors. */
function filterView(
  data: Pick<KgGraphResponse, "entities" | "edges">,
  rawQuery: string,
): KgView {
  const query = rawQuery.trim().toLowerCase();
  if (!query) {
    return { entities: data.entities, edges: data.edges, query: "" };
  }
  const entities = data.entities.filter(
    (e) =>
      e.name.toLowerCase().includes(query) ||
      e.entityType.toLowerCase().includes(query) ||
      (e.description ?? "").toLowerCase().includes(query) ||
      e.tags.some((t) => t.toLowerCase().includes(query)),
  );
  const ids = new Set(entities.map((e) => e.entityId));
  const edges = data.edges
    .filter((e) => e.members.length > 0 && ids.has(e.members[0].entityId))
    .map((e) => ({
      ...e,
      members: e.members.filter((m) => ids.has(m.entityId)),
    }))
    .filter((e) => e.members.length >= 2);
  return { entities, edges, query: rawQuery.trim() };
}

export default function KnowledgeGraphPage() {
  const [namespace, setNamespace] = useState<number>(KG_NAMESPACES[0].code);
  const [search, setSearch] = useState("");
  const [tableScope, setTableScope] = useState<string[]>([]);
  const [page, setPage] = useState(1);
  const themeMode = useChartThemeMode();
  const {
    data,
    loading,
    error,
    refresh,
  } = useChartData(() => fetchKgGraph(namespace), [namespace]);

  // The window narrows with everything upstream — reset to the first
  // page whenever the namespace, table scope or search changes (a
  // stale page would slice past a shrunken match list).
  useEffect(() => {
    setPage(1);
  }, [namespace, tableScope, search]);

  // The forced-table-scope preview narrows FIRST; the search filters
  // within the scoped subgraph.
  const scoped = useMemo(
    () => (data != null ? scopeNarrow(data, tableScope) : null),
    [data, tableScope],
  );
  const view = useMemo(() => {
    if (data == null) return null;
    const base = scoped ?? {
      entities: data.entities,
      edges: data.edges,
    };
    return filterView(base, search);
  }, [data, scoped, search]);

  // The LAST narrowing: slice the filtered entities to the page
  // window; edges prune the search rule's way — an edge survives only
  // with its HEAD entity on the page, objects trim to page survivors,
  // >= 2 members remain (no arrow points off-page).
  const totalPages =
    view != null ? Math.max(1, Math.ceil(view.entities.length / PAGE_SIZE)) : 1;
  const curPage = Math.min(page, totalPages);
  const paged = useMemo(() => {
    if (view == null) return null;
    const entities = view.entities.slice(
      (curPage - 1) * PAGE_SIZE,
      curPage * PAGE_SIZE,
    );
    const ids = new Set(entities.map((e) => e.entityId));
    const edges = view.edges
      .filter((e) => e.members.length > 0 && ids.has(e.members[0].entityId))
      .map((e) => ({
        ...e,
        members: e.members.filter((m) => ids.has(m.entityId)),
      }))
      .filter((e) => e.members.length >= 2);
    return { entities, edges };
  }, [view, curPage]);

  // The shared picker's option list: THIS namespace's TABLE entities,
  // grouped by schema (the dropdown mirrors the canvas data; ns1 has
  // none — the dropdown disables there). A selection made under one
  // namespace survives the switch — the rule matches by name whatever
  // namespace renders.
  const scopeGroups = useMemo<KgSchemaGroup[]>(() => {
    if (data == null || namespace === 1) return [];
    const bySchema = new Map<string, string[]>();
    for (const e of data.entities) {
      if (e.entityType !== "table") continue;
      const dot = e.name.indexOf(".");
      const schema = dot >= 0 ? e.name.slice(0, dot) : e.name;
      const bucket = bySchema.get(schema);
      if (bucket === undefined) bySchema.set(schema, [e.name]);
      else bucket.push(e.name);
    }
    return [...bySchema.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([schema, names]) => ({
        schema,
        tables: names.sort((a, b) => a.localeCompare(b)),
      }));
  }, [data, namespace]);

  const option = useMemo(
    () =>
      paged != null && paged.entities.length > 0
        ? buildKgGraphOption(paged.entities, paged.edges, themeMode)
        : null,
    [paged, themeMode],
  );

  const nsMeta = KG_NAMESPACES.find((n) => n.code === namespace);
  const searching = (view?.query ?? "").length > 0;
  const scoping = tableScope.length > 0;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 2, flexWrap: "wrap" }}>
        <Typography variant="h6">Knowledge Graph</Typography>
        <Typography variant="caption" color="text.secondary">
          {view != null && data != null
            ? searching && scoping
              ? `${view.entities.length} / ${data.entities.length} entities match "${view.query}" within the table scope`
              : scoping
                ? `${view.entities.length} / ${data.entities.length} entities in the table scope · ${view.edges.length} hyperedges`
                : searching
                  ? `${view.entities.length} / ${data.entities.length} entities match "${view.query}"`
                  : `${data.entities.length} entities · ${data.edges.length} hyperedges · source: database/sql/llm/static/knowledge_graphs/`
            : "loading…"}
        </Typography>
        <Box sx={{ flexGrow: 1 }} />
        <Tooltip title="Reload the namespace's subgraph from the DB">
          <span>
            <IconButton size="small" aria-label="reload" onClick={refresh}>
              <Refresh fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
      </Box>

      {error && <Alert severity="error">{error}</Alert>}

      <BaseChart
        variant="card"
        title={`ns${namespace} · ${nsMeta?.label ?? ""}`}
        subtitle={nsMeta?.hint}
        headerAction={
          <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
            <Tooltip title="Forced table scope preview — tick the tables (grouped by schema; selected names render as chips). The canvas narrows to them plus their reading views / label domains: exactly what a deep-agent run forced to these tables may search. ns1 (the prompt catalog) has no tables — the serving layer exempts it.">
              <Box sx={{ display: "inline-flex", width: 420, flexShrink: 0 }}>
                <KgTableScopeSelect
                  value={tableScope}
                  onChange={setTableScope}
                  groups={scopeGroups}
                  disabled={namespace === 1}
                  placeholder={
                    namespace === 1
                      ? "Table scope — ns1 exempt"
                      : "Table scope: tick tables…"
                  }
                />
              </Box>
            </Tooltip>
            <TextField
              size="small"
              placeholder="Search name / tag / description…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              sx={{ width: 240, flexShrink: 0 }}
              InputProps={{
                sx: { fontSize: "0.78rem" },
                startAdornment: (
                  <InputAdornment position="start">
                    <Search fontSize="small" />
                  </InputAdornment>
                ),
                endAdornment: search ? (
                  <InputAdornment position="end">
                    <IconButton
                      size="small"
                      aria-label="clear search"
                      onClick={() => setSearch("")}
                    >
                      <Clear fontSize="small" />
                    </IconButton>
                  </InputAdornment>
                ) : null,
              }}
            />
            <FormControl size="small" sx={{ minWidth: 200 }}>
              <InputLabel sx={{ fontSize: "0.78rem" }}>Namespace</InputLabel>
              <Select
                value={namespace}
                label="Namespace"
                onChange={(e) => setNamespace(Number(e.target.value))}
                sx={{ fontSize: "0.78rem" }}
              >
                {KG_NAMESPACES.map((n) => (
                  <MenuItem key={n.code} value={n.code} sx={{ fontSize: "0.78rem" }}>
                    {n.code} · {n.label}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          </Box>
        }
        loading={loading}
        error={error ? `Failed to load the knowledge graph: ${error}` : null}
        option={option}
        emptyText={
          scoping && (view?.entities.length ?? 0) === 0
            ? `No entity matches the table scope (${tableScope.join(", ")}) in this namespace — untick or switch namespace.`
            : searching
              ? `No entity matches "${view?.query ?? search.trim()}" in this namespace — try a tag or a shorter substring.`
              : "This namespace has no seeded entities yet — apply the seeds under database/sql/llm/static/knowledge_graphs/"
        }
        height={680}
      />

      {view != null && totalPages > 1 && (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 2,
          }}
        >
          <Typography variant="caption" color="text.secondary">
            {paged?.entities.length ?? 0} of {view.entities.length} entities on
            page {curPage}/{totalPages} · {PAGE_SIZE} / page
          </Typography>
          <Pagination
            count={totalPages}
            page={curPage}
            onChange={(_, v) => setPage(v)}
            size="small"
            siblingCount={1}
            boundaryCount={1}
          />
        </Box>
      )}
    </Box>
  );
}
