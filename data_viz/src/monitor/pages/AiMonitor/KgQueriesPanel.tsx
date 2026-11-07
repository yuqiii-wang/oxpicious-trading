/**
 * KgQueriesPanel — the AI tab's knowledge-graph half, beneath the deep
 * agents runs table: the kg_search chain cache / log (llm.kg_queries via
 * GET /api/monitor/ai/kg-queries), one row per logged CHAIN — the exact
 * query text the agent asked, its namespace (0 DB semantics = "which
 * tables", 1 prompts = "which skill"), the chain id with its edge count,
 * the depth span (each logged row's depth is its edge's 0-based ordinal
 * in the chain), and the entity-description hash the chain stays valid
 * under (the same chain replays while the hash holds; a hash change
 * re-runs the traversal and relogs). Static seed data behind the graphs
 * means rows arrive only as agents search — a refresh button, no event
 * wiring. Clicking a row expands it: the chain's own subgraph
 * (GET /api/monitor/ai/kg-chain — edges resolved against the LIVE graph,
 * depth order) drawn through the Knowledge Graph tab's option builder.
 */
import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import {
  Box,
  CircularProgress,
  IconButton,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import { ExpandMore, Refresh } from "@mui/icons-material";
import {
  fetchAiKgChain,
  fetchAiKgQueries,
  type AiKgQueryEntry,
} from "@/lib/api-client/monitor";
import {
  BaseChart,
  useChartThemeMode,
  useChartData,
} from "@/shared/charts/base-chart";
import { buildKgGraphOption } from "@/config/pages/KnowledgeGraphPage/kgGraphOption";

const LIMIT = 50;

/** The kg_namespace vocabulary (the DDL's own codes — deliberately not a
 *  label_dim domain): what the row's subgraph is. */
const NS_LABELS: Record<number, string> = {
  0: "ns0 DB semantics",
  1: "ns1 prompts",
};

/** One expanded row's chain subgraph: fetched on mount (expand), drawn
 *  with the Knowledge Graph tab's builder — same nodes/edges shapes,
 *  same tooltips, force layout settled without animation. */
function ChainGraph({ namespace, chainId }: { namespace: number; chainId: string }) {
  const themeMode = useChartThemeMode();
  const { data, loading, error } = useChartData(
    () => fetchAiKgChain(namespace, chainId),
    [namespace, chainId],
  );
  const option = useMemo(
    () =>
      data != null && data.entities.length > 0
        ? buildKgGraphOption(data.entities, data.edges, themeMode)
        : null,
    [data, themeMode],
  );
  return (
    <BaseChart
      variant="bare"
      title={`chain ${chainId.slice(0, 8)} · ns${namespace} · ${
        data != null
          ? `${data.edges.length} edge${data.edges.length === 1 ? "" : "s"}`
          : "loading…"
      }`}
      loading={loading}
      error={error ? `Failed to load the chain graph: ${error}` : null}
      option={option}
      emptyText="Every edge of this chain has dropped out of the live graph — a fresh search will relog it."
      height={360}
    />
  );
}

export default function KgQueriesPanel() {
  const [rows, setRows] = useState<AiKgQueryEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());

  const toggle = useCallback((key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }, []);

  const refresh = useCallback(() => {
    fetchAiKgQueries(LIMIT)
      .then((body) => {
        setRows(body.rows);
        setError(null);
      })
      .catch((e: unknown) => setError(String(e)));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 2,
          flexWrap: "wrap",
        }}
      >
        <Typography variant="subtitle2">Knowledge graph</Typography>
        <Typography variant="caption" color="text.secondary">
          kg_search chains · llm.kg_queries — exact-ask cache, one chain per
          (query, namespace); click a row for its chain graph
        </Typography>
        <Tooltip title="Reload the kg_search chain log">
          <span>
            <IconButton
              size="small"
              aria-label="refresh kg queries"
              onClick={refresh}
              sx={{ ml: "auto" }}
            >
              <Refresh fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
      </Box>
      {error != null && (
        <Typography variant="caption" color="error">
          {error}
        </Typography>
      )}
      <Paper sx={{ overflowX: "auto" }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Logged</TableCell>
              <TableCell>Query</TableCell>
              <TableCell>Namespace</TableCell>
              <TableCell>Chain</TableCell>
              <TableCell align="right">Edges</TableCell>
              <TableCell align="right">Depth</TableCell>
              <TableCell>Entity hash</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {(rows ?? []).map((r) => {
              const key = `${r.namespace}:${r.chainId}`;
              const open = expanded.has(key);
              return (
                <Fragment key={key}>
                  <TableRow
                    hover
                    onClick={() => toggle(key)}
                    sx={{ cursor: "pointer" }}
                  >
                    <TableCell sx={{ whiteSpace: "nowrap" }}>
                      {r.loggedAt.slice(0, 16).replace("T", " ")}
                    </TableCell>
                    <TableCell>
                      <Typography
                        variant="caption"
                        sx={{
                          display: "inline-block",
                          maxWidth: 420,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                          verticalAlign: "bottom",
                        }}
                      >
                        {r.query}
                      </Typography>
                    </TableCell>
                    <TableCell sx={{ whiteSpace: "nowrap" }}>
                      {NS_LABELS[r.namespace] ?? `ns${r.namespace}`}
                    </TableCell>
                    <TableCell sx={{ fontFamily: "monospace", whiteSpace: "nowrap" }}>
                      <ExpandMore
                        fontSize="inherit"
                        sx={{
                          fontSize: 14,
                          verticalAlign: "-0.2em",
                          mr: 0.5,
                          transform: open ? "rotate(180deg)" : "none",
                          transition: "transform 120ms",
                        }}
                      />
                      {r.chainId.slice(0, 8)}
                    </TableCell>
                    <TableCell align="right">{r.edgeCount}</TableCell>
                    <TableCell align="right" sx={{ whiteSpace: "nowrap" }}>
                      {r.minDepth}–{r.maxDepth}
                    </TableCell>
                    <Tooltip title={r.entityHash}>
                      <TableCell sx={{ fontFamily: "monospace" }}>
                        {r.entityHash.slice(0, 8)}
                      </TableCell>
                    </Tooltip>
                  </TableRow>
                  {open && (
                    <TableRow>
                      <TableCell
                        colSpan={7}
                        sx={{ py: 1, bgcolor: "action.hover", px: 2 }}
                      >
                        <ChainGraph namespace={r.namespace} chainId={r.chainId} />
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              );
            })}
            {rows != null && rows.length === 0 && (
              <TableRow>
                <TableCell colSpan={7} align="center">
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ my: 2, display: "inline-block" }}
                  >
                    No kg_search chains logged yet — they appear as deep
                    agents search the knowledge graphs.
                  </Typography>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
        {rows == null && error == null && (
          <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
            <CircularProgress size={20} />
          </Box>
        )}
        {rows != null && rows.length > 0 && (
          <Box sx={{ display: "flex", alignItems: "center", px: 1.5, pb: 0.5 }}>
            <Typography variant="caption" color="text.secondary">
              {rows.length} chain{rows.length === 1 ? "" : "s"}
              {rows.length === LIMIT ? " (newest first, capped)" : " (newest first)"}
            </Typography>
          </Box>
        )}
      </Paper>
    </Box>
  );
}
