/**
 * ForecastSignalsTable — the forecast register as a TRADING-SIGNALS-LIST
 * (the live LiveDataTradingSignalsPage's SignalTable idiom): ONE parent
 * row per (SCENARIO, CODE) — the scenario is the σ rung (−kσ / +kσ) —
 * grouped SCENARIO-major in the σ-ladder order (−1σ → +1σ → −2σ → …),
 * code ASC inside each scenario (a heavier rule opens every scenario
 * block). The parent row's representative is the scenario's
 * highest-confidence projected signal for the code, with a Count column
 * (the scenario's fired cells for the code) and a "+N"-free Signal cell.
 * A row holding MORE THAN ONE fired cell is CLICKABLE: clicking toggles
 * an expansion below it listing every fired cell, trigger_no ASC. One
 * row expanded at a time. A (scenario, code) that fires nothing is its
 * own muted "no signal" row (not clickable) — the scenario's trigger_no
 * 0 summary row carries the projected close.
 *
 * Column set mirrors SignalTable: Scenario | Code | Signal | Action |
 * Signal value | Threshold | Excess | Confidence | regime | exec |
 * Count; the action rides the shared SignalActionChip (confidence =
 * the projected bp / 100) with a "gated" marker when the strategy
 * pickup would NOT execute the sell. The expansion ALSO renders the
 * shared SignalCodeTrendChart (the live expansion's singleton signal
 * plot) for the code anchored on the scenario's anchor date — the
 * singleton signal history as buy/sell markers beside the projected
 * cells; the chart self-fetches and degrades to the plain trend on a
 * history failure, never blocking the rows.
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
import { SignalCodeTrendChart } from "@/shared/components/signal-code-trend";
import { regimeAccentColor } from "@/shared/charts/regimeBands";
import { fmtDate } from "@/shared/datetime";
import type { CodeTrendSecType } from "@/ui/OhlcChart";
import type { ForecastNextDayRow } from "@/lib/api-client";

/** The σ rung label (−kσ / +kσ) — the scenario's name. */
function rungLabel(direction: string, sigmaMult: number): string {
  return `${direction === "down" ? "−" : "+"}${sigmaMult}σ`;
}

/** The rung's σ-ladder position: sigma_mult ASC (±1 → ±2 → ±3), down
 *  before up. */
function rungOrder(direction: string, sigmaMult: number): number {
  return sigmaMult * 2 + (direction === "down" ? 0 : 1);
}

/** The Code cell — the code with its display name beside it. */
function codeCell(code: string, name: string | null) {
  return (
    <TableCell sx={{ whiteSpace: "nowrap" }}>
      {code}
      {name ? (
        <Box component="span" sx={{ color: "text.secondary", ml: 1 }}>
          {name}
        </Box>
      ) : null}
    </TableCell>
  );
}

/** The table's row order — scenario-major (the σ ladder: −1σ → +1σ →
 *  −2σ → …), code ASC inside a scenario, trigger_no ASC inside a
 *  (scenario, code). */
function sortRows(rows: ForecastNextDayRow[]): ForecastNextDayRow[] {
  return [...rows].sort(
    (a, b) =>
      rungOrder(a.direction, a.sigma_mult) -
        rungOrder(b.direction, b.sigma_mult) ||
      a.code.localeCompare(b.code) ||
      a.trigger_no - b.trigger_no,
  );
}

/** One parent group per (scenario, code): the scenario's fired signals
 *  (trigger_no ASC), its trigger_no 0 summary row (the pipeline
 *  registers one per scenario — the no-signal group's own row), and the
 *  representative (the highest-confidence fired signal, else the summary
 *  row). */
interface ScenarioCodeGroup {
  /** (anchor, scenario, code) — the expansion identity. */
  key: string;
  /** (anchor, scenario) — the block a new group opens. */
  scenarioKey: string;
  fired: ForecastNextDayRow[];
  summary: ForecastNextDayRow | null;
  rep: ForecastNextDayRow;
}

function groupByScenarioCode(
  rows: ForecastNextDayRow[],
): ScenarioCodeGroup[] {
  // insertion-ordered — the scenario-major sort's order
  const groups = new Map<string, ScenarioCodeGroup>();
  for (const row of sortRows(rows)) {
    const scenarioKey =
      `${row.anchor_date}|${row.direction}|${row.sigma_mult}`;
    const key = `${scenarioKey}|${row.code}`;
    let g: ScenarioCodeGroup | undefined = groups.get(key);
    if (!g) {
      g = { key, scenarioKey, fired: [], summary: null, rep: row };
      groups.set(key, g);
    }
    if (row.trigger_no === 0) g.summary = row;
    else g.fired.push(row);
  }
  return [...groups.values()].map((g) => ({
    ...g,
    rep:
      g.fired.length > 0
        ? g.fired.reduce((best, r) =>
            (r.confidence_bp ?? 0) > (best.confidence_bp ?? 0) ? r : best,
          )
        : (g.summary ?? g.rep),
  }));
}

/** The Scenario cell — the σ rung + the projected close, the anchor's
 *  scenario stats in the tooltip. Indented + regular weight on the
 *  expansion rows, flush + bold on the parent rows. */
function scenarioCell(s: ForecastNextDayRow, indented = false) {
  return (
    <Tooltip
      title={[
        `anchor ${fmtDate(s.anchor_date)}`,
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
  );
}

/** The no-signal row's cells — the scenario + the code, then the muted
 *  marker spanning the signal columns (the caller's Count cell closes
 *  the row). */
function summaryCells(s: ForecastNextDayRow) {
  return (
    <>
      {scenarioCell(s)}
      {codeCell(s.code, s.code_name)}
      <TableCell
        colSpan={8}
        sx={{ color: "text.secondary", fontStyle: "italic" }}
      >
        no signal
      </TableCell>
    </>
  );
}

export function ForecastSignalsTable({
  rows,
  loading,
  secType,
}: {
  /** The register rows (the latest anchor per code, or one code's). */
  rows: ForecastNextDayRow[];
  loading: boolean;
  /** The register's security type — the signal plot's baseline source. */
  secType: CodeTrendSecType;
}) {
  const theme = useTheme();
  const [expandedKey, setExpandedKey] = useState<string | null>(null);
  const groups = useMemo(() => groupByScenarioCode(rows), [rows]);

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
              title="The σ scenario — the next-day close = base close × (1 ± k·σ20). Rows group by scenario, then code; the row shows the scenario's highest-confidence projected signal for the code"
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
              title="Fired rule cells for this scenario × code — a row holding more than one expands on click"
              arrow
            >
              <TableCell align="right">Count</TableCell>
            </Tooltip>
          </TableRow>
        </TableHead>
        <TableBody>
          {groups.map((g, i) => {
            const expanded = expandedKey === g.key;
            const noSignal = g.fired.length === 0;
            // only a multi-signal scenario holds collapsed rows to show
            const expandable = g.fired.length > 1;
            // a scenario block's first row opens on a heavier rule
            const opensScenario =
              i > 0 && groups[i - 1].scenarioKey !== g.scenarioKey;
            return (
              <Fragment key={g.key}>
                <TableRow
                  hover={expandable}
                  onClick={
                    expandable
                      ? () => setExpandedKey(expanded ? null : g.key)
                      : undefined
                  }
                  sx={{
                    ...(opensScenario && {
                      "& .MuiTableCell-root": {
                        borderTop: "2px solid",
                        borderColor: "divider",
                      },
                    }),
                    ...(expandable && { cursor: "pointer" }),
                    ...(expanded && { backgroundColor: "action.hover" }),
                  }}
                >
                  {noSignal
                    ? summaryCells(g.rep)
                    : detailCells(g.rep, theme, false)}
                  <TableCell
                    align="right"
                    sx={{
                      whiteSpace: "nowrap",
                      fontWeight: noSignal ? 400 : 600,
                      color: noSignal ? "text.secondary" : "text.primary",
                    }}
                  >
                    {g.fired.length}
                  </TableCell>
                </TableRow>
                {expanded &&
                  g.fired.map((row) => (
                    <TableRow
                      key={`${g.key}|${row.trigger_no}`}
                      sx={{ "& td": { bgcolor: "background.default" } }}
                    >
                      {detailCells(row, theme)}
                    </TableRow>
                  ))}
                {expanded && (
                  <TableRow>
                    <TableCell
                      colSpan={11}
                      sx={{ p: 0, border: "none", bgcolor: "background.default" }}
                    >
                      <Box sx={{ px: 1.5, py: 1.5 }}>
                        <SignalCodeTrendChart
                          secType={secType}
                          code={g.rep.code}
                          name={g.rep.code_name ?? undefined}
                          anchorDate={g.rep.anchor_date}
                          height={320}
                        />
                      </Box>
                    </TableCell>
                  </TableRow>
                )}
              </Fragment>
            );
          })}
        </TableBody>
      </Table>
    </TableContainer>
  );

  // -- the 10 shared data cells (scenario .. exec) ------------------------

  function detailCells(
    s: ForecastNextDayRow,
    th: typeof theme,
    indented = true,
  ) {
    return (
      <>
        {scenarioCell(s, indented)}
        {codeCell(s.code, s.code_name)}
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
