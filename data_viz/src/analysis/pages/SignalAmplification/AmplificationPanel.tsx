/**
 * AmplificationPanel — the Signal Amplification page's content for one
 * picked security: the binding-config RULES panel (one row per
 * amplified family — which amplifiers bind, at what side/strength,
 * with the measured per-amplifier evidence) above the amplifier-day
 * RECORDS table (the original record keeper — one row per amplifier
 * day with the day's OHLCV anatomy facts + the per-bound-family
 * amplification applied; a row click expands that day's bindings).
 */
import { Box, Chip, CircularProgress, MenuItem, Select, Typography } from "@mui/material";
import { useCallback, useEffect, useMemo, useState } from "react";
import ExpandedTable, {
  type ExpandedTableColumn,
} from "@/shared/components/ExpandedTable";
import {
  fetchSignalAmplificationHistory,
  fetchSignalAmplificationRules,
  type SignalAmplificationBindingResult,
  type SignalAmplificationRecord,
  type SignalAmplificationRule,
} from "@/lib/api-client";
import { fmtDate, fmtStamp } from "@/shared/datetime";
import {
  AMPLIFIERS,
  AMPLIFIER_TYPES,
  amplifierMeta,
  amplifierMetaByCode,
  amplifierTypeByCode,
} from "./amplifiers";

/** The sub-type dropdown options for the picked parent type (all subs
 *  when no type is picked — the labels are unique across types). */
function amplifierOptions(typeFilter: string) {
  const subs = typeFilter
    ? AMPLIFIER_TYPES.find((t) => t.key === typeFilter)?.subs ?? []
    : AMPLIFIERS;
  return subs.map((a) => ({
    value: a.key,
    label: `${a.label} (${a.zh})`,
  }));
}

/** "buy" / "sell" / "discount" chip tone. */
const SIDE_COLOR: Record<string, "success" | "warning" | "default"> = {
  buy: "success",
  sell: "warning",
  discount: "default",
};

function pct(v: number | null | undefined, digits = 2): string {
  return v === null || v === undefined ? "–"
    : `${(v * 100).toFixed(digits)}%`;
}

function num(v: number | null | undefined, digits = 3): string {
  return v === null || v === undefined ? "–" : v.toFixed(digits);
}

function bp(v: number): string {
  return `${v > 0 ? "+" : ""}${v}`;
}

export default function AmplificationPanel({
  secType,
  code,
}: {
  secType: string;
  code: string;
}) {
  const [rules, setRules] = useState<SignalAmplificationRule[]>([]);
  const [records, setRecords] = useState<SignalAmplificationRecord[]>([]);
  // DEFAULT CHOICE: the ohlcv parent type with all its sub types —
  // the margin type stays one dropdown switch away
  const [typeFilter, setTypeFilter] = useState<string>("ohlcv");
  const [amplifier, setAmplifier] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [ruleRes, recRes] = await Promise.all([
        fetchSignalAmplificationRules(secType, code),
        // the sub filter pins the parent server-side; a bare type
        // filter narrows the parent axis only
        fetchSignalAmplificationHistory(
          secType, code, amplifier || typeFilter || null),
      ]);
      setRules(ruleRes.rules);
      setRecords(recRes.records);
    } finally {
      setLoading(false);
    }
  }, [secType, code, amplifier, typeFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  // the RULES panel follows the two-level filter: only families whose
  // bound amplifiers match the picked type / sub
  const filteredRules = useMemo(() => {
    if (!typeFilter && !amplifier) return rules;
    return rules.filter((r) =>
      (r.rule_params?.amplifiers ?? []).some((a) =>
        (!typeFilter || a.amplifier_label === typeFilter)
        && (!amplifier || a.sub_label === amplifier)));
  }, [rules, typeFilter, amplifier]);

  // ---- the rules table ------------------------------------------------
  const ruleColumns: ExpandedTableColumn<SignalAmplificationRule>[] =
    useMemo(
      () => [
        {
          key: "signal_type",
          label: "Amplified family",
          width: 130,
          render: (r) => <strong>{r.signal_type}</strong>,
        },
        {
          key: "valid",
          label: "Valid",
          align: "center",
          width: 80,
          render: (r) => (
            <Chip
              size="small"
              color={r.is_valid ? "success" : "default"}
              label={r.is_valid ? "evidence" : "obs"}
            />
          ),
        },
        {
          key: "amplifiers",
          label: "Bound amplifiers",
          render: (r) => (
            <Box sx={{ display: "flex", gap: 0.5, flexWrap: "wrap" }}>
              {(r.rule_params?.amplifiers ?? []).map((a) => (
                <Chip
                  key={`${a.amplifier_type}-${a.sub_type}`}
                  size="small"
                  color={SIDE_COLOR[
                    amplifierMeta(a.sub_label)?.side ?? "discount"
                  ]}
                  variant="outlined"
                  label={`${a.amplifier_label}·${a.sub_label} · ${a.action_label} · ${bp(a.base_bp)}bp`}
                />
              ))}
            </Box>
          ),
        },
        {
          key: "evidence",
          label: "Evidence (n · Δ · hit)",
          align: "right",
          width: 220,
          render: (r) => {
            // nested per the two-level axis: str(type code) ->
            // str(sub code) -> the evidence block
            const ev = r.rule_params?.evidence ?? {};
            const parts = Object.entries(ev).flatMap(([t, subs]) => {
              const typeKey = amplifierTypeByCode(Number(t))?.key;
              return Object.entries(subs).map(([s, e]) => {
                const meta = typeKey
                  ? amplifierMetaByCode(typeKey, Number(s))
                  : undefined;
                return (
                  <Typography
                    key={`${t}-${s}`}
                    variant="body2"
                    component="div"
                    sx={{
                      color: e.delta_pct !== null && e.delta_pct > 0
                        ? "success.main" : "text.secondary",
                    }}
                  >
                    {meta?.label ?? `${t}:${s}`}:{" "}
                    n={e.n} · Δ {num(e.delta_pct, 2)}% · hit{" "}
                    {num(e.hit_pct, 1)}%
                  </Typography>
                );
              });
            });
            return parts.length ? (
              <Box>{parts}</Box>
            ) : (
              "–"
            );
          },
        },
        {
          key: "built",
          label: "Built",
          align: "right",
          width: 130,
          render: (r) => fmtStamp(r.created_at),
        },
      ],
      [],
    );

  // ---- the records table ----------------------------------------------
  const recordColumns: ExpandedTableColumn<SignalAmplificationRecord>[] =
    useMemo(
      () => [
        { key: "date", label: "Date", width: 110, render: (r) => fmtDate(r.date) },
        {
          key: "type",
          label: "Type",
          align: "center",
          width: 110,
          render: (r) => {
            // the wire carries the decoded parent-type label
            const type = AMPLIFIER_TYPES.find(
              (t) => t.key === r.amplifier_type,
            );
            return type ? type.label : r.amplifier_type;
          },
        },
        {
          key: "sub_type",
          label: "Sub type",
          width: 200,
          render: (r) => {
            const meta = amplifierMeta(r.sub_type);
            return (
              <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                <Chip
                  size="small"
                  color={SIDE_COLOR[meta?.side ?? "discount"]}
                  label={meta?.label ?? r.sub_type}
                />
                <Typography variant="body2" color="text.secondary">
                  {meta?.zh}
                </Typography>
              </Box>
            );
          },
        },
        {
          key: "margin",
          label: "Margin vs bar",
          align: "right",
          width: 110,
          render: (r) => pct(r.margin_vs_bar),
        },
        {
          key: "gap",
          label: "Gap (open)",
          align: "right",
          width: 100,
          render: (r) => pct(r.gap_pct),
        },
        {
          key: "wick",
          label: "Wick",
          align: "right",
          width: 90,
          render: (r) => pct(r.wick_pct),
        },
        {
          key: "amt",
          label: "Amt ratio",
          align: "right",
          width: 95,
          render: (r) => num(r.amt_ratio),
        },
        {
          key: "close_pos",
          label: "Close pos",
          align: "right",
          width: 95,
          render: (r) => num(r.close_pos, 2),
        },
        {
          key: "depth",
          label: "Depth",
          align: "right",
          width: 90,
          render: (r) => num(r.depth),
        },
        {
          key: "fired",
          label: "Bound · fired",
          align: "center",
          width: 120,
          render: (r) => {
            const fired = r.bindings.filter((b) => b.fired).length;
            return `${fired}/${r.bindings.length}`;
          },
        },
        {
          key: "top_amp",
          label: "Top binding",
          align: "right",
          render: (r) => {
            const top = [...r.bindings].sort(
              (a, b) => Math.abs(b.amplify_bp) - Math.abs(a.amplify_bp),
            )[0];
            return top
              ? `${top.signal_label} · ${top.action_label} · ${bp(top.amplify_bp)}bp${top.fired ? " · fired" : ""}`
              : "–";
          },
        },
      ],
      [],
    );

  const renderRecordExpanded = (r: SignalAmplificationRecord) => (
    <Box sx={{ p: 1, display: "grid", gap: 1 }}>
      <Typography variant="caption" color="text.secondary">
        {amplifierMeta(r.sub_type)?.description}
      </Typography>
      <ExpandedTable<SignalAmplificationBindingResult>
        columns={[
          { key: "family", label: "Bound family", width: 140,
            render: (b) => b.signal_label },
          { key: "side", label: "Side", align: "center", width: 80,
            render: (b) => (
              <Chip size="small" variant="outlined"
                color={SIDE_COLOR[
                  b.action_label === "buy" && b.base_bp < 0
                    ? "discount" : b.action_label]}
                label={b.base_bp < 0 ? "discount" : b.action_label} />
            ) },
          { key: "base", label: "Base bp", align: "right", width: 90,
            render: (b) => bp(b.base_bp) },
          { key: "amp", label: "Amplify bp", align: "right", width: 100,
            render: (b) => (
              <strong>{bp(b.amplify_bp)}</strong>
            ) },
          { key: "fired", label: "Fired", align: "center", width: 80,
            render: (b) => (b.fired ? `✓ (${b.fired_n})` : "–") },
        ]}
        rows={r.bindings}
        rowKey={(b) => `${b.signal_type}-${b.action_label}`}
        maxHeight={260}
      />
    </Box>
  );

  return (
    <Box sx={{ display: "grid", gap: 3 }}>
      {/* the two-level amplifier filter (type + sub type dropdowns) */}
      <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
        <Select
          size="small"
          displayEmpty
          value={typeFilter}
          onChange={(e) => {
            const next = e.target.value as string;
            setTypeFilter(next);
            // a picked sub outside the newly picked type no longer
            // applies — drop it
            if (next && amplifier
                && amplifierMeta(amplifier)?.typeKey !== next) {
              setAmplifier("");
            }
          }}
          renderValue={(v) =>
            v
              ? `Type: ${AMPLIFIER_TYPES.find((t) => t.key === v)?.label ?? v}`
              : "Type: All"}
          sx={{ minWidth: 170 }}
        >
          <MenuItem value="">All types</MenuItem>
          {AMPLIFIER_TYPES.map((t) => (
            <MenuItem key={t.key} value={t.key}
              sx={{ fontSize: "0.8rem", py: 0.25 }}>
              {t.label} ({t.zh})
            </MenuItem>
          ))}
        </Select>
        <Select
          size="small"
          displayEmpty
          value={amplifier}
          onChange={(e) => setAmplifier(e.target.value as string)}
          renderValue={(v) => {
            const m = v ? amplifierMeta(v) : undefined;
            return m ? `Sub type: ${m.label} (${m.zh})` : "Sub type: All";
          }}
          sx={{ minWidth: 230 }}
        >
          <MenuItem value="">All subs</MenuItem>
          {amplifierOptions(typeFilter).map((o) => (
            <MenuItem key={o.value} value={o.value}
              sx={{ fontSize: "0.8rem", py: 0.25 }}>
              {o.label}
            </MenuItem>
          ))}
        </Select>
        {loading && <CircularProgress size={18} />}
      </Box>

      {/* the RULES panel */}
      <Box>
        <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
          Amplification rules{" "}
          <Typography variant="caption" color="text.secondary">
            — the binding config per amplified family (which amplifiers,
            side, base strength; the evidence is the measured verdict);
            the type / sub filter above narrows both panels
          </Typography>
        </Typography>
        <ExpandedTable<SignalAmplificationRule>
          columns={ruleColumns}
          rows={filteredRules}
          rowKey={(r) => r.signal_type}
          maxHeight={320}
          emptyState={
            <Typography variant="body2" color="text.secondary">
              {(typeFilter || amplifier)
                ? "No bound amplifier matches the picked filter for this security."
                : "No amplification configs for this security yet — the pipeline binds a family once its signals fire."}
            </Typography>
          }
        />
      </Box>

      {/* the RECORDS table */}
      <Box>
        <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
          Amplifier days{" "}
          <Typography variant="caption" color="text.secondary">
            — the original record keeper: one row per amplifier day with
            the day's anatomy facts; click a row for the per-family
            amplification applied
          </Typography>
        </Typography>
        <ExpandedTable<SignalAmplificationRecord>
          columns={recordColumns}
          rows={records}
          rowKey={(r) => `${r.date}-${r.amplifier_type}-${r.sub_type}`}
          maxHeight={560}
          expandedRowKey={expandedKey}
          onRowClick={(r) =>
            setExpandedKey((k) =>
              k === `${r.date}-${r.amplifier_type}-${r.sub_type}`
                ? null : `${r.date}-${r.amplifier_type}-${r.sub_type}`)
          }
          expandedContent={renderRecordExpanded}
          emptyState={
            <Typography variant="body2" color="text.secondary">
              No amplifier days recorded for this security
              {(typeFilter || amplifier)
                ? ` (${[typeFilter, amplifier].filter(Boolean).join(" / ")})`
                : ""}.
            </Typography>
          }
        />
      </Box>
    </Box>
  );
}
