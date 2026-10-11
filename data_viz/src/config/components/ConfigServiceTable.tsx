/**
 * ConfigServiceTable — shared table shell for the config pages: a Paper
 * holding a named header box (table name + visible/total row count, with the
 * DirPathFilterSelect subtree filter and the search box pushed right) above
 * the <Table> itself.
 *
 * Owns the search + directory filter state and applies both to `rows` —
 * including the dir-staleness collapse after a save/delete removes the
 * selected directory (which also turns a `defaultDir` the tree doesn't
 * contain back into no-filter) — handing the filtered rows (and the current
 * search query, for empty-state copy) to the `children` render prop. The
 * <TableHead> and <TableBody> stay page-specific since each config table's
 * columns and row actions differ.
 */
import { useMemo, useState, type ReactNode } from "react";
import { Box, CircularProgress, Paper, Table, TextField, Typography } from "@mui/material";
import DirPathFilterSelect, {
  buildDirPaths,
  serviceInDir,
} from "@/config/components/DirPathFilterSelect";

interface ConfigServiceTableProps<R extends { service: string }> {
  /** Table name shown at the left of the header box. */
  name: string;
  /** Loaded rows, unfiltered — null while loading. */
  rows: readonly R[] | null;
  /** Row text the search box matches (service + request type, typically). */
  searchText: (r: R) => string;
  /** Cap the directory dropdown at this many dotted layers (undefined = all). */
  maxDepth?: number;
  /** Directory filter preselected on mount — ignored when the tree doesn't
   *  contain it (falls back to "(all)"). */
  defaultDir?: string;
  /** Search input placeholder. */
  searchPlaceholder?: string;
  children: (visible: R[], query: string) => ReactNode;
}

export default function ConfigServiceTable<R extends { service: string }>({
  name,
  rows,
  searchText,
  maxDepth,
  defaultDir,
  searchPlaceholder = "Search services…",
  children,
}: ConfigServiceTableProps<R>) {
  const [search, setSearch] = useState("");
  // Nested-directory subtree filter over the dotted service names, preset
  // to `defaultDir` (the staleness check below drops it if not a real dir).
  const [dir, setDir] = useState(defaultDir ?? "");

  const services = useMemo(() => (rows ?? []).map((r) => r.service), [rows]);
  const query = search.trim().toLowerCase();
  // A dir selection can go stale after a save/delete removes it — treat it
  // as no-filter then.
  const activeDir = buildDirPaths(services, maxDepth).includes(dir) ? dir : "";
  const visible = (rows ?? []).filter(
    (r) =>
      serviceInDir(r.service, activeDir) &&
      searchText(r).toLowerCase().includes(query),
  );

  return (
    <Paper sx={{ overflowX: "auto" }}>
      <Box
        sx={{
          px: 2,
          pt: 1.5,
          pb: 1,
          display: "flex",
          alignItems: "center",
          gap: 2,
          flexWrap: "wrap",
        }}
      >
        <Typography variant="subtitle2">{name}</Typography>
        <Typography variant="caption" color="text.secondary">
          {rows != null ? `${visible.length} of ${rows.length} rows` : "loading…"}
        </Typography>
        <Box sx={{ flexGrow: 1 }} />
        <DirPathFilterSelect
          services={services}
          value={activeDir}
          onChange={setDir}
          maxDepth={maxDepth}
        />
        <TextField
          size="small"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={searchPlaceholder}
          sx={{ width: 260 }}
        />
      </Box>
      {/* Rows still loading → centered spinner instead of an empty shell. */}
      {rows == null ? (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress size={24} />
        </Box>
      ) : (
        <Table size="small">{children(visible, search.trim())}</Table>
      )}
    </Paper>
  );
}
