/**
 * ForecastSignalsTable — the forecast register as a TRADING-SIGNALS-LIST
 * (the live LiveDataTradingSignalsPage's SignalTable idiom): ONE parent
 * row per CODE — the representative is the code's highest-confidence
 * projected signal — with a Count column (fired cells across the six σ
 * scenarios) and a "+N"-free Signal cell; EVERY parent row is CLICKABLE:
 * clicking toggles an expansion below it listing the code's fired
 * signals in the σ-ladder order (−1σ → +1σ → −2σ → …) plus the muted
 * "no signal" markers of its empty rungs. One row expanded at a time.
 *
 * Column set mirrors SignalTable: Scenario | Code | Signal | Action |
 * Signal value | Threshold | Excess | Confidence | regime | exec |
 * Count; the action rides the shared SignalActionChip (confidence =
 * the projected bp / 100) with a "gated" marker when the strategy
 * pickup would NOT execute the sell.
 */
import { Fragment, useMemo, useState } from "react";
import {
  Box,
  Chip,
  CircularProgress,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import { SignalActionChip } from "@/shared/components/signal-action";
import { regimeAccentColor } from "@/shared/charts/regimeBands";
import type { ForecastNextDayRow } from "@/lib/api-client";

/** The σ rung label (−kσ / +kσ). */
function rungLabel(direction: string, sigmaMult: number): string {
  return `${direction === "down" ? "−" : "+"}${sigmaMult}σ`;
}

/** The σ-ladder order inside one code's expansion: sigma_mult ASC
 *  (±1 → ±2 → ±3), down before up, trigger_no ASC. */
function sortRows(rows: ForecastNextDayRow[]): ForecastNextDayRow[] {
  return [...rows].sort(
    (a, b) =>
      a.code.localeCompare(b.code) ||
      a.sigma_mult - b.sigma_mult ||
      (a.direction === b.direction
        ? a.trigger_no - b.trigger_no
        : a.direction === "down"
          ? -1
          : 1),
  );
}

/** One parent group per code: its σ-ordered fired signals, the muted
 *  no-signal markers of its empty rungs, and the representative (the
 *  highest-confidence fired signal — or the first summary row when the
 *  code fires nothing at all). */
interface CodeGroup {
  code: string;
  fired: ForecastNextDayRow[];
  noSignal: ForecastNextDayRow[];
  rep: ForecastNextDayRow;
}

function groupByCode(rows: ForecastNextDayRow[]): CodeGroup[] {
  const sorted = sortRows(rows);
  const byCode = new Map<string, { fired: ForecastNextDayRow[]; noSignal: ForecastNextDayRow[] }>();
  for (const row of sorted) {
    const g = byCode.get(row.code) ?? { fired: [], noSignal: [] };
    if (row.trigger_no === 0) g.noSignal.push(row);
    else g.fired.push(row);
    byCode.set(row.code, g);
  }
  return [...byCode.entries()].map(([code, g]) => ({
    code,
    fired: g.fired,
    noSignal: g.noSignal,
    rep:
      g.fired.length > 0
        ? g.fired.reduce((best, r) =>
            (r.confidence_bp ?? 0) > (best.confidence_bp ?? 0) ? r : best,
          )
        : g.noSignal[0],
  }));
}

export function ForecastSignalsTable({
  rows,
  loading,
}: {
  /** The register rows (the latest anchor per code, or one code's). */
  rows: ForecastNextDayRow[];
  loading: boolean;
}) {
  const theme = useTheme();
  const [expandedCode, setExpandedCode] = useState<string | null>(null);
  const groups = useMemo(() => groupByCode(rows), [rows]);

  if (loading && rows.length === 0) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
        <CircularProgress size={28} />
      </Box>
    );
  }
  if (groups.length === 0) {
    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "center",
          py: 10,
          gap: 1,
        }}
      >
        <Typography variant="body2" color="text.secondary">
          No forecast registered yet — click Run Forecast.
        </Typography>
      </Box>
    );
  }
  return (
    <TableContainer component={Paper} variant="outlined">
      <Table size="small" stickyHeader>
        <TableHead>
          <TableRow>
            <Tooltip
              title="The representative row's σ rung (base close × (1 ± k·σ20)) — the code's highest-confidence projected signal; expand for every rung −1σ → +1σ → −2σ → …"
              arrow
            >
              <TableCell>Scenario</TableCell>
            </Tooltip>
            <TableCell>Code</TableCell>
            <TableCell>Signal</TableCell>
            <TableCell>Action</TableCell>
            <TableCell align="right">Signal value</TableCell>
            <TableCell align="right">Threshold</TableCell>
            <TableCell align="right">Excess</TableCell>
            <Tooltip
              title={
                "The strategy's expected favorable move (the cell's " +
                "sign-aligned dir_ave) amplified by the breach depth — " +
                "the projected confidence in basis points / 100"
              }
              arrow
            >
              <TableCell align="right">Confidence</TableCell>
            </Tooltip>
            <Tooltip
              title={
                "The day-context regime the cell matched (the anchor's own " +
                "stats.market_regimes label — tomorrow approximated by " +
                "today, a documented approximation)"
              }
              arrow
            >
              <TableCell align="center">regime</TableCell>
            </Tooltip>
            <Tooltip
              title={
                "The strategy pickup reading: buy events always execute " +
                "(long-only); sell events only with a position open past " +
                "min_holding_period — 'gated' marks a sell that would NOT"
              }
              arrow
            >
              <TableCell align="center">exec</TableCell>
            </Tooltip>
            <Tooltip
              title="Fired rule cells for the code across the selected σ scenarios — click the row to expand every rung's signals"
              arrow
            >
              <TableCell align="right">Count</TableCell>
            </Tooltip>
          </TableRow>
        </TableHead>
        <TableBody>
          {groups.map((g) => {
            const expanded = expandedCode === g.code;
            return (
              <Fragment key={g.code}>
                <TableRow
                  hover
                  onClick={() => setExpandedCode(expanded ? null : g.code)}
                  sx={{
                    cursor: "pointer",
                    ...(expanded && { backgroundColor: "action.hover" }),
                  }}
                >
                  {repCells(g.rep, theme)}
                  <TableCell
                    align="right"
                    sx={{
                      whiteSpace: "nowrap",
                      fontWeight: g.fired.length > 0 ? 600 : 400,
                      color: g.fired.length > 0
                        ? "text.primary"
                        : "text.secondary",
                    }}
                  >
                    {g.fired.length}
                  </TableCell>
                </TableRow>
                {expanded && (
                  <>
                    {g.fired.map((row, i) => (
                      <TableRow
                        key={`${row.direction}-${row.sigma_mult}-${row.trigger_no}-${i}`}
                        sx={{ "& td": { bgcolor: "background.default" } }}
                      >
                        {detailCells(row, theme)}
                      </TableRow>
                    ))}
                    {g.noSignal.map((row, i) => (
                      <TableRow
                        key={`ns-${row.direction}-${row.sigma_mult}-${i}`}
                        sx={{ "& td": { bgcolor: "background.default" } }}
                      >
                        <TableCell sx={{ pl: 6, whiteSpace: "nowrap" }}>
                          <Box component="span" sx={{ color: "text.secondary" }}>
                            {rungLabel(row.direction, row.sigma_mult)}
                          </Box>
                          <Box
                            component="span"
                            sx={{ color: "text.disabled", ml: 1 }}
                          >
                            {Number(row.forecast_close).toFixed(2)}
                          </Box>
                        </TableCell>
                        <TableCell colSpan={9} />
                      </TableRow>
                    ))}
                  </>
                )}
              </Fragment>
            );
          })}
        </TableBody>
      </Table>
    </TableContainer>
  );

  // -- the parent row (the representative's cells + the count rides the
  //    caller's last cell above) -----------------------------------------

  function repCells(rep: ForecastNextDayRow, th: typeof theme) {
    if (rep.trigger_no === 0) {
      // a code whose every scenario fires nothing — the muted marker row
      return (
        <>
          <TableCell sx={{ whiteSpace: "nowrap", pl: 2 }}>
            <Box component="span" sx={{ fontWeight: 600 }}>
              {rungLabel(rep.direction, rep.sigma_mult)}
            </Box>
            <Box component="span" sx={{ color: "text.secondary", ml: 1 }}>
              {Number(rep.forecast_close).toFixed(2)}
            </Box>
          </TableCell>
          <TableCell>{rep.code}</TableCell>
          <TableCell
            colSpan={8}
            sx={{ color: "text.secondary", fontStyle: "italic" }}
          >
            no signal
          </TableCell>
        </>
      );
    }
    return detailCells(rep, th, false);
  }

  // -- the 10 shared data cells (scenario .. exec) ------------------------

  function detailCells(
    s: ForecastNextDayRow,
    th: typeof theme,
    indented = true,
  ) {
    return (
      <>
        <Tooltip
          title={[
            `anchor ${s.anchor_date}`,
            `base close ${Number(s.base_close).toFixed(2)}`,
            `σ20 ${Number(s.sigma_20d_pct).toFixed(3)}%`,
            `change ${Number(s.forecast_change_pct) >= 0 ? "+" : ""}${Number(s.forecast_change_pct).toFixed(2)}%`,
          ].join(" · ")}
          arrow
        >
          <TableCell sx={{ whiteSpace: "nowrap", pl: indented ? 6 : 2 }}>
            <Box component="span" sx={{ fontWeight: indented ? 400 : 600 }}>
              {rungLabel(s.direction, s.sigma_mult)}
            </Box>
            <Box component="span" sx={{ color: "text.secondary", ml: 1 }}>
              {Number(s.forecast_close).toFixed(2)}
            </Box>
          </TableCell>
        </Tooltip>
        <TableCell>{s.code}</TableCell>
        <TableCell sx={{ whiteSpace: "nowrap" }}>
          {s.signal_type} · {s.signal_sub_type}
        </TableCell>
        <TableCell>
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
            <SignalActionChip
              action={s.action ?? ""}
              confidence={s.confidence_bp != null ? s.confidence_bp / 100 : null}
            />
            {s.would_execute === false && (
              <Tooltip
                title="The strategy pickup would NOT execute this sell (no position open past min_holding_period)"
                arrow
              >
                <Chip
                  size="small"
                  label="gated"
                  variant="outlined"
                  sx={{ height: 18, fontSize: "0.65rem" }}
                />
              </Tooltip>
            )}
          </Box>
        </TableCell>
        <TableCell align="right">
          {s.signal_value != null ? Number(s.signal_value).toFixed(4) : "—"}
        </TableCell>
        <TableCell align="right">
          {s.signal_threshold != null
            ? Number(s.signal_threshold).toFixed(4)
            : "—"}
        </TableCell>
        <TableCell
          align="right"
          sx={{
            whiteSpace: "nowrap",
            color: s.action === "buy"
              ? th.palette.success.main
              : th.palette.error.main,
            fontWeight: 600,
          }}
        >
          {Number(s.signal_excess_pct ?? 0) >= 0 ? "▲ " : "▼ "}
          {s.signal_excess_pct != null
            ? `${Number(s.signal_excess_pct).toFixed(2)}%`
            : "—"}
        </TableCell>
        <TableCell
          align="right"
          sx={{ whiteSpace: "nowrap", color: th.palette.text.secondary }}
        >
          {s.confidence_bp != null
            ? `${(s.confidence_bp / 100).toFixed(2)}%`
            : "—"}
        </TableCell>
        <TableCell align="center">
          <Box
            component="span"
            sx={{
              color: regimeAccentColor(s.regime_state ?? "") ?? "text.disabled",
              fontWeight: 600,
            }}
          >
            {s.regime_state === "calm" ? "·" : s.regime_state}
          </Box>
        </TableCell>
        <TableCell align="center">
          {s.would_execute == null ? "—" : s.would_execute ? "yes" : "no"}
        </TableCell>
      </>
    );
  }
}
