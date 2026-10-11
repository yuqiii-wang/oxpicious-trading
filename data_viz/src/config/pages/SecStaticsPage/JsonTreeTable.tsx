/**
 * JsonTreeTable — the JSON popup's PLAIN ITEM TABLE: rows are the
 * document's items and every item's JSON fields render as extra TABLE
 * COLUMNS. No expansion anywhere — containers of spreadable objects
 * flatten in place to one plain row per child (replacing the older
 * one-expansion tree whose "industries" group had to be clicked open).
 *
 * Row rules:
 *   • object item → spread across the item columns (column set = union
 *     of the spread objects' keys, first-seen order); fields missing on
 *     an item leave a blank cell, array fields render as bullet lists
 *     and other container fields inline as compact JSON.
 *   • a container (object or array) whose every child is an object with
 *     only scalar/array fields flattens to one plain row per child —
 *     catalog "industries" → one row per industry code, the owners list
 *     → one row per owner; the flatten recurses, so nested flat
 *     containers are plain too.
 *   • any other container → one terminal row rendered inline as
 *     formatted JSON (bullet list / key-value pairs).
 *   • scalar item → value cell spanning the item columns.
 *
 * Read-only display — editing happens in the dialog's raw JSON mode.
 */
import { useMemo } from "react";
import {
  Box,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import {
  expandedTableBodyCellSx,
  expandedTableContainerSx,
  expandedTableHeadCellSx,
} from "@/shared/styles/expanded-table-styles";

interface JsonTreeRow {
  /** Path-based id ("$.industries.BAIJIU") — unique row key. */
  id: string;
  /** Display key — object key, or "[i]" for array elements. */
  key: string;
  value: unknown;
  /** Terminal row rendered across the item columns (plain object). */
  spread: boolean;
}

const isPlainObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const isScalar = (v: unknown): boolean =>
  v == null || typeof v === "string" || typeof v === "number" || typeof v === "boolean";

/** A plain object whose every field is a scalar or an array is fully
 *  shown by the item columns (arrays inline) — a single spread row. */
const isColumnSpreadable = (v: unknown): boolean =>
  isPlainObject(v) && Object.values(v).every((f) => isScalar(f) || Array.isArray(f));

/** A container whose every child is a column-spreadable object —
 *  flattens in place to one plain row per child, no expansion. */
const isFlatContainer = (v: unknown): boolean =>
  (isPlainObject(v) || Array.isArray(v)) &&
  Object.values(v).length > 0 &&
  Object.values(v).every(isColumnSpreadable);

function buildRows(value: unknown): JsonTreeRow[] {
  if (!isPlainObject(value) && !Array.isArray(value)) {
    return [{ id: "$", key: "value", value, spread: false }];
  }
  const rows: JsonTreeRow[] = [];
  const pushContainer = (container: unknown, parentId: string): void => {
    const isArray = Array.isArray(container);
    for (const [rawKey, v] of Object.entries(container as Record<string, unknown>)) {
      const id = `${parentId}.${rawKey}`;
      if (isFlatContainer(v)) {
        pushContainer(v, id);
        continue;
      }
      rows.push({
        id,
        key: isArray ? `[${rawKey}]` : rawKey,
        value: v,
        spread: isColumnSpreadable(v),
      });
    }
  };
  pushContainer(value, "$");
  return rows;
}

/** Leaf value cell text — JSON-ish styling per type (quoted muted strings,
 *  monospace numbers, colored booleans, italic null). `plain` drops the
 *  string quotes for formatted (non-JSON) contexts like list items. */
function LeafValue({ value, plain = false }: { value: unknown; plain?: boolean }) {
  if (value === null) {
    return (
      <Typography component="span" variant="inherit" sx={{ fontStyle: "italic", color: "text.disabled" }}>
        null
      </Typography>
    );
  }
  if (typeof value === "string") {
    if (plain) {
      return (
        <Typography component="span" variant="inherit" sx={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
          {value}
        </Typography>
      );
    }
    return (
      <Typography component="span" variant="inherit" sx={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
        <Box component="span" sx={{ color: "text.disabled" }}>&quot;</Box>
        {value}
        <Box component="span" sx={{ color: "text.disabled" }}>&quot;</Box>
      </Typography>
    );
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return (
      <Typography
        component="span"
        variant="inherit"
        sx={{
          fontFamily: "monospace",
          color: value === true ? "success.main" : value === false ? "error.main" : "info.main",
        }}
      >
        {String(value)}
      </Typography>
    );
  }
  return <Typography component="span" variant="inherit">{String(value)}</Typography>;
}

/** Inline compact JSON — terminal rendering for empty arrays and any
 *  container field of a spread row too deep to format. */
function InlineJson({ value }: { value: unknown }) {
  return (
    <Typography
      component="span"
      variant="inherit"
      sx={{
        fontFamily: "monospace",
        color: "text.secondary",
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
      }}
    >
      {JSON.stringify(value)}
    </Typography>
  );
}

/** Formatted object — "key: value" pairs joined by commas, muted keys,
 *  plain values; non-scalar values fall back to compact JSON. */
function ObjectPairs({ value }: { value: Record<string, unknown> }) {
  const entries = Object.entries(value);
  return (
    <Typography component="span" variant="inherit">
      {entries.map(([k, v], i) => (
        <Box component="span" key={k}>
          {i > 0 && <Box component="span" sx={{ color: "text.disabled" }}>, </Box>}
          <Box component="span" sx={{ color: "text.secondary" }}>
            {k}:{" "}
          </Box>
          {isScalar(v) ? <LeafValue value={v} plain /> : <InlineJson value={v} />}
        </Box>
      ))}
    </Typography>
  );
}

/** Formatted array — one bullet line per element (element objects as
 *  key/value pairs). */
function ArrayItems({ items }: { items: unknown[] }) {
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        alignItems: "flex-start",
        gap: 0.25,
      }}
    >
      {items.map((item, i) => (
        <Box key={i} sx={{ display: "flex", alignItems: "baseline", gap: 0.5 }}>
          <Box component="span" sx={{ color: "text.disabled", flexShrink: 0 }}>
            •
          </Box>
          <Box component="span">
            {isPlainObject(item) ? (
              <ObjectPairs value={item} />
            ) : isScalar(item) ? (
              <LeafValue value={item} plain />
            ) : (
              <InlineJson value={item} />
            )}
          </Box>
        </Box>
      ))}
    </Box>
  );
}

/** Terminal cell value — arrays and objects formatted (list / pairs),
 *  compact JSON only for containers too deep or empty to format. */
function CellValue({ value }: { value: unknown }) {
  if (isScalar(value)) return <LeafValue value={value} />;
  if (Array.isArray(value)) {
    return value.length > 0 ? (
      <ArrayItems items={value} />
    ) : (
      <InlineJson value={value} />
    );
  }
  if (isPlainObject(value) && Object.keys(value).length > 0) {
    return <ObjectPairs value={value} />;
  }
  return <InlineJson value={value} />;
}

export default function JsonTreeTable({
  value,
  maxHeight,
}: {
  value: unknown;
  /** Scroll viewport height — a number (px) or CSS length; omit for
   *  unbounded height. "100%" fills the dialog's flex body. */
  maxHeight?: number | string;
}) {
  const rows = useMemo(() => buildRows(value), [value]);

  /** Item columns — union of the spread rows' keys, first-seen order. */
  const itemColumns = useMemo(() => {
    const cols: string[] = [];
    const seen = new Set<string>();
    for (const row of rows) {
      if (row.spread && isPlainObject(row.value)) {
        for (const k of Object.keys(row.value)) {
          if (!seen.has(k)) {
            seen.add(k);
            cols.push(k);
          }
        }
      }
    }
    return cols;
  }, [rows]);

  /** Remaining cells after the Key column — colSpan for spanning rows. */
  const span = Math.max(1, itemColumns.length);

  return (
    <TableContainer
      sx={{ ...expandedTableContainerSx(maxHeight), flex: 1, minHeight: 0 }}
    >
      <Table size="small" stickyHeader>
        <TableHead>
          <TableRow>
            <TableCell sx={{ ...expandedTableHeadCellSx, minWidth: 180 }}>Key</TableCell>
            {itemColumns.length > 0 ? (
              itemColumns.map((col) => (
                <TableCell key={col} sx={expandedTableHeadCellSx}>
                  {col}
                </TableCell>
              ))
            ) : (
              <TableCell sx={expandedTableHeadCellSx}>Value</TableCell>
            )}
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.id}>
              <TableCell
                sx={{ ...expandedTableBodyCellSx, pl: 1, whiteSpace: "normal" }}
              >
                <Typography
                  component="span"
                  variant="inherit"
                  sx={{ fontFamily: "monospace" }}
                >
                  {row.key}
                </Typography>
              </TableCell>
              {row.spread && isPlainObject(row.value) ? (
                itemColumns.map((col) => {
                  const fieldValue = row.value[col];
                  return (
                    <TableCell
                      key={col}
                      sx={{ ...expandedTableBodyCellSx, whiteSpace: "normal" }}
                    >
                      {fieldValue === undefined ? null : <CellValue value={fieldValue} />}
                    </TableCell>
                  );
                })
              ) : (
                <TableCell
                  colSpan={span}
                  sx={{ ...expandedTableBodyCellSx, whiteSpace: "normal" }}
                >
                  <CellValue value={row.value} />
                </TableCell>
              )}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}
