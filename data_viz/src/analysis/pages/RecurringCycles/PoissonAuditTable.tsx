/**
 * PoissonAuditTable — the canonical Poisson-audit presentation for the
 * Recurring Cycles page: OBSERVED vs EXPECTED vs tail probability per
 * auditable day period. The spectrum bar charts above show the verdict
 * (significance bars vs the p<0.05 markline); this table shows the raw
 * pair behind it:
 *
 *   hits(d)   — observed prominence-filtered swing-hit count (the recEXT
 *               numerator, uncapped integral count);
 *   λ̂₀(d)    — the chance expectation of the point-process null
 *               (empirically calibrated n_pool × g(pool-bin, d) rate);
 *   ×null     — evidence ratio hits/λ̂₀ (how many × the chance rate);
 *   −log10 p  — Bonferroni-adjusted tail significance (≥ 1.30 ⇔ p<0.05,
 *               ≥ 2.0 ⇔ p<0.01);
 *   p         — the Bonferroni-adjusted p itself (= 10^−sig);
 *   verdict   — the significance tier.
 *
 * One row per (range_days, auditable day d ≤ N/3) across ALL windows for
 * the selected date, pre-sorted by significance desc so the day periods
 * that beat chance surface first. The headline RECURRING period (argmax
 * of strength, row.period_days) is marked ◆ in green on its day cell.
 * Rendered by the globally shared ExpandedTable (layered headers +
 * opt-in per-column header filters: ticks for window/verdict, numeric
 * ranges for day/hits/λ̂₀/evidence/sig), mirroring MaSpread's
 * ForecastTable usage.
 *
 * Data: analysis.recurring_cycles.hits_spectrum / lam0_spectrum /
 * significance_spectrum (day-aligned arrays, element j = day j+2) served
 * by the spectrum endpoint. Rows built client-side — no extra endpoint.
 */
import { useMemo } from "react";
import { Box, Typography } from "@mui/material";
import ExpandedTable, {
  type ExpandedTableColumn,
} from "@/shared/components/ExpandedTable";
import { UP_COLOR } from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import { SIG05 } from "./spectrumOption";
import type { RecurringCyclesSpectrumRow } from "@shared/types";

/** One audit row: (window, auditable day period) with the observed /
 *  expected / p triple. */
interface AuditRow {
  range_days: number;
  day: number;
  /** Observed swing-hit count (integral). */
  hits: number;
  /** Chance expectation λ̂₀(d) of the point-process null. */
  lam0: number;
  /** hits / λ̂₀ — how many × the chance rate. */
  evidence: number;
  /** −log10 Bonferroni p (stored; 0 = not significant). */
  sig: number;
  /** The Bonferroni-adjusted p itself (= 10^−sig). */
  p: number;
  /** p-value tier label (ticks-filter value). */
  tier: string;
  /** The headline recurring period (argmax of strength) for its window. */
  isDom: boolean;
}

/** p-value tier for a stored significance value (−log10 Bonferroni p). */
function pTier(sig: number): string {
  if (sig >= 2.0) return "p<0.01";
  if (sig >= SIG05) return "p<0.05";
  if (sig >= 1.0) return "p<0.1";
  return "n.s.";
}

/** Build the audit rows: all auditable day periods (d ≤ N/3) across the
 *  windows, pre-sorted by significance desc. */
function buildAuditRows(spectrums: RecurringCyclesSpectrumRow[]): AuditRow[] {
  const rows: AuditRow[] = [];
  for (const spec of spectrums) {
    const { range_days: n, hits_spectrum: hitsSpec, lam0_spectrum: lamSpec } = spec;
    // Rows predating the raw observed/expected spectra have empty arrays.
    if (!hitsSpec || hitsSpec.length === 0 || !lamSpec || lamSpec.length === 0) {
      continue;
    }
    const sigSpec = spec.significance_spectrum;
    const maxDay = Math.floor(n / 3); // auditable: ≥ 3 cycles in the window
    for (let j = 0; j < hitsSpec.length; j++) {
      const d = j + 2;
      if (d > maxDay) break;
      const hits = hitsSpec[j] ?? 0;
      const lam0 = lamSpec[j] ?? 0;
      const sig = sigSpec?.[j] ?? 0;
      rows.push({
        range_days: n,
        day: d,
        hits,
        lam0,
        evidence: lam0 > 0 ? hits / lam0 : 0,
        sig,
        p: Math.pow(10, -sig),
        tier: pTier(sig),
        isDom: d === spec.period_days && spec.period_days > 0,
      });
    }
  }
  // Significant day periods surface first; ties by evidence then window/day.
  rows.sort(
    (a, b) =>
      b.sig - a.sig ||
      b.evidence - a.evidence ||
      a.range_days - b.range_days ||
      a.day - b.day,
  );
  return rows;
}

/** Muted em-dash cell. */
function dash() {
  return (
    <Typography component="span" variant="inherit" color="text.disabled">
      —
    </Typography>
  );
}

interface Props {
  /** Spectrum rows for the selected (code, date) — all range_days. */
  spectrums: RecurringCyclesSpectrumRow[];
  /** Scope key — filters reset when it changes (code / date). */
  scopeKey: string;
  /** Header-filter master switch — threaded from the spectrum response's
   *  `enable_filters` backend arg (disabled by default). */
  enableFilters?: boolean;
}

export function PoissonAuditTable({
  spectrums,
  scopeKey,
  enableFilters = false,
}: Props) {
  const rows = useMemo(() => buildAuditRows(spectrums), [spectrums]);

  const columns: ExpandedTableColumn<AuditRow>[] = useMemo(
    () => [
      {
        key: "window",
        label: "window",
        align: "right",
        width: 58,
        render: (r) => `${r.range_days}d`,
        filter: { type: "ticks", value: (r) => String(r.range_days) },
      },
      {
        key: "day",
        label: "day",
        align: "right",
        width: 56,
        render: (r) =>
          r.isDom ? (
            <Box component="span" sx={{ color: UP_COLOR, fontWeight: 700 }}>
              ◆ {r.day}
            </Box>
          ) : (
            <>{r.day}</>
          ),
        filter: { type: "range", value: (r) => r.day },
      },
      {
        key: "hits",
        label: "hits",
        align: "right",
        width: 52,
        group: "observed",
        render: (r) => (Number.isFinite(r.hits) ? <>{Math.round(r.hits)}</> : dash()),
        filter: { type: "range", value: (r) => r.hits },
      },
      {
        key: "lam0",
        label: "λ̂₀ exp",
        align: "right",
        width: 62,
        group: "null model",
        render: (r) => (Number.isFinite(r.lam0) ? <>{fmtNum(r.lam0, 2)}</> : dash()),
        filter: { type: "range", value: (r) => r.lam0 },
      },
      {
        key: "evidence",
        label: "×null",
        align: "right",
        width: 58,
        group: "audit",
        render: (r) => (Number.isFinite(r.evidence) ? <>{fmtNum(r.evidence, 1)}×</> : dash()),
        filter: { type: "range", value: (r) => r.evidence },
      },
      {
        key: "sig",
        label: "−log10 p",
        align: "right",
        width: 68,
        group: "audit",
        render: (r) => (
          <Box
            component="span"
            sx={{ color: r.sig >= SIG05 ? UP_COLOR : "text.primary", fontWeight: r.sig >= SIG05 ? 600 : 400 }}
          >
            {fmtNum(r.sig, 2)}
          </Box>
        ),
        filter: { type: "range", value: (r) => r.sig },
      },
      {
        key: "p",
        label: "p bonf",
        align: "right",
        width: 70,
        group: "audit",
        render: (r) =>
          r.sig > 0 ? (
            <>{r.p < 1e-4 ? r.p.toExponential(1) : r.p.toFixed(4)}</>
          ) : (
            dash()
          ),
      },
      {
        key: "tier",
        label: "verdict",
        align: "center",
        width: 68,
        group: "audit",
        render: (r) => (
          <Box
            component="span"
            sx={{
              color:
                r.tier === "p<0.01" || r.tier === "p<0.05"
                  ? UP_COLOR
                  : r.tier === "n.s."
                    ? "text.disabled"
                    : "text.primary",
              fontWeight: r.tier === "p<0.01" || r.tier === "p<0.05" ? 600 : 400,
            }}
          >
            {r.tier}
          </Box>
        ),
        filter: { type: "ticks", value: (r) => r.tier },
      },
    ],
    [],
  );

  return (
    <ExpandedTable
      columns={columns}
      rows={rows}
      rowKey={(r) => `${r.range_days}-${r.day}`}
      maxHeight={320}
      enableFilters={enableFilters}
      filterScopeDeps={[scopeKey]}
      emptyState={
        <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.68rem" }}>
          no Poisson-audit spectra for this date — the raw observed/expected
          arrays are backfilled by{" "}
          <code>python -m analyze.recurring_cycles</code> (incremental run
          picks up rows missing them)
        </Typography>
      }
    />
  );
}

export default PoissonAuditTable;
