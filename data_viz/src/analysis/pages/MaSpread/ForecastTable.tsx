/**
 * ForecastTable — the MA-Spread panel's 2nd plot: one code's extreme-day
 * bucket table from the analysis_forecasts schema, chosen by the parent's
 * dropdown (mov_rsi = RSI extreme-percentile buckets, mov_std = Bollinger
 * breach buckets).
 *
 * Each row is one bucket (config cols + is_market_hyped [+ excess cols for
 * mov_std]) mapped via forecast_id to its forecast_results columns — 4
 * forward horizons (next / 5d / 20d / 60d) with mean fractional change +
 * P(>1% reversal against the bucket side); the 5d/20d/60d horizons add
 * close-based max/min changes and the within-window close swing
 * amplitude (max_low_change_ratio), the next-day horizon does not.
 *
 * A ToggleButtonGroup above the table selects which horizon's result
 * columns are shown (Next / 5d / 20d / 60d); the horizon's sub-column
 * layout differs accordingly (Next: mean / P>1% / days — the others add
 * max / min / max-low). All fractional values render as
 * percentages; changes are signed (green + / red −), probabilities plain.
 * The first column shows the bucket's trailing 5-year window as a
 * "start → end" year-month period label. Sticky header inside a
 * max-height scroll container; rows sorted stat_month DESC then config.
 * While the fetch is in flight a spinner replaces the table.
 */
import { useEffect, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  FormControl,
  MenuItem,
  Select,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { UP_COLOR, DOWN_COLOR } from "@/theme/chart-palette";
import { fetchMovAveSpreadForecast } from "@/lib/api-client";
import type {
  ForecastKind,
  ForecastResponse,
  MaSpreadSecType,
  MovRsiForecastRow,
  MovStdForecastRow,
} from "@shared/types";
import { expandedTableContainerSx } from "@/shared/styles/expanded-table-styles";

/** Fractional change → signed % string, colored green/red. */
function ChangeCell({ v }: { v: number | null }) {
  if (v == null || !Number.isFinite(v)) {
    return <Typography component="span" variant="inherit" color="text.disabled">—</Typography>;
  }
  const pct = v * 100;
  return (
    <Box
      component="span"
      sx={{
        color: pct > 0 ? UP_COLOR : pct < 0 ? DOWN_COLOR : "text.primary",
        fontWeight: 600,
      }}
    >
      {pct > 0 ? "+" : ""}
      {pct.toFixed(2)}%
    </Box>
  );
}

/** P(>1% reversal) ∈ [0,1] → plain % string. */
function ProbCell({ v }: { v: number | null }) {
  if (v == null || !Number.isFinite(v)) {
    return <Typography component="span" variant="inherit" color="text.disabled">—</Typography>;
  }
  return <>{(v * 100).toFixed(1)}%</>;
}

/** Within-window close swing amplitude (1 + max change) / (1 + min change)
 *  = max(close)/min(close) over the forward window. Always ≥ 1. */
function RatioCell({ v }: { v: number | null }) {
  if (v == null || !Number.isFinite(v)) {
    return <Typography component="span" variant="inherit" color="text.disabled">—</Typography>;
  }
  return <>{v.toFixed(3)}</>;
}

/** The 4 forward horizons — toggle options + their result column names.
 *  Layout differs: the next-day horizon has only mean + P>1% + days, the
 *  5d/20d/60d horizons add close-based max/min changes and the mean
 *  within-window close swing amplitude (max_low_change_ratio, max/low). */
interface HorizonCols {
  label: string;
  /** Forward-change columns rendered as signed % cells — mean first,
   *  then max / min (only the 5d/20d/60d horizons have those). */
  changeCols: string[];
  /** Within-window close swing column; null at the next horizon. */
  mlrCol: string | null;
  probCol: string;
  occCol: string;
}

const HORIZONS: Record<HorizonKey, HorizonCols> = {
  next: {
    label: "Next",
    changeCols: ["ave_next_change"],
    mlrCol: null,
    probCol: "reverse_prob",
    occCol: "occurrence_count_next",
  },
  "5d": {
    label: "5d",
    changeCols: ["ave_next_5d_change", "max_5d_change", "min_5d_change"],
    mlrCol: "max_low_change_ratio_5d",
    probCol: "reverse_prob_5d",
    occCol: "occurrence_count_5d",
  },
  "20d": {
    label: "20d",
    changeCols: ["ave_next_20d_change", "max_20d_change", "min_20d_change"],
    mlrCol: "max_low_change_ratio_20d",
    probCol: "reverse_prob_20d",
    occCol: "occurrence_count_20d",
  },
  "60d": {
    label: "60d",
    changeCols: ["ave_next_60d_change", "max_60d_change", "min_60d_change"],
    mlrCol: "max_low_change_ratio_60d",
    probCol: "reverse_prob_60d",
    occCol: "occurrence_count_60d",
  },
};

type HorizonKey = "next" | "5d" | "20d" | "60d";

/** Sub-column headers matching a horizon's changeCols (+ max/low). */
const CHANGE_HEADS = ["mean", "max", "min"];

/** stat_month (YYYY-MM-DD) → trailing 5-year window label "YYYY-MM → YYYY-MM". */
function periodLabel(statMonth: string): string {
  const startYear = Number(statMonth.slice(0, 4)) - 5;
  return `${startYear}${statMonth.slice(4, 7)} → ${statMonth.slice(0, 7)}`;
}

const HEAD_CELL_SX = {
  fontSize: "0.62rem",
  px: 0.75,
  py: 0.4,
  whiteSpace: "nowrap" as const,
  lineHeight: 1.2,
};
const BODY_CELL_SX = {
  fontSize: "0.66rem",
  px: 0.75,
  py: 0.3,
  whiteSpace: "nowrap" as const,
  textAlign: "right" as const,
};
const GROUP_CELL_SX = {
  ...HEAD_CELL_SX,
  textAlign: "center" as const,
  borderBottom: "none",
  pb: 0,
};
const SUB_CELL_SX = {
  ...HEAD_CELL_SX,
  textAlign: "right" as const,
  pt: 0,
};

interface Props {
  code: string;
  secType: MaSpreadSecType;
  kind: ForecastKind;
}

export function ForecastTable({ code, secType, kind }: Props) {
  const [data, setData] = useState<ForecastResponse | null>(null);
  // Starts TRUE so the spinner shows on first mount (before the first
  // effect tick) instead of flashing the empty-state message.
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Which horizon's result columns the table shows.
  const [horizon, setHorizon] = useState<HorizonKey>("next");
  // Selected stat_month — null = latest (server default). Keyed by the
  // (secType, code, kind) scope so switching any of them resets the
  // selection back to the latest month for the new code.
  const scopeKey = `${secType}|${code}|${kind}`;
  const [sel, setSel] = useState<{ key: string; month: string | null }>({
    key: scopeKey,
    month: null,
  });
  const month = sel.key === scopeKey ? sel.month : null;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchMovAveSpreadForecast(code, secType, kind, month)
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [code, secType, kind, month]);

  if (loading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
        <CircularProgress size={20} />
      </Box>
    );
  }
  if (error) {
    return <Alert severity="error" variant="outlined">{error}</Alert>;
  }
  const months = data?.months ?? [];
  const rows = data?.rows ?? [];
  if (rows.length === 0) {
    return (
      <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.68rem" }}>
        {months.length === 0 ? (
          <>
            no analysis_forecasts rows for {code} — run{" "}
            <code>python -m analyze.analysis_forecasts</code> (index data is
            backfilled; etf/stock pending)
          </>
        ) : (
          <>no rows for {month ?? months[0]} — pick another month</>
        )}
      </Typography>
    );
  }

  const isRsi = kind === "mov_rsi";
  const rsiRows = rows as MovRsiForecastRow[];
  const stdRows = rows as MovStdForecastRow[];
  const h = HORIZONS[horizon];
  const hasMM = h.mlrCol != null;

  return (
    <Box>
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          mb: 0.5,
        }}
      >
        {/* stat_month selector — defaults to the latest available month */}
        <FormControl size="small" sx={{ minWidth: 96 }}>
          <Select
            value={month ?? months[0] ?? ""}
            onChange={(e) => setSel({ key: scopeKey, month: e.target.value })}
            sx={{ fontSize: "0.7rem", "& .MuiSelect-select": { py: 0.25 } }}
          >
            {months.map((m) => (
              <MenuItem key={m} value={m} sx={{ fontSize: "0.7rem" }}>
                {m.slice(0, 7)}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        <ToggleButtonGroup
          size="small"
          exclusive
          value={horizon}
          onChange={(_, v) => {
            if (v) setHorizon(v as HorizonKey);
          }}
        >
          {(Object.keys(HORIZONS) as HorizonKey[]).map((h) => (
            <ToggleButton key={h} value={h} sx={{ px: 1, py: 0.15, fontSize: "0.65rem" }}>
              {HORIZONS[h].label}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
      </Box>
      <TableContainer sx={{ ...expandedTableContainerSx, maxHeight: 320 }}>
        <Table size="small" stickyHeader>
          <TableHead>
            <TableRow>
              <TableCell sx={{ ...GROUP_CELL_SX, textAlign: "left" }} rowSpan={2}>
                period (5y)
              </TableCell>
              {isRsi ? (
                <>
                  <TableCell sx={GROUP_CELL_SX} rowSpan={2}>RSI win</TableCell>
                  <TableCell sx={GROUP_CELL_SX} rowSpan={2}>pct</TableCell>
                </>
              ) : (
                <>
                  <TableCell sx={GROUP_CELL_SX} rowSpan={2}>MA win</TableCell>
                  <TableCell sx={GROUP_CELL_SX} rowSpan={2}>k</TableCell>
                </>
              )}
              <TableCell sx={GROUP_CELL_SX} rowSpan={2}>side</TableCell>
              <TableCell sx={GROUP_CELL_SX} rowSpan={2}>hyped</TableCell>
              {!isRsi && (
                <>
                  <TableCell sx={GROUP_CELL_SX} rowSpan={2}>exc close</TableCell>
                  <TableCell sx={GROUP_CELL_SX} rowSpan={2}>exc max avg</TableCell>
                  <TableCell sx={GROUP_CELL_SX} rowSpan={2}>exc max peak</TableCell>
                </>
              )}
              <TableCell sx={GROUP_CELL_SX} colSpan={h.changeCols.length + (hasMM ? 1 : 0) + 2}>
                {h.label}
              </TableCell>
            </TableRow>
            <TableRow>
              {h.changeCols.map((_, i) => (
                <TableCell key={CHANGE_HEADS[i]} sx={SUB_CELL_SX}>
                  {CHANGE_HEADS[i]}
                </TableCell>
              ))}
              {hasMM && <TableCell sx={SUB_CELL_SX}>max/low</TableCell>}
              <TableCell sx={SUB_CELL_SX}>P&gt;1%</TableCell>
              <TableCell sx={SUB_CELL_SX}>days</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((r, i) => {
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              const row = r as any;
              const configKey = isRsi
                ? `${rsiRows[i].rsi_window}-${rsiRows[i].side}-${rsiRows[i].pct}`
                : `${stdRows[i].ma_window}-${stdRows[i].k}-${stdRows[i].side}`;
              return (
                <TableRow key={`${row.stat_month}-${configKey}-${row.is_market_hyped}`} hover>
                  <TableCell sx={{ ...BODY_CELL_SX, textAlign: "left" }}>
                    {periodLabel(row.stat_month)}
                  </TableCell>
                  {isRsi ? (
                    <>
                      <TableCell sx={BODY_CELL_SX}>{rsiRows[i].rsi_window}</TableCell>
                      <TableCell sx={BODY_CELL_SX}>{rsiRows[i].pct}%</TableCell>
                    </>
                  ) : (
                    <>
                      <TableCell sx={BODY_CELL_SX}>{stdRows[i].ma_window}</TableCell>
                      <TableCell sx={BODY_CELL_SX}>{stdRows[i].k}</TableCell>
                    </>
                  )}
                  <TableCell
                    sx={{
                      ...BODY_CELL_SX,
                      color:
                        rsiRows[i]?.side === "top" || stdRows[i]?.side === "upper"
                          ? UP_COLOR
                          : DOWN_COLOR,
                      textAlign: "center",
                    }}
                  >
                    {row.side}
                  </TableCell>
                  <TableCell sx={{ ...BODY_CELL_SX, textAlign: "center" }}>
                    {row.is_market_hyped ? "●" : "·"}
                  </TableCell>
                  {!isRsi && (
                    <>
                      <TableCell sx={BODY_CELL_SX}>
                        {stdRows[i].mean_excess_close != null
                          ? `${(stdRows[i].mean_excess_close * 100).toFixed(2)}%`
                          : "—"}
                      </TableCell>
                      <TableCell sx={BODY_CELL_SX}>
                        {stdRows[i].mean_excess_max != null
                          ? `${(stdRows[i].mean_excess_max * 100).toFixed(2)}%`
                          : "—"}
                      </TableCell>
                      <TableCell sx={BODY_CELL_SX}>
                        {stdRows[i].max_excess_max != null
                          ? `${(stdRows[i].max_excess_max * 100).toFixed(2)}%`
                          : "—"}
                      </TableCell>
                    </>
                  )}
                  {h.changeCols.map((c) => (
                    <TableCell key={`${c}-c`} sx={BODY_CELL_SX}>
                      <ChangeCell v={row[c] as number | null} />
                    </TableCell>
                  ))}
                  {h.mlrCol != null && (
                    <TableCell sx={BODY_CELL_SX}>
                      <RatioCell v={row[h.mlrCol] as number | null} />
                    </TableCell>
                  )}
                  <TableCell sx={BODY_CELL_SX}>
                    <ProbCell v={row[h.probCol] as number | null} />
                  </TableCell>
                  <TableCell sx={BODY_CELL_SX}>
                    <Typography
                      component="span"
                      variant="inherit"
                      color={row[h.occCol] == null ? "text.disabled" : "text.primary"}
                    >
                      {row[h.occCol] == null ? "—" : row[h.occCol]}
                    </Typography>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </TableContainer>
    </Box>
  );
}

export default ForecastTable;
