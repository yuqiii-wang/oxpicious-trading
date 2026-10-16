/**
 * AdviceJustificationTable — one DYNAMIC justification table from the
 * analysis.analysis_advice payload: a columns spec (key/label/align/mono)
 * plus row arrays over the shared expanded-table visual baseline, so the
 * advice tables read like every other expanded table in the app. The
 * schema is free-form jsonb — the renderer stays defensive (unknown
 * aligns fall back left, missing cells render empty, null cells "—").
 */
import {
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
} from "@mui/material";
import {
  expandedTableBodyCellSx,
  expandedTableBodyRowSx,
  expandedTableContainerSx,
  expandedTableHeadCellSx,
  expandedTableNumCellSx,
} from "@/shared/styles/expanded-table-styles";
import type { AdviceJustificationTable } from "@/lib/api-client";

type CellAlign = "left" | "right" | "center";

const cellAlign = (align: unknown): CellAlign =>
  align === "right" || align === "center" ? align : "left";

const cellText = (v: unknown): string =>
  v == null ? "—" : String(v);

export default function AdviceJustificationTable({
  table,
}: {
  table: AdviceJustificationTable;
}) {
  if (table.columns.length === 0) return null;
  return (
    <TableContainer sx={expandedTableContainerSx()}>
      <Table size="small" stickyHeader>
        <TableHead>
          <TableRow>
            {table.columns.map((c) => (
              <TableCell
                key={c.key}
                align={cellAlign(c.align)}
                sx={expandedTableHeadCellSx}
              >
                {c.label}
              </TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {table.rows.map((row, i) => (
            <TableRow key={i} sx={expandedTableBodyRowSx(i)}>
              {table.columns.map((c, j) => (
                <TableCell
                  key={c.key}
                  align={cellAlign(c.align)}
                  sx={c.mono ? expandedTableNumCellSx : expandedTableBodyCellSx}
                >
                  {cellText(row[j])}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}
