/**
 * The state derivation for the News Text States page — each chip IS a
 * bridged research finding, rendered as context (never a direction
 * call):
 *   mood / band      — CFSD level-vs-change; Garcia regime conditioning
 *                      (neutral band widens to ±0.75σ in low-vol)
 *   regime           — Garcia 2013 / 管理世界 2024: sentiment is most
 *                      informative in high-volatility regimes (RV20 vs
 *                      its trailing-250d median, derived client-side
 *                      from the loaded close series — the DDL's
 *                      read-time-join rule)
 *   contrarian       — the validated monthly tail (ema10 trailing
 *                      deciles: +0.33% vs +2.14% next month)
 *   coverage         — the abnormal-flow risk flag (±1 median/IQR,
 *                      validated negative forward IC)
 *   asymmetry        — Heston-Sinha: negative coverage drifts for a
 *                      quarter, positive dies in days — flagged when
 *                      the 10d flow_neg sum hits its own top decile
 *   dispersion       — Isakin 2023: origin disagreement = conviction
 *                      discount (top decile → halve conviction)
 *   tone             — the kw_market talk cross-check (±0.75)
 *   quality          — low_coverage (n_scored < 3)
 *   ambiguity        — lexicon-vs-LLM disagreement share vs its
 *                      trailing baseline (fetched separately)
 */
import { Box, Chip, Tooltip, Typography } from "@mui/material";
import type { NewsTextStatesChartRow, NewsTextStatesDisagreementRow } from "@shared/types";

export interface TextStates {
  moodLabel: string;
  moodSeverity: "error" | "warning" | "success" | "info" | "default";
  moodDetail: string;
  regime: null | {
    label: string;
    severity: "default" | "success";
    detail: string;
  };
  contrarian: null | {
    label: string;
    detail: string;
  };
  coverageLabel: string;
  coverageSeverity: "error" | "warning" | "success" | "info" | "default";
  coverageDetail: string;
  asymmetry: null | {
    label: string;
    detail: string;
  };
  dispersion: null | {
    label: string;
    detail: string;
  };
  toneLabel: string | null;
  toneSeverity: "error" | "warning" | "success" | "info" | "default";
  lowCoverage: boolean;
  /** Latest defined day (the chips' as-of date). */
  latest: NewsTextStatesChartRow | null;
  /** Mean sent_z over the last 5 defined days (the Heston-Sinha weekly
   *  formation reading) — for the strip line. */
  weeklyZ: number | null;
}

/** pct-rank of x within the non-null series (0..1); null under 30 obs. */
function pctRank(series: Array<number | null>, x: number): number | null {
  const vals = series.filter((v): v is number => v != null);
  if (vals.length < 30) return null;
  const below = vals.filter((v) => v <= x).length;
  return below / vals.length;
}

function median(vals: number[]): number {
  const s = [...vals].sort((a, b) => a - b);
  const mid = s.length >> 1;
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

/**
 * Garcia regime from the loaded close series (client-side, read-time —
 * the DDL stores no regime flags): rv20 = std of the last 20 close
 * returns annualized; high-vol when rv20 exceeds the median of its own
 * trailing 250 observations.
 */
function computeRegime(
  rows: NewsTextStatesChartRow[],
): { volhi: boolean; rv20: number | null } | null {
  const rets: number[] = [];
  let prev: number | null = null;
  for (const r of rows) {
    if (r.close == null) continue;
    if (prev != null && prev > 0) rets.push(r.close / prev - 1);
    prev = r.close;
  }
  if (rets.length < 270) return null; // 20 for rv + 250 for the baseline
  const rvSeries: number[] = [];
  for (let i = 19; i < rets.length; i++) {
    const win = rets.slice(i - 19, i + 1);
    const mu = win.reduce((a, b) => a + b, 0) / win.length;
    const sd = Math.sqrt(
      win.reduce((a, b) => a + (b - mu) ** 2, 0) / (win.length - 1));
    rvSeries.push(sd * Math.sqrt(252));
  }
  const rv20 = rvSeries[rvSeries.length - 1];
  const baseline = median(rvSeries.slice(-251, -1));
  if (!Number.isFinite(rv20) || !Number.isFinite(baseline)) return null;
  return { volhi: rv20 > baseline, rv20 };
}

export function deriveTextStates(
  rows: NewsTextStatesChartRow[],
): TextStates {
  const latest = rows.filter((r) => r.sent_ema10 != null).at(-1) ?? null;

  // Garcia regime — derived from the scope's own close series (market
  // scopes: the SSE Composite; industry scopes: the industry composite).
  const regime = computeRegime(rows);
  const volhi = regime?.volhi ?? null;

  // mood band on the persistent line — the neutral band WIDENS in
  // low-vol (Garcia: tone less informative when calm)
  const neutral = volhi === false ? 0.75 : 0.5;
  const stretched = volhi === false ? 1.25 : 1.0;
  const bandNote = volhi === false
    ? "Low-vol regime: the neutral band is widened to ±0.75σ (tone less informative in calm markets — Garcia 2013)."
    : volhi === true
      ? "High-vol regime: sentiment readings are at their most informative (Garcia 2013; 管理世界 2024 downturn asymmetry)."
      : "";

  let moodLabel = "Neutral mood";
  let moodSeverity: TextStates["moodSeverity"] = "default";
  let moodDetail =
    "The persistent mood line sits inside its neutral band of its trailing history.";
  const e10 = latest?.sent_ema10 ?? null;
  if (e10 != null) {
    if (e10 >= stretched) {
      moodLabel = "Stretched optimistic";
      moodSeverity = "warning";
      moodDetail = `ema10 ≥ +${stretched}σ — the mood line is stretched high; historically the monthly tail here reads contrarian (fade context), not momentum. ${bandNote}`;
    } else if (e10 >= neutral) {
      moodLabel = "Optimistic tilt";
      moodSeverity = "success";
      moodDetail = `ema10 in +${neutral}..+${stretched}σ — persistently upbeat coverage mood. ${bandNote}`;
    } else if (e10 <= -stretched) {
      moodLabel = "Washed-out mood";
      moodSeverity = "info";
      moodDetail = `ema10 ≤ −${stretched}σ — the mood line is stretched low; historically the monthly tail here reads contrarian (recovery context). ${bandNote}`;
    } else if (e10 <= -neutral) {
      moodLabel = "Cautious tilt";
      moodSeverity = "warning";
      moodDetail = `ema10 in −${stretched}..−${neutral}σ — persistently cautious coverage mood. ${bandNote}`;
    } else {
      moodDetail = `The persistent mood line sits inside ±${neutral}σ of its trailing history. ${bandNote}`;
    }
  }

  // the validated contrarian tail — ema10 in its own trailing deciles
  const e10Rank = latest?.sent_ema10 != null
    ? pctRank(rows.map((r) => r.sent_ema10), latest.sent_ema10)
    : null;
  let contrarian: TextStates["contrarian"] = null;
  if (e10Rank != null) {
    if (e10Rank >= 0.9) {
      contrarian = {
        label: "Contrarian context — mood top decile",
        detail: `ema10 sits in its trailing top decile (${(e10Rank * 100).toFixed(0)}% of loaded history). Validated: after top-20% mood months the index averaged +0.33% the next month vs +2.14% after bottom-20% months — state context, not a trigger.`,
      };
    } else if (e10Rank <= 0.1) {
      contrarian = {
        label: "Contrarian context — mood bottom decile",
        detail: `ema10 sits in its trailing bottom decile (${(e10Rank * 100).toFixed(0)}% of loaded history). Validated: after bottom-20% mood months the index averaged +2.14% the next month — the recovery tail the CFSD paper documents.`,
      };
    }
  }

  // coverage state on the abnormal-flow flag
  const af = latest?.flow_af ?? null;
  let coverageLabel = "Normal coverage";
  let coverageSeverity: TextStates["coverageSeverity"] = "default";
  let coverageDetail =
    "Daily article count inside its trailing 63-day median/IQR band (|AF| < 1).";
  if (af != null) {
    if (af >= 1.0) {
      coverageLabel = "Abnormal coverage — risk flag";
      coverageSeverity = "error";
      coverageDetail = `AF = ${af.toFixed(1)}σ-IQR above the trailing median. Validated: abnormal coverage precedes LOWER forward returns at 1-10d horizons (IC −0.03..−0.08) — treat as a risk/event flag.`;
    } else if (af <= -1.0) {
      coverageLabel = "Unusually quiet";
      coverageSeverity = "info";
      coverageDetail = `AF = ${af.toFixed(1)} — article count abnormally below its trailing median (no information event).`;
    }
  }

  // Heston-Sinha asymmetry: the 10d flow_neg sum at its own top decile
  // (negative coverage drifts ~a quarter; positive dies within days)
  let asymmetry: TextStates["asymmetry"] = null;
  if (latest?.flow_neg != null) {
    const roll10: Array<number | null> = rows.map((_, i) => {
      const win = rows.slice(Math.max(0, i - 9), i + 1);
      return win.some((r) => r.flow_neg == null)
        ? null
        : win.reduce((a, r) => a + (r.flow_neg ?? 0), 0);
    });
    const rank = pctRank(roll10, roll10[roll10.length - 1] ?? 0);
    if (rank != null && rank >= 0.9) {
      asymmetry = {
        label: "Negative coverage persisting",
        detail: `The 10-day negative-article sum sits in its trailing top decile (${(rank * 100).toFixed(0)}%). Heston-Sinha: positive news is priced within a week, but negative news drifts for roughly a quarter (short-sale constraints) — the gloom persists.`,
      };
    }
  }

  // Isakin dispersion: per-origin score std at its own top decile =
  // conviction discount, never a direction
  let dispersion: TextStates["dispersion"] = null;
  const disp = latest?.disp_origin ?? null;
  if (disp != null) {
    const rank = pctRank(rows.map((r) => r.disp_origin), disp);
    if (rank != null && rank >= 0.9) {
      dispersion = {
        label: "Origin opinions diverge — reduced conviction",
        detail: `Cross-origin sentiment dispersion is in its trailing top decile (${(rank * 100).toFixed(0)}%; ${disp.toFixed(2)} pts std). Isakin 2023: disagreement is a conviction discount and volatility warning — halve the weight you give the day's direction.`,
      };
    }
  }

  // talk tone cross-check
  const tone = latest?.lex_tone_z ?? null;
  let toneLabel: string | null = null;
  let toneSeverity: TextStates["toneSeverity"] = "default";
  if (tone != null) {
    if (tone >= 0.75) {
      toneLabel = "Bullish talk";
      toneSeverity = "success";
    } else if (tone <= -0.75) {
      toneLabel = "Bearish talk";
      toneSeverity = "error";
    } else {
      toneLabel = "Mixed talk";
    }
  }

  // Heston-Sinha weekly formation: mean sent_z over the last 5 defined
  // days (weekly aggregates persist ~13 weeks; daily dies in 2)
  const zVals = rows
    .filter((r) => r.sent_z != null)
    .map((r) => r.sent_z as number);
  const weeklyZ = zVals.length >= 5
    ? zVals.slice(-5).reduce((a, b) => a + b, 0) / 5
    : null;

  return {
    moodLabel,
    moodSeverity,
    moodDetail,
    regime: regime
      ? {
          label: regime.volhi
            ? "High-vol regime"
            : "Low-vol regime",
          severity: regime.volhi ? "success" : "default",
          detail: regime.volhi
            ? `RV20 ${(regime.rv20! * 100).toFixed(1)}% exceeds its trailing-250d median — sentiment/flow readings are at their most informative (Garcia 2013).`
            : `RV20 ${(regime.rv20! * 100).toFixed(1)}% below its trailing-250d median — the neutral band widens; treat readings with lighter conviction (Garcia 2013).`,
        }
      : null,
    contrarian,
    coverageLabel,
    coverageSeverity,
    coverageDetail,
    asymmetry,
    dispersion,
    toneLabel,
    toneSeverity,
    lowCoverage: latest?.low_coverage === true,
    latest,
    weeklyZ,
  };
}

/**
 * The lexicon-vs-LLM ambiguity chip (fetched separately from the
 * disagreement endpoint): recent-30d share vs the trailing baseline —
 * elevated disagreement = ambiguous corpus = reduced conviction.
 */
export function deriveDisagreementState(
  rows: NewsTextStatesDisagreementRow[],
): { label: string; detail: string } | null {
  if (rows.length < 40) return null;
  const share = (win: NewsTextStatesDisagreementRow[]) => {
    const scored = win.reduce((a, r) => a + r.scored_count, 0);
    const dis = win.reduce((a, r) => a + r.disagree_count, 0);
    return scored >= 50 ? dis / scored : null;
  };
  const recent = share(rows.slice(-30));
  const baseline = share(rows.slice(0, -30));
  if (recent == null || baseline == null || baseline < 0.02) return null;
  if (recent >= baseline * 1.5) {
    return {
      label: "Lexicon-LLM disagreement elevated",
      detail: `${(recent * 100).toFixed(0)}% of scored articles disagree with their LLM digestion score over the last 30 days vs a ${(baseline * 100).toFixed(0)}% trailing baseline. Ambiguous corpus — the daily blend already halves these articles' weight; read the mood curves with reduced conviction.`,
    };
  }
  return null;
}

export function StateChipsRow({
  states,
  ambiguity,
}: {
  states: TextStates;
  ambiguity: { label: string; detail: string } | null;
}) {
  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
      {states.regime != null && (
        <Tooltip title={states.regime.detail}>
          <Chip
            size="small"
            variant="outlined"
            color={states.regime.severity}
            label={states.regime.label}
          />
        </Tooltip>
      )}
      <Tooltip title={states.moodDetail}>
        <Chip
          size="small"
          color={states.moodSeverity}
          label={states.moodLabel}
        />
      </Tooltip>
      {states.contrarian != null && (
        <Tooltip title={states.contrarian.detail}>
          <Chip
            size="small"
            variant="outlined"
            color="secondary"
            label={states.contrarian.label}
          />
        </Tooltip>
      )}
      <Tooltip title={states.coverageDetail}>
        <Chip
          size="small"
          color={states.coverageSeverity}
          label={states.coverageLabel}
        />
      </Tooltip>
      {states.asymmetry != null && (
        <Tooltip title={states.asymmetry.detail}>
          <Chip
            size="small"
            variant="outlined"
            color="warning"
            label={states.asymmetry.label}
          />
        </Tooltip>
      )}
      {states.dispersion != null && (
        <Tooltip title={states.dispersion.detail}>
          <Chip
            size="small"
            variant="outlined"
            color="warning"
            label={states.dispersion.label}
          />
        </Tooltip>
      )}
      {ambiguity != null && (
        <Tooltip title={ambiguity.detail}>
          <Chip
            size="small"
            variant="outlined"
            color="warning"
            label={ambiguity.label}
          />
        </Tooltip>
      )}
      {states.toneLabel != null && (
        <Chip size="small" variant="outlined" color={states.toneSeverity}
          label={states.toneLabel} />
      )}
      {states.lowCoverage && (
        <Tooltip title="Fewer than 3 scored articles that day — no reliable sentiment reading; the level/z fields are NULL and the ewm lines carry forward.">
          <Chip size="small" variant="outlined" label="Low coverage" />
        </Tooltip>
      )}
      {states.latest?.date != null && (
        <Typography variant="caption" color="text.secondary">
          as of {states.latest.date}
        </Typography>
      )}
    </Box>
  );
}
