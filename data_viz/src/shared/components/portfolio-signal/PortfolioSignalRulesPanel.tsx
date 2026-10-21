/**
 * PortfolioSignalRulesPanel — the PORTFOLIO tier's host surface: ONE
 * ROW PER PORTFOLIO in the roster table (code · cells · gate verdict
 * · drawdown benefit), EXPANDING into the two surfaces of the pair:
 *
 *   • PortfolioSurface — construction · benefit audit · combined
 *     member stats · the gate (WHAT THE PORTFOLIO TOOK)
 *   • CellRulesSurface — the coordinated member cells grouped as the
 *     PAIR (one leg's action → the other leg ≤5d), each with its own
 *     evidence
 *
 * Data: GET /trading-signals/portfolio?codes=… — the server matches
 * the portfolio rows whose legs sit in the codes and expands the ids
 * to FULL rule rows; each portfolio's LATEST
 * history_portfolio_signal_performance audit snapshot joins laterally.
 * The roster fetch (all managed codes) fills the no-match empty state.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Paper,
  Typography,
  useTheme,
} from "@mui/material";
import {
  fetchPortfolioSignals,
  fetchSignalPortfolios,
  type PortfolioSignalGroup,
  type SignalRule,
} from "@/lib/api-client";
import ExpandedTable, {
  type ExpandedTableColumn,
} from "@/shared/components/ExpandedTable";

import CellRulesSurface from "./cellSurface";
import PortfolioSurface from "./portfolioSurface";
import {
  DEFAULT_SEC_TYPE,
  portfolioAudit,
  portfolioTypeLabel,
  subOrderRank,
} from "./common";

export interface PortfolioSignalRulesPanelProps {
  /** The component codes (INDUSTRY ids for now) — every portfolio
   *  whose legs all sit in this list renders its rule group. */
  codes: ReadonlyArray<string>;
  /** The rows' sec_type scope (the pipeline stamp); default "index". */
  secType?: string;
  /** Bump to refetch (the host panel's Refresh button). */
  reloadKey?: number;
  /** The selected coordinated sub type (the host page's dropdown) —
   *  null/undefined = all; the expanded CELL surface filters to it. */
  subType?: string | null;
  /** The matched portfolios' coordinated sub types (SUB_ORDER-sorted),
   *  reported whenever the fetch resolves — the host dropdown's
   *  options. */
  onSubTypes?: (subs: string[]) => void;
}

export default function PortfolioSignalRulesPanel({
  codes,
  secType = DEFAULT_SEC_TYPE,
  reloadKey = 0,
  subType,
  onSubTypes,
}: PortfolioSignalRulesPanelProps) {
  const theme = useTheme();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [portfolios, setPortfolios] = useState<PortfolioSignalGroup[]>([]);
  const [rules, setRules] = useState<SignalRule[]>([]);
  const [roster, setRoster] = useState<string[]>([]);
  const [expandedKey, setExpandedKey] = useState<string | null>(null);
  // the expanded MEMBER rule inside the open portfolio's content
  const [openMember, setOpenMember] = useState<string | null>(null);
  const joined = codes.join(",");

  // the codes' portfolio groups + expanded rule rows
  useEffect(() => {
    const cleaned = [...new Set(
      joined.split(",").map((c) => c.trim()).filter(Boolean),
    )];
    if (cleaned.length === 0) {
      setPortfolios([]);
      setRules([]);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    setExpandedKey(null);
    fetchPortfolioSignals(secType, cleaned)
      .then((resp) => {
        if (cancelled) return;
        setPortfolios(resp.portfolios ?? []);
        setRules(resp.rules ?? []);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setPortfolios([]);
        setRules([]);
        setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [joined, secType, reloadKey]);

  // the managed roster (the no-match empty state's hint) — per reload
  useEffect(() => {
    let cancelled = false;
    fetchSignalPortfolios(secType)
      .then((resp) => {
        if (!cancelled) {
          setRoster((resp.portfolios ?? []).map((p) => p.code));
        }
      })
      .catch(() => {
        if (!cancelled) setRoster([]);
      });
    return () => {
      cancelled = true;
    };
  }, [secType, reloadKey]);

  const byId = useMemo(
    () => new Map(rules.map((r) => [r.signal_rule_id, r])),
    [rules],
  );
  // the Hedge Pairs layer's own rows per portfolio code (the
  // composite + the exits — NOT the imported components)
  const ownByCode = useMemo(() => {
    const m = new Map<string, SignalRule[]>();
    for (const r of rules) {
      if (r.rule_params.portfolio_role === "component") continue;
      const list = m.get(r.code) ?? [];
      list.push(r);
      m.set(r.code, list);
    }
    return m;
  }, [rules]);

  // the coordinated sub types present across the matched portfolios
  // (the orchestrating cells only — the mov_rsi components carry the
  // source families' own sub types), SUB_ORDER-sorted; reported to the
  // host dropdown
  const subTypes = useMemo(() => {
    const set = new Set<string>();
    for (const r of rules) {
      if (r.rule_params.portfolio_role !== "component") set.add(r.signal_sub_type);
    }
    return [...set].sort(
      (a, b) => subOrderRank(a) - subOrderRank(b) || a.localeCompare(b),
    );
  }, [rules]);
  const subTypesKey = subTypes.join("|");
  useEffect(() => {
    onSubTypes?.(subTypesKey ? subTypesKey.split("|") : []);
  }, [subTypesKey, onSubTypes]);

  const handleRowClick = useCallback(
    (p: PortfolioSignalGroup) => {
      setExpandedKey(expandedKey === p.code ? null : p.code);
    },
    [expandedKey],
  );

  const columns = useMemo<ExpandedTableColumn<PortfolioSignalGroup>[]>(
    () => [
      {
        key: "portfolio",
        label: "Portfolio",
        render: (p) => (
          <Box component="span" sx={{ display: "inline-flex", alignItems: "center", gap: 1 }}>
            <Box component="span" sx={{ fontWeight: 700 }}>
              {p.code}
            </Box>
            <Chip
              size="small"
              variant="outlined"
              sx={{ fontSize: "0.65rem", height: 20 }}
              label={portfolioTypeLabel(p.portfolio_type)}
            />
          </Box>
        ),
      },
      {
        key: "rules",
        label: "Cells",
        info: "member cells · coordinated sub rules",
        align: "right",
        render: (p) => `${p.n_rules} cells · ${p.n_sub_types} subs`,
      },
      {
        key: "valid",
        label: "Valid",
        align: "center",
        info: "cells clearing the side's dir_ave bar",
        render: (p) => (
          <Box component="span" sx={{ fontWeight: 600 }}>
            <Box component="span" sx={{ color: p.n_valid > 0 ? theme.palette.success.main : "text.disabled" }}>
              {p.n_valid}
            </Box>
            <Box component="span" sx={{ color: "text.secondary" }}>
              {" / "}
              {p.n_rules}
            </Box>
          </Box>
        ),
      },
      {
        key: "benefit",
        label: "DD benefit",
        info: "pair max drawdown vs the worst leg — positive = shallower (the hedge paid)",
        render: (p) => {
          const a = portfolioAudit(p);
          if (a == null) return "—";
          const r = a.maxDd.reduction_vs_worst_pct;
          if (!Number.isFinite(r)) return "—";
          return (
            <Box
              component="span"
              sx={{
                fontWeight: 600,
                color: r >= 0
                  ? theme.palette.success.main
                  : theme.palette.error.main,
                whiteSpace: "nowrap",
              }}
            >
              {`${r >= 0 ? "+" : "−"}${Math.abs(r).toFixed(1)}pp vs worst leg`}
            </Box>
          );
        },
      },
      {
        key: "latest_event",
        label: "Latest event",
        render: (p) => p.latest_event ?? "—",
      },
      {
        key: "latest_built",
        label: "Built",
        render: (p) => p.latest_built,
      },
    ],
    [theme],
  );

  return (
    <Paper variant="outlined" sx={{ px: 1.5, py: 1.25 }}>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: "block", mb: 0.75, px: 0.5 }}
      >
        Portfolio signal rules · {portfolios.length} — click a row to
        expand its construction + cells
      </Typography>
      {error && (
        <Alert severity="warning" sx={{ mb: 1 }}>
          Failed to load portfolio signals: {error}
        </Alert>
      )}
      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
          <CircularProgress size={24} />
        </Box>
      )}
      {!error && !loading && portfolios.length === 0 && (
        <Alert severity="info">
          {codes.filter((c) => c.trim()).length === 0
            ? "Pick codes to load their portfolio signal rules."
            : `No portfolio for these codes (${
                codes.filter((c) => c.trim()).join(", ")
              }). Managed: ${roster.join(", ") || "(none yet)"}`}
        </Alert>
      )}
      {!error && !loading && portfolios.length > 0 && (
        <ExpandedTable
          columns={columns}
          rows={portfolios}
          rowKey={(p) => p.code}
          maxHeight={320}
          onRowClick={handleRowClick}
          selectedRowKey={expandedKey}
          expandedRowKey={expandedKey}
          expandedContent={(p) => (
            <>
              <PortfolioSurface portfolio={p} />
              <CellRulesSurface
                portfolio={p}
                byId={byId}
                ownRows={ownByCode.get(p.code) ?? []}
                openMember={openMember}
                subType={subType ?? null}
                onToggleMember={(key) =>
                  setOpenMember(openMember === key ? null : key)}
              />
            </>
          )}
          emptyState={
            <Typography variant="body2" color="text.secondary" sx={{ px: 0.5, pb: 1 }}>
              No portfolio signal rules for these codes.
            </Typography>
          }
        />
      )}
    </Paper>
  );
}
