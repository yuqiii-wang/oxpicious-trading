/**
 * MaSpreadPanel — one card per code: pair chips + two-curve chart with
 * green/red fill between them + date-range slider + Bollinger envelope.
 *
 * Each panel renders:
 *   1. 9 pair chips (Price/MA5 … MA5/MA255); clicking one selects the pair
 *      shown in the chart below.
 *   2. Date-range slider above the chart.
 *   3. Two-curve chart (short + long MA) with green fill when short > long
 *      (growth) and red fill when short < long (decline). The tooltip shows
 *      each series' slope (1st derivative) and curvature (2nd derivative)
 *      — including price's own slope/curvature for Price/MA pairs.
 *   4. Bollinger envelope (Price/MA pairs only): ±k×σ dashed lines around
 *      the long MA, with a faint fill between them. k is selected from a
 *      dropdown in the card's top-right corner (0 = hidden, 2 = standard
 *      Bollinger, max 3, step 0.5). MA5/MA pairs do not show the envelope
 *      (σ is of price, not of an MA-of-MA) and the dropdown is hidden.
 *
 * Fetches its own chart data on mount via fetchMovAveSpreadChart(code, secType).
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  MenuItem,
  Select,
  Slider,
  Stack,
  Typography,
} from "@mui/material";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import { UP_COLOR } from "@/theme/chart-palette";
import { fmtNum, fmtPct } from "@/lib/series";
import { fetchMovAveSpreadChart } from "@/lib/api-client";
import type { MovAveSpreadChartResponse } from "../../../../shared/types";
import type { PanelProps } from "./types";
import { buildPairOption } from "./chartOption";

/** Bollinger multiplier options for the top-right dropdown (0.0 … 3.0, step 0.5).
 *  0.0 = band hidden; 2.0 = standard Bollinger. */
const BOLL_K_OPTIONS = [0, 0.5, 1, 1.5, 2, 2.5, 3];

export function MaSpreadPanel({ code, name, secType, themeMode }: PanelProps) {
  // ---- Chart data ---------------------------------------------------------
  const [chartData, setChartData] = useState<MovAveSpreadChartResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Which of the 9 pairs is shown in the single plot (default 0 = Price/MA5).
  const [selectedPairIdx, setSelectedPairIdx] = useState(0);

  // Date-range slider state — two indices into the first pair's rows array.
  // The slider drives ALL pairs (they share the same date axis).
  const firstPairRows = chartData?.pairs[0]?.rows ?? [];
  const maxIdx = firstPairRows.length - 1;
  const [range, setRange] = useState<[number, number]>([0, maxIdx]);

  // Bollinger multiplier k in MA ± k×σ. Default 2 (standard Bollinger).
  // 0 hides the envelope. Only affects Price/MA pairs (ma_short === 0);
  // MA5/MA pairs don't get the envelope and the dropdown is hidden.
  // Options: 0, 0.5, 1, 1.5, 2, 2.5, 3 (step 0.5).
  const [bollingerK, setBollingerK] = useState(2);

  // Fetch chart data on mount and whenever the code/sec_type changes.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchMovAveSpreadChart(code, secType)
      .then((d) => {
        if (cancelled) return;
        setChartData(d);
        const m = Math.max(0, (d.pairs[0]?.rows.length ?? 1) - 1);
        setRange([0, m]);
        setSelectedPairIdx(0);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setChartData(null);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [code, secType]);

  // Slice each pair's rows to the selected date window.
  const filteredPairs = useMemo(() => {
    if (!chartData) return [];
    return chartData.pairs.map((p) => ({
      ...p,
      rows: p.rows.slice(range[0], range[1] + 1),
    }));
  }, [chartData, range]);

  // Clamp selectedPairIdx to valid range.
  const safePairIdx = Math.min(selectedPairIdx, Math.max(0, filteredPairs.length - 1));
  const selectedPair = filteredPairs[safePairIdx];

  // Optional secondary stat row from the latest snapshot of all 9 pairs —
  // surfaced as a small caption so the user can scan the page quickly.
  const latestSummary = chartData?.pairs[safePairIdx]?.rows.slice(-1)[0] ?? null;

  const subtitle = chartData
    ? `${chartData.code} · ${chartData.name || name || "—"} · ${firstPairRows.length} bars` +
      (firstPairRows.length > 0
        ? ` · ${firstPairRows[0].date} → ${firstPairRows[firstPairRows.length - 1].date}`
        : "")
    : `${code} · ${name || "—"}`;

  // Bollinger dropdown shown in the card header's top-right corner. Only
  // rendered for Price/MA pairs (ma_short === 0). For MA5/MA pairs the
  // envelope doesn't apply (σ is of price, not of an MA-of-MA), so the
  // dropdown is hidden entirely.
  const bollAction = !loading && !error && selectedPair && selectedPair.ma_short === 0 ? (
    <Stack direction="row" alignItems="center" spacing={0.5} sx={{ mr: 0.5 }}>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ fontSize: "0.7rem", whiteSpace: "nowrap" }}
      >
        Bollinger
      </Typography>
      <Select
        size="small"
        value={bollingerK}
        onChange={(e) => setBollingerK(e.target.value as number)}
        sx={{
          height: 26,
          fontSize: "0.75rem",
          "& .MuiSelect-select": { py: 0.25, px: 1, fontSize: "0.75rem" },
        }}
        renderValue={(v) =>
          v === 0 ? "Off" : `${Number(v).toFixed(1)}σ`
        }
      >
        {BOLL_K_OPTIONS.map((k) => (
          <MenuItem key={k} value={k} sx={{ fontSize: "0.75rem", py: 0.25 }}>
            {k === 0 ? "Off (0.0)" : `${k.toFixed(1)}σ`}
          </MenuItem>
        ))}
      </Select>
    </Stack>
  ) : undefined;

  return (
    <ChartCard
      title={selectedPair ? selectedPair.pair_label : "MA-Spread"}
      subtitle={subtitle}
      action={bollAction}
    >
      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress size={24} />
        </Box>
      )}
      {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}

      {!loading && !error && firstPairRows.length > 0 && maxIdx > 0 && (
        <Box sx={{ px: 1, py: 0.5 }}>
          <Slider
            value={range}
            onChange={(_, v) => setRange(v as [number, number])}
            min={0}
            max={maxIdx}
            size="small"
            valueLabelDisplay="auto"
            valueLabelFormat={(idx) => firstPairRows[idx]?.date ?? ""}
            sx={{ mt: 0.5, "& .MuiSlider-valueLabel": { fontSize: "0.7rem" } }}
          />
          <Stack direction="row" justifyContent="space-between" sx={{ mt: -0.5 }}>
            <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.65rem" }}>
              {firstPairRows[range[0]]?.date ?? "—"}
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.65rem" }}>
              {firstPairRows[range[1]]?.date ?? "—"}
            </Typography>
          </Stack>
        </Box>
      )}

      {!loading && !error && selectedPair && selectedPair.rows.length > 0 && (
        <EChart
          option={buildPairOption({ pair: selectedPair, themeMode, bollingerK })}
          height={420}
        />
      )}

      {!loading && !error && selectedPair && selectedPair.rows.length === 0 && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
          <Typography variant="caption" color="text.secondary">
            No data for {selectedPair.pair_label} in this date range.
          </Typography>
        </Box>
      )}

      {/* Latest-snapshot summary line for the selected pair. */}
      {!loading && !error && latestSummary && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 0.5, fontSize: "0.7rem" }}
        >
          {selectedPair?.pair_label} @ {latestSummary.date} · short {fmtNum(latestSummary.short_value)} ·
          long {fmtNum(latestSummary.long_value)} · gap{" "}
          <Box
            component="span"
            sx={{
              color: latestSummary.gap_value == null ? "text.disabled" : UP_COLOR,
              fontWeight: 600,
            }}
          >
            {latestSummary.gap_value == null
              ? "—"
              : fmtPct(latestSummary.gap_value * 100, 2)}
          </Box>
        </Typography>
      )}

      {/* Pair chips row — click to switch the pair shown above. */}
      {!loading && !error && filteredPairs.length > 0 && (
        <Box sx={{ mt: 1 }}>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ mb: 0.5, display: "block", fontSize: "0.7rem" }}
          >
            Pairs — click to switch
          </Typography>
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75 }}>
            {filteredPairs.map((pair, idx) => {
              const active = idx === safePairIdx;
              return (
                <Chip
                  key={pair.pair_label}
                  label={pair.pair_label}
                  clickable
                  size="small"
                  color={active ? "primary" : "default"}
                  variant={active ? "filled" : "outlined"}
                  onClick={() => setSelectedPairIdx(idx)}
                  sx={{ fontSize: "0.75rem" }}
                />
              );
            })}
          </Box>
        </Box>
      )}
    </ChartCard>
  );
}
