/**
 * IndustryEtfContributionChart — fetches the per-ETF contribution rows for
 * ONE industry from analysis.industry_etf_contribution (via the API that also
 * reads stats.etf_liquidity_margin + stats.etf_basic_stats) and renders the
 * bar chart. This is the 2nd plot (and onward — one per selected industry)
 * in "ETF Contribution" mode.
 *
 * The as-of `date` is passed in from the parent (set by clicking a date on
 * the ETF price chart — the 1st plot). Each bar = one ETF showing its
 * trading amount (capital flow, left Y-axis, colored by return direction)
 * and its % share of the industry total (right Y-axis).
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  Typography,
} from "@mui/material";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import { fetchIndustryEtfContributionBars } from "@/lib/api-client";
import type { IndustryEtfContributionBarsResponse } from "../../../../shared/types";
import type { IndustryEtfContributionChartProps } from "./types";
import { buildIndustryEtfContributionOption } from "./industryEtfContributionOption";

export function IndustryEtfContributionChart({
  industryId,
  industryLabel,
  date,
  themeMode,
}: IndustryEtfContributionChartProps) {
  const [data, setData] = useState<IndustryEtfContributionBarsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Stable key for the fetch effect — refetch when industry or date changes.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchIndustryEtfContributionBars(industryId, date || null)
      .then((resp) => {
        if (cancelled) return;
        setData(resp);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [industryId, date]);

  const option = useMemo(
    () => (data ? buildIndustryEtfContributionOption(data, themeMode) : null),
    [data, themeMode],
  );

  const etfCount = data?.etfs.filter((e) => e.trading_amount != null).length ?? 0;

  return (
    <ChartCard
      title={`${industryLabel || industryId} — ETF Contribution`}
      subtitle={
        data
          ? `${industryLabel || industryId} — ${etfCount} ETF${etfCount === 1 ? "" : "s"} · as-of ${data.date || "—"}`
          : `${industryLabel || industryId}`
      }
    >
      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
          <CircularProgress size={24} />
        </Box>
      )}
      {error && (
        <Alert severity="error" sx={{ py: 0.5 }}>Failed to load ETF contribution: {error}</Alert>
      )}
      {!loading && !error && data && etfCount > 0 && option && (
        <EChart option={option} height={360} />
      )}
      {!loading && !error && data && etfCount === 0 && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
          <Typography variant="body2" color="text.secondary">
            No ETF trading data for {industryId} on {data.date || "this date"}. Run{" "}
            <code>python -m analyze.industry_sentiments</code> to populate.
          </Typography>
        </Box>
      )}
    </ChartCard>
  );
}
