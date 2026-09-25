/**
 * Live Data — Options page.
 *
 * Intraday OI-weighted moneyness skewness for option underlyings: the
 * dataviz Open Interests tab's "Underlying Price & OI-wtd Moneyness Skew
 * Over Time" chart with the x-axis switched from daily dates to 5-min
 * bars. Venue toggle (SZSE/SSE/CFFEX) + underlying picker + date selector
 * + refresh, with a 5-min auto-refresh during trading hours while the
 * latest date is on screen (Market Movements pattern). The series itself
 * is computed server-side — /api/live-options spawns
 * python -m live.options_intraday_skewness on demand when missing/stale;
 * the page polls the run status so the indicator survives page refreshes.
 *
 * "Build Yday Ref" button (Market Movements pattern): when the estimator's
 * prev-trading-day OI snapshot is missing/stale (nightly build failed, or
 * the SSE streamer died mid-session so the day has no final EOD), the
 * on-demand compute has no universe to work with and the chart stays empty.
 * The button runs the ref chain — refresh contract listing → builds.options
 * --date <snapshot_date> (non-final quote files accepted for the forced
 * date) → recompute the series — deduped by process-id-tag, with the
 * spinner state restored after a page refresh via the chain-tag status
 * poll (POST /api/live-options/run + GET /api/live-options/run/status).
 */
import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  CircularProgress,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import RefreshButton from "@/components/RefreshButton";
import { DateSelector } from "@/shared/components/date-selector";
import OiSkewIntradayPanel from "@/live/features/options/OiSkewIntradayPanel";
import { isWithinTradingHours } from "@/live/hooks/useSecAllocLivePipeline";
import {
  fetchLiveOptionsDates,
  fetchLiveOiSkewIntraday,
  fetchLiveOptionsRunStatus,
  fetchUnderlyings,
  invalidateCacheForPrefix,
  liveOptionsComputeTag,
  liveOptionsRefChainTags,
  runLiveOptionsRefChain,
  venueToTargetType,
  type OptionsVenue,
} from "@/lib/api-client";
import type {
  LiveOptionsOiSkewResponse,
  OptionsUnderlying,
} from "@shared/types";

const VENUES: readonly OptionsVenue[] = ["SZSE", "SSE", "CFFEX"];
// Default SSE: the only venue with a live options streamer today, so the
// landing view has 5-min spot + volume-augmented OI out of the box.
const DEFAULT_VENUE: OptionsVenue = "SSE";
/** Auto-refresh cadence while the latest date is on screen (trading hours). */
const AUTO_REFRESH_MS = 5 * 60_000;
/** Poll cadence of the on-demand compute run status. */
const COMPUTE_POLL_MS = 5_000;

export default function OptionsPage() {
  const [venue, setVenue] = useState<OptionsVenue>(DEFAULT_VENUE);
  const targetType = venueToTargetType(venue);
  const [underlyings, setUnderlyings] = useState<OptionsUnderlying[]>([]);
  const [underlying, setUnderlying] = useState<OptionsUnderlying | null>(null);
  const [dates, setDates] = useState<string[]>([]);
  const [selectedDate, setSelectedDate] = useState<string>("");
  const [resp, setResp] = useState<LiveOptionsOiSkewResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [computing, setComputing] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  // Underlyings for the venue (venue switch resets the selection).
  useEffect(() => {
    let cancelled = false;
    setUnderlyings([]);
    setUnderlying(null);
    setDates([]);
    setSelectedDate("");
    setResp(null);
    setError(null);
    fetchUnderlyings(targetType, venue)
      .then((list) => {
        if (cancelled) return;
        setUnderlyings(list);
        const first = list.length > 0 ? list[0] : null;
        if (first) setUnderlying(first);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(`Failed to load option underlyings: ${e.message}`);
      });
    return () => {
      cancelled = true;
    };
  }, [venue, targetType]);

  // Available dates for the selected underlying (refreshKey re-checks so a
  // freshly streamed day appears; the current pick is kept when still valid).
  useEffect(() => {
    if (!underlying) return;
    let cancelled = false;
    fetchLiveOptionsDates(underlying.code, targetType)
      .then((r) => {
        if (cancelled) return;
        setDates(r.dates);
        if (r.dates.length > 0 && !r.dates.includes(selectedDate)) {
          setSelectedDate(r.dates[0]);
        }
      })
      .catch(() => {
        if (!cancelled) setDates([]);
      });
    return () => {
      cancelled = true;
    };
  }, [underlying, targetType, selectedDate, refreshKey]);

  // The chart data itself.
  useEffect(() => {
    if (!underlying) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchLiveOiSkewIntraday(underlying.code, selectedDate || null, targetType)
      .then((r) => {
        if (cancelled) return;
        setResp(r);
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
  }, [underlying, selectedDate, targetType, refreshKey]);

  const handleRefresh = useCallback(() => {
    invalidateCacheForPrefix("/api/live-options/");
    setRefreshKey((k) => k + 1);
  }, []);

  // Auto-refresh: every 5 min during trading hours, latest view only.
  const isLatestView = dates.length > 0 && selectedDate === dates[0];
  useEffect(() => {
    const timer = setInterval(() => {
      if (isWithinTradingHours() && isLatestView) handleRefresh();
    }, AUTO_REFRESH_MS);
    return () => clearInterval(timer);
  }, [isLatestView, handleRefresh]);

  // On-demand compute status: poll the tag while the response is fresh
  // state; on running → indicator on, running → done → refetch (the
  // REF_CHAIN latch pattern from the Market Movements page).
  const computeTag =
    resp && resp.date && underlying
      ? liveOptionsComputeTag(underlying.code, resp.date)
      : "";
  useEffect(() => {
    if (!computeTag) return;
    let wasRunning = false;
    const poll = async () => {
      try {
        const status = await fetchLiveOptionsRunStatus([computeTag]);
        const running = status.status[computeTag] === true;
        if (running) {
          wasRunning = true;
          setComputing(true);
        } else {
          setComputing(false);
          if (wasRunning) handleRefresh();
        }
      } catch {
        // status polling is best-effort
      }
    };
    void poll();
    const timer = setInterval(poll, COMPUTE_POLL_MS);
    return () => clearInterval(timer);
  }, [computeTag, handleRefresh]);

  // ---- Yday Ref (prev-trading-day OI snapshot) manual build ---------------
  // The skewness estimator bases OI on the prev-trading-day options snapshot
  // (stats.v_options_quote at `snapshot_date`); when that day's snapshot is
  // missing or terms-stale (e.g. the nightly options build failed, or the
  // SSE streamer died mid-session so the day never reached a "final" EOD),
  // the on-demand compute finds no universe and the chart stays empty. The
  // button runs the whole chain server-side (deduped by process-id-tag — a
  // second click / page refresh / second tab while ANY phase runs resolves
  // immediately with already_running):
  //    1. downloads.options.sse.price — refresh the contract-listing CSVs.
  //    2. builds.options --date <snapshot_date> — force single-date rebuild
  //       of the yday snapshot (non-final quote files accepted for the
  //       forced date).
  //    3. live.options_intraday_skewness --date <selected> --underlying U —
  //       recompute the on-screen series.
  // Spinner state restores after a page refresh via the chain-tag status
  // poll below (same latch pattern as Market Movements' Build Yday Ref).
  const [refRunning, setRefRunning] = useState(false);
  const [refMessage, setRefMessage] = useState<string | null>(null);
  const handleBuildRef = useCallback(async () => {
    if (refRunning || !resp?.snapshot_date) return;
    setRefRunning(true);
    setRefMessage("Building yday ref (prev-day OI snapshot)…");
    try {
      const result = await runLiveOptionsRefChain({
        snapshotDate: resp.snapshot_date,
        date: resp.date || selectedDate || undefined,
        underlying: underlying?.code,
      });
      if (result.already_running) {
        setRefMessage("Yday ref process already running — waiting for it to finish…");
      } else if (result.success) {
        setRefMessage("Yday ref built.");
      } else {
        setRefMessage(`Yday ref failed: ${result.stderr_tail ?? "unknown error"}`);
      }
    } finally {
      setRefRunning(false);
      invalidateCacheForPrefix("/api/live-options");
      setRefreshKey((k) => k + 1);
    }
  }, [refRunning, resp, selectedDate, underlying]);

  // Remote-ref spinner recovery: poll ALL chain tags (download, build,
  // compute) on mount and every 5s while any is running, so a page refresh
  // during ANY phase puts the button straight back into spinning + notified
  // state, and refreshes the chart when the whole chain finishes.
  useEffect(() => {
    let cancelled = false;
    let wasRunning = false;
    const poll = async () => {
      try {
        const status = await fetchLiveOptionsRunStatus(liveOptionsRefChainTags);
        if (cancelled) return;
        const running = liveOptionsRefChainTags.some(
          (t) => status.status[t] === true,
        );
        if (running) {
          wasRunning = true;
          setRefRunning(true);
          setRefMessage("Yday ref chain already running — waiting for it to finish…");
        } else if (wasRunning) {
          wasRunning = false;
          setRefRunning(false);
          setRefMessage("Yday ref finished — refreshed.");
          invalidateCacheForPrefix("/api/live-options");
          setRefreshKey((k) => k + 1);
        }
      } catch {
        /* status poll is best-effort */
      }
    };
    void poll();
    const timer = setInterval(poll, COMPUTE_POLL_MS);
    return () => { cancelled = true; clearInterval(timer); };
  }, []);

  // Freeze the plot behind the full-card spinner while what's on screen
  // would otherwise mislead: the loaded response belongs to a DIFFERENT
  // underlying/date than the selection (switching can block up to 90s while
  // the route's on-demand compute runs, and the stale curves would keep
  // rendering the previous selection behind a corner spinner), or a compute
  // / yday-ref process is rebuilding the series. A same-selection refresh
  // (the 5-min auto-refresh) does NOT freeze — the chart stays mounted with
  // just the corner spinner (Market Movements' silent-refresh rule).
  const respMatchesSelection =
    !!resp &&
    !!underlying &&
    resp.underlying_code === underlying.code &&
    (selectedDate === "" || resp.date === selectedDate);
  const freezePlot =
    refRunning ||
    computing ||
    (loading && !respMatchesSelection);
  const freezeSubtitle = `Loading ${underlying ? `${underlying.code} ${underlying.name}` : ""} · ${
    selectedDate || "latest"
  }…`;

  return (
    <Box>
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          gap: 2,
          flexWrap: "wrap",
        }}
      >
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>
            Live Data · Options
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Intraday OI-weighted moneyness skewness vs the underlying&apos;s
            5-min spot · {underlying ? `${underlying.code} ${underlying.name}` : "—"} ·{" "}
            {selectedDate || "—"}
          </Typography>
        </Box>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
          <ToggleButtonGroup
            size="small"
            exclusive
            value={venue}
            onChange={(_, v: OptionsVenue | null) => {
              if (v) setVenue(v);
            }}
          >
            {VENUES.map((v) => (
              <ToggleButton key={v} value={v} sx={{ px: 1.5, py: 0.25, fontSize: "0.7rem" }}>
                {v}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
          <Autocomplete
            size="small"
            sx={{ minWidth: 260 }}
            options={underlyings}
            getOptionLabel={(o) => `${o.code} ${o.name}`}
            isOptionEqualToValue={(o, v) => o.code === v.code}
            value={underlying}
            onChange={(_, v) => setUnderlying(v)}
            renderInput={(params) => (
              <TextField {...params} placeholder="Underlying" size="small" />
            )}
          />
          <DateSelector
            dates={dates}
            value={selectedDate || null}
            sx={{ "& .MuiInputBase-input": { fontSize: "0.8rem" } }}
            onChange={(d) => setSelectedDate(d ?? "")}
          />
          {computing && (
            <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
              <CircularProgress size={14} />
              <Typography variant="caption" color="text.secondary">
                computing series…
              </Typography>
            </Box>
          )}
          <Button
            size="small"
            variant="outlined"
            disabled={refRunning || !resp?.snapshot_date}
            onClick={() => { void handleBuildRef(); }}
            startIcon={refRunning ? <CircularProgress size={12} /> : null}
            sx={{ height: 26, minWidth: 0, px: 1, fontSize: "0.7rem" }}
            title={
              resp?.snapshot_date
                ? `Runs the yday-ref chain for the OI base the skewness estimator reads (snapshot ${resp.snapshot_date}): (1) downloads.options.sse.price — refresh contract-listing CSVs; (2) builds.options --date ${resp.snapshot_date} — force single-date rebuild of the prev-trading-day snapshot (a day the streamer died mid-session is accepted despite its non-final quotes); (3) live.options_intraday_skewness — recompute the on-screen series. Deduped by process-id-tag.`
                : "Available once a series response with a snapshot date is loaded."
            }
          >
            {refRunning ? "Building Yday Ref…" : "Build Yday Ref"}
          </Button>
          {refMessage && (
            <Typography
              variant="caption"
              color={refRunning ? "text.secondary" : "text.primary"}
              sx={{ maxWidth: 360 }}
            >
              {refMessage}
            </Typography>
          )}
          <RefreshButton
            onClick={handleRefresh}
            loading={loading}
            label="Refresh"
            tooltip="Refresh intraday OI skew (bypass cache)"
          />
        </Box>
      </Box>

      {error && underlyings.length === 0 && (
        <Alert severity="error" variant="filled" sx={{ mt: 2 }}>
          {error}
        </Alert>
      )}
      {!error && underlyings.length === 0 && (
        <Alert severity="info" sx={{ mt: 2 }}>
          No option underlyings for venue {venue}.
        </Alert>
      )}

      <Box sx={{ mt: 1.5 }}>
        <OiSkewIntradayPanel
          resp={resp}
          unit={targetType === "INDEX" ? "points" : "yuan"}
          loading={loading}
          error={error}
          freeze={freezePlot}
          freezeSubtitle={freezeSubtitle}
        />
      </Box>
    </Box>
  );
}
