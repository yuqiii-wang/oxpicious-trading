/**
 * ruleParams — the PURE readers of a rule cell's rule_params payload
 * (the shared vocabulary of the rules-era panels): the cell's display
 * key and the breach-bar word decoded from the family's bar_def.
 * Pure .ts (no component) so the panels stay fast-refresh clean.
 */
import type { SignalRule } from "@/lib/api-client";

export const ruleCellKey = (r: SignalRule): string =>
  `${r.signal_sub_type}|${r.trend}|${r.regime_state}|${r.action}`;

const fmtPct = (v: number): string => {
  const s = (v * 100).toFixed(v * 100 % 1 === 0 ? 0 : 2).replace("-", "−");
  return s;
};

/** The breach-bar display word from the cell's rule_params.bar_def —
 *  each family's own bar vocabulary, read defensively (unknown kinds
 *  fall back to the raw def). Pure; exported for reuse/tests. */
export function ruleBarLabel(barDef: Record<string, unknown> | undefined):
  string {
  if (!barDef || typeof barDef !== "object") return "—";
  const kind = String(barDef.kind ?? "");
  const num = (k: string): number | null => {
    const v = barDef[k];
    return typeof v === "number" ? v : null;
  };
  switch (kind) {
    case "rolling_quantile": {
      const tail = num("tail_pct");
      return tail == null ? "own tail quantile"
        : `own ${tail}% tail · ${String(barDef.window_days ?? "")}d`;
    }
    case "fixed_sigma": {
      const k = num("k");
      return k == null ? "±kσ" : `±${k}σ`;
    }
    case "cross_excess": {
      const bar = num("min_below_gap");
      return bar == null ? "cross excess bar"
        : `excess ≤ ${fmtPct(bar)}%`;
    }
    case "gap_pct": {
      const below = num("below");
      return below == null ? "gap bar" : `gap ≤ ${fmtPct(below)}%`;
    }
    case "episode_gap": {
      const gap = num("max_gap_days");
      return gap == null ? "retest window" : `retest gap ≤ ${gap}d`;
    }
    case "wall_dist_pct": {
      const below = num("below");
      const above = num("above");
      if (below != null) return `dist ≤ ${fmtPct(below)}%`;
      if (above != null) return `dist ≥ +${fmtPct(above)}%`;
      return "wall breach";
    }
    case "corr_slope_interaction":
    case "corr_slope_z_interaction": {
      const corr = num("corr_below");
      const slope = num("slope5_gt");
      const z = num("z252_gt");
      const parts = [
        corr != null ? `corr < ${corr}` : null,
        slope != null ? `slope > +${fmtPct(slope)}%` : null,
        z != null ? `z > ${z}` : null,
      ].filter((p): p is string => p !== null);
      return parts.length > 0 ? parts.join(" & ") : "axis interaction";
    }
    case "corr_cross_up": {
      const through = num("through");
      const witness = num("witness_sessions");
      return `corr ≥ ${through ?? "−0.5"}`
        + (witness != null ? ` · witness ${witness}d` : "");
    }
    case "vol_ath_escalation": {
      const win = num("ath_window");
      const margin = num("ath_margin_pct");
      const slope = num("slope5_gt");
      const dn = num("downside_window");
      const parts = [
        win != null && margin != null
          ? `vol > ${win}d max +${fmtPct(margin / 100)}%` : null,
        slope != null ? `slope > +${fmtPct(slope)}%` : null,
        dn != null ? `spot < ${dn}d-ago` : null,
      ].filter((p): p is string => p !== null);
      return parts.length > 0 ? parts.join(" & ") : "vol escalation";
    }
    default:
      return kind ? kind : "—";
  }
}
