/**
 * OracleCodeGroup — one security's hindsight-oracle panel: the pivot
 * chart (close curve + the shared buy/sell marker layer ON the local
 * extremes), the action-mix summary chips, and the latest pivots
 * table (the pivot date, action, the pivot's own close, the
 * favorable-move grade — "pending" on an unrealized frontier window —
 * and the hindsight swing target — the next opposite pivot with its
 * swing return; a buy dominated by a coming deeper low — the v6
 * prepare-to-buy-deep rule — never appears as a row). ONE
 * fetchOracleSeries call feeds all three.
 */
import { Alert, Box, Chip, Paper, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Typography } from "@mui/material";
import { useChartData } from "@/shared/charts/base-chart";
import { fetchOracleSeries, type OracleSecType } from "@/lib/api-client";
import type { OracleRow } from "@shared/types";
import OracleDecisionChart from "./OracleDecisionChart";
import { TABLE_ROWS } from "./constants";

interface Props {
  code: string;
  name: string;
  secType: OracleSecType;
}

/** The pct formatter for the swing return (fraction -> %). */
function fmtPct(v: number | null): string {
  return v == null ? "—" : `${(v * 100).toFixed(2)}%`;
}

export default function OracleCodeGroup({ code, name, secType }: Props) {
  const { data, loading, error } = useChartData(
    () => fetchOracleSeries(code, secType),
    [code, secType],
  );

  const buys = data ? data.rows.filter((r) => r.action === "buy").length : 0;
  const sells = data ? data.rows.filter((r) => r.action === "sell").length : 0;
  const latest = data ? data.rows.slice(-TABLE_ROWS).reverse() : [];

  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack direction="row" spacing={1} alignItems="baseline" sx={{ mb: 1 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
          {code}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {name}
        </Typography>
        {data && (
          <Stack direction="row" spacing={0.5} sx={{ ml: "auto" }}>
            <Chip size="small" color="success" variant="outlined"
              label={`buys ${buys}`} />
            <Chip size="small" color="error" variant="outlined"
              label={`sells ${sells}`} />
          </Stack>
        )}
      </Stack>
      {error && (
        <Alert severity="error" sx={{ mb: 1 }}>
          Failed to load oracle pivots: {error}
        </Alert>
      )}
      <OracleDecisionChart data={data} loading={loading} />
      <Box sx={{ mt: 1.5 }}>
        <Typography variant="caption" color="text.secondary">
          Latest local extremes (5-bar-window pivots: buy at every
          local minimum, sell at every local maximum; each side speaks
          only at a genuine extreme of its own horizon — a buy passing
          the genuine-deep-min test (drawdown into the pivot from the
          prior 20 rows' high 5%+; a shallower min omits only when a
          materially deeper level is imminent within 20 rows), a sell
          only at a genuine high (no 5%+ fall from the prior 20 rows'
          ceiling, no close 5%+ above within the next 20 rows) —
          anything less never appears as a row; each surviving pivot
          graded 0–100 by its favorable move on either side — 0.3 ×
          the move into it + 0.7 × the move after it, each leg ramped
          from the 2% no-action floor to 10% full; both legs below the
          floor emits nothing; the swing target is the next opposite
          pivot — the hindsight exit / re-entry)
        </Typography>
        <TableContainer sx={{ maxHeight: 320 }}>
          <Table size="small" stickyHeader>
            <TableHead>
              <TableRow>
                <TableCell>Pivot</TableCell>
                <TableCell>Action</TableCell>
                <TableCell align="right">Close</TableCell>
                <TableCell align="right">Conf</TableCell>
                <TableCell>Swing target</TableCell>
                <TableCell align="right">Off</TableCell>
                <TableCell align="right">Target px</TableCell>
                <TableCell align="right">Swing</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {latest.map((r) => (
                <PivotRow key={r.date} row={r} />
              ))}
              {latest.length === 0 && (
                <TableRow>
                  <TableCell colSpan={8}>
                    <Typography variant="caption" color="text.secondary">
                      No pivots — the code's history is shorter than one
                      pivot window or the pipeline has not run.
                    </Typography>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </TableContainer>
      </Box>
    </Paper>
  );
}

function PivotRow({ row }: { row: OracleRow }) {
  const swing = row.ext_price != null && row.close != null && row.close !== 0
    ? (row.action === "buy"
        ? row.ext_price / row.close - 1   // buy: exit / entry - 1
        : 1 - row.ext_price / row.close) // sell: sold-high vs re-entry
    : null;
  return (
    <TableRow>
      <TableCell>{row.date}</TableCell>
      <TableCell>
        <Chip
          size="small"
          color={row.action === "buy" ? "success" : "error"}
          label={row.action === "buy" ? "BUY" : "SELL"}
        />
      </TableCell>
      <TableCell align="right">
        {row.close != null ? row.close.toFixed(2) : "—"}
      </TableCell>
      <TableCell align="right">
        {row.confidence != null
          ? Math.round(row.confidence)
          : "pending"}
      </TableCell>
      <TableCell>{row.ext_date ?? "—"}</TableCell>
      <TableCell align="right">{row.ext_offset ?? "—"}</TableCell>
      <TableCell align="right">
        {row.ext_price != null ? row.ext_price.toFixed(2) : "—"}
      </TableCell>
      <TableCell align="right">{fmtPct(swing)}</TableCell>
    </TableRow>
  );
}
