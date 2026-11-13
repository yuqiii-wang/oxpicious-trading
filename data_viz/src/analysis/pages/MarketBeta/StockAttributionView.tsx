/**
 * StockAttributionView — the "Stock Attribution" mode on the Market Beta
 * page: the rise/drop audit per selected industry. For the as-of date it
 * renders ONE histogram per selected industry (each bar = one of the 12
 * 'stock_change_bucket' buckets — "26 stocks in this industry rose above
 * 7.5%"), plus a shared date dropdown fed by the API's available_dates.
 *
 * The container fetches ALL selected industries in one parallel effect
 * (Promise.allSettled — a single load state, partial failures degrade to
 * per-chart error cards) rather than per-chart useChartData, so the date
 * dropdown's options come from the same responses. Bars are colored by
 * side (UP_COLOR green = rise buckets 6-11, DOWN_COLOR red = drop
 * buckets 0-5, the repo chart convention) and the subtitle carries the
 * audit headline numbers: up(≥0) / down(<0) split and the >=7.5% /
 * <=-7.5% tails.
 *
 * Source: analysis.industry_stock_change_dist (built by
 * analysis.market_beta.stock_attribution) via
 * GET /api/analysis/industry-stock-attribution.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Autocomplete,
  Box,
  Stack,
  TextField,
} from "@mui/material";
import {
  BaseChart,
  useChartThemeMode,
} from "@/shared/charts/base-chart";
import { INSIGHT_PAGES } from "@/shared/ai-ask";
import type { AiAskSpec } from "@/shared/ai-ask";
import { useDocs } from "@/shared/docs";
import { fetchIndustryStockAttribution } from "@/lib/api-client";
import type { IndustryStockAttributionResponse } from "@shared/types";
import type { StockAttributionViewProps } from "./types";
import { buildStockAttributionOption } from "./stockAttributionOption";

/** Rise-side tail buckets (>= 7.5%) and drop-side tail buckets
 *  (<= -7.5%) for the subtitle's audit headline numbers. */
const FIRST_RISE_BUCKET = 6;
const FIRST_BIG_RISE_BUCKET = 9; // '7.5~12.5%'
const LAST_BIG_DROP_BUCKET = 2;  // '-12.5~-7.5%'

function sumBuckets(
  resp: IndustryStockAttributionResponse,
  pred: (bucket: number) => boolean,
): number {
  return resp.buckets
    .filter((b) => pred(b.bucket))
    .reduce((s, b) => s + b.stock_count, 0);
}

export function StockAttributionView({
  industries,
  refreshKey,
}: StockAttributionViewProps) {
  const themeMode = useChartThemeMode();
  const docs = useDocs();

  // As-of date shared by every per-industry histogram. null → latest
  // available. Set from the dropdown (options = available_dates from the
  // loaded responses); clearing it snaps back to latest.
  const [date, setDate] = useState<string | null>(null);
  const [responses, setResponses] = useState<
    Array<IndustryStockAttributionResponse | null>
  >([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const idsKey = industries.map((i) => i.id).join(",");

  useEffect(() => {
    if (industries.length === 0) {
      setResponses([]);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.allSettled(
      industries.map((ind) => fetchIndustryStockAttribution(ind.id, date)),
    ).then((results) => {
      if (cancelled) return;
      setResponses(
        results.map((r) =>
          r.status === "fulfilled" ? r.value : null,
        ),
      );
      const firstError = results.find((r) => r.status === "rejected");
      setError(
        firstError && firstError.status === "rejected"
          ? String(firstError.reason)
          : null,
      );
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idsKey, date, refreshKey]);

  // The dropdown's date options — every industry shares the stock daily
  // calendar, so the first loaded response's available_dates covers all.
  const availableDates = useMemo(() => {
    const first = responses.find((r) => r != null && r.available_dates.length > 0);
    return first ? first.available_dates : [];
  }, [responses]);

  // The user may have picked a date that is no longer in the options
  // (industry set changed); the Autocomplete simply shows the raw value
  // then — no forced reset, the next explicit pick or clear fixes it.
  return (
    <Stack spacing={1.5}>
      {/* Shared as-of date selector — one date for every industry's
          histogram (audit comparison across industries needs a common
          date). Clearing the selection snaps back to the latest date. */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "flex-end",
          alignItems: "center",
          gap: 2,
          flexWrap: "wrap",
        }}
      >
        <Autocomplete
          size="small"
          options={availableDates}
          value={date}
          onChange={(_, newValue) => setDate(newValue)}
          renderInput={(params) => (
            <TextField
              {...params}
              size="small"
              label="As-of date (clear = latest)"
              sx={{ minWidth: 200, "& .MuiOutlinedInput-root": { py: 0.25 } }}
            />
          )}
          sx={{ minWidth: 200, maxWidth: 260 }}
        />
      </Box>

      {error && industries.length > 0 && responses.every((r) => r == null) && (
        <Alert severity="error" sx={{ py: 0.5 }}>
          Failed to load stock attribution: {error}
        </Alert>
      )}

      {industries.map((ind, i) => {
        const resp = responses[i] ?? null;
        const option =
          resp && resp.buckets.length > 0
            ? buildStockAttributionOption(resp, themeMode)
            : null;

        // Audit headline numbers for the subtitle: the up(>=0)/down(<0)
        // split plus the >=7.5% / <=-7.5% tails (e.g. "26 stocks rose
        // above 7.5%" reads straight off the rise tail).
        let subtitle = ind.label || ind.id;
        if (resp && resp.buckets.length > 0) {
          const up = sumBuckets(resp, (b) => b >= FIRST_RISE_BUCKET);
          const down = resp.total_stocks - up;
          const riseTail = sumBuckets(resp, (b) => b >= FIRST_BIG_RISE_BUCKET);
          const dropTail = sumBuckets(resp, (b) => b <= LAST_BIG_DROP_BUCKET);
          subtitle =
            `as-of ${resp.date} · ${resp.total_stocks} stocks · ` +
            `up(≥0) ${up} / down(<0) ${down} · ` +
            `≥7.5%: ${riseTail} · ≤-7.5%: ${dropTail}`;
        }

        const aiAsk: AiAskSpec = {
          insight: INSIGHT_PAGES.marketBeta,
          intro: docs.text("ui.market-beta.stock-attribution", {
            industry: ind.label || ind.id,
            date: resp?.date || "the latest date",
          }),
          series: docs.series("ui.market-beta.stock-attribution"),
        };

        return (
          <BaseChart
            key={ind.id}
            title={`${ind.label || ind.id} — Stock Attribution`}
            subtitle={subtitle}
            aiAsk={aiAsk}
            option={option}
            loading={loading && !resp}
            error={
              !loading && resp == null && error
                ? `Failed to load stock attribution: ${error}`
                : null
            }
            emptyText={
              resp && resp.buckets.length === 0
                ? `No stock change distribution for ${ind.id}. Run python -m analysis.market_beta to populate.`
                : "No data"
            }
            height={340}
          />
        );
      })}
    </Stack>
  );
}
