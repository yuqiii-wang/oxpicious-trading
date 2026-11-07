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
 * READ-ONLY like the Keywords tab: the graph is a static seed — rows
 * land only via the SQL seed files, so the tab displays and filters
 * only (editing means editing the owning seed file and re-applying).
 * Data: GET /api/config/kg?namespace=N (plain fetch — the shared
 * client cache must never serve a pre-save state on these pages; the
 * server holds a 10-min TTL that the Reload button refreshes through).
 */
import { useMemo, useState } from "react";
import {
  Alert,
  Box,
  FormControl,
  IconButton,
  InputAdornment,
  InputLabel,
  MenuItem,
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
  type KgGraphResponse,
} from "@/lib/api-client/config";
import {
  BaseChart,
  useChartThemeMode,
  useChartData,
} from "@/shared/charts/base-chart";
import { buildKgGraphOption } from "./kgGraphOption";

/** The searched view of one namespace: the filtered subgraph the
 * canvas renders + how many entities matched. */
interface KgView {
  entities: KgGraphResponse["entities"];
  edges: KgEdge[];
  query: string;
}

/** The client-side subgraph filter: name / type / description / tag
 * substring (case-insensitive); edges keep their head-first shape —
 * an edge survives only with its subject, objects trim to survivors. */
function filterView(
  data: KgGraphResponse,
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
  const themeMode = useChartThemeMode();
  const {
    data,
    loading,
    error,
    refresh,
  } = useChartData(() => fetchKgGraph(namespace), [namespace]);

  const view = useMemo(
    () => (data != null ? filterView(data, search) : null),
    [data, search],
  );

  const option = useMemo(
    () =>
      view != null && view.entities.length > 0
        ? buildKgGraphOption(view.entities, view.edges, themeMode)
        : null,
    [view, themeMode],
  );

  const nsMeta = KG_NAMESPACES.find((n) => n.code === namespace);
  const searching = (view?.query ?? "").length > 0;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 2, flexWrap: "wrap" }}>
        <Typography variant="h6">Knowledge Graph</Typography>
        <Typography variant="caption" color="text.secondary">
          {view != null && data != null
            ? searching
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
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <TextField
              size="small"
              placeholder="Search name / tag / description…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              sx={{ width: 240 }}
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
          searching
            ? `No entity matches "${view?.query ?? search.trim()}" in this namespace — try a tag or a shorter substring.`
            : "This namespace has no seeded entities yet — apply the seeds under database/sql/llm/static/knowledge_graphs/"
        }
        height={680}
      />
    </Box>
  );
}
