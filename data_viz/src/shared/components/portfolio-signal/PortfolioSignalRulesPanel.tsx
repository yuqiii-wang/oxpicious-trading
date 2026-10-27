/**
 * PortfolioSignalRulesPanel — the PORTFOLIO tier's host surface: ONE
 * SIMPLE ROW PER PORTFOLIO in the roster table (code · trigger
 * conditions · latest event), EXPANDING on click into the pair's two
 * surfaces — the DD audit, the members' combined stats and the
 * construction detail live THERE, never on the row:
 *
 *   • PortfolioRuleParams — the constant rule definition
 *     (portfolio_signal_rules.rule_params) as a structured spec
 *     sheet: the FIRST section of the expansion, FOLDED by default
 *     (its header row expands it) — construction · per-sub
 *     coordination · constants, loaded from the backend grammar
 *   • CellRulesSurface — the sub type's PORTFOLIO RULE (the pair's
 *     own orchestrating rows, deduped — the portfolio's coordination,
 *     never a cell, no action) + the CELL SIGNAL RULES under it (one
 *     witness rule per curve — conditions, never dated event records)
 *   • PortfolioSurface — the PORTFOLIO ITSELF, the LAST section of
 *     the expansion, FOLDED by default (its header row expands it):
 *     its own audit — entry-rule trigger condition · measured
 *     evidence · gate + benefit boost · DD benefit (WHAT THE PORTFOLIO
 *     TOOK; the portfolio is never a cell and has no action)
 *
 * The row's trigger-conditions column explains WHEN the portfolio's
 * signals fire — one plain-language line per coordinated sub rule,
 * derived from rule_params.subs (common.portfolioTriggerLines).
 *
 * Data: GET /trading-signals/portfolio?codes=… — the server matches
 * the portfolio rows whose legs sit in the codes and expands the ids
 * to FULL rule rows; each portfolio's LATEST
 * history_portfolio_signal_performance audit snapshot joins laterally.
 * The roster fetch (all managed codes) fills the no-match empty state.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useDocs } from "@/shared/docs";
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Paper,
  Typography,
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
import PortfolioRuleParams from "./PortfolioRuleParams";
import {
  DEFAULT_SEC_TYPE,
  portfolioSubLabel,
  portfolioTriggerLines,
  portfolioTypeLabel,
} from "./common";

export interface PortfolioSignalRulesPanelProps {
  /** The component codes (INDUSTRY ids for now) — every portfolio
   *  whose legs all sit in this list renders its rule groups. */
  codes: ReadonlyArray<string>;
  /** The rows' sec_type scope (the pipeline stamp); default "index". */
  secType?: string;
  /** Bump to refetch (the host panel's Refresh button). */
  reloadKey?: number;
  /** The matched portfolio rules' OWN portfolio sub types (the roster
   *  rows' identity, roster order), reported whenever the fetch
   *  resolves — the host dropdown's options: ONE ROW PER PORTFOLIO
   *  RULE since the 2026-10-25 portfolio_sub_type split (trio wash /
   *  spread are the rows themselves), so the dropdown lists the
   *  portfolios 1:1. */
  onPortfolioSubTypes?: (subs: string[]) => void;
  /** The host dropdown's portfolio sub-type focus (null = all): the
   *  matching portfolio row renders SELECTED + EXPANDED and the roster
   *  NARROWS to it (the other subs' rows hidden — the dropdown
   *  choosing the portfolio); a null focus shows every row and never
   *  touches the expansion. */
  subTypeFocus?: string | null;
  /** The clicked roster row's portfolio sub type (null when the click
   *  collapsed the row) — the host dropdown syncs to the row. */
  onRowSelect?: (portfolioSubType: string | null) => void;
}

export default function PortfolioSignalRulesPanel({
  codes,
  secType = DEFAULT_SEC_TYPE,
  reloadKey = 0,
  onPortfolioSubTypes,
  subTypeFocus = null,
  onRowSelect,
}: PortfolioSignalRulesPanelProps) {
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

  // ONE ROSTER ROW PER PORTFOLIO RULE (the 2026-10-25 sub split):
  // trio wash / spread are separate portfolio signals — the row key
  // carries the rule's sub identity
  const rowKeyOf = (p: PortfolioSignalGroup) =>
    `${p.code}|${p.portfolio_sub_type}`;

  // the matched portfolio rules' OWN sub types (the roster rows'
  // identity — the host dropdown lists the portfolios 1:1), reported
  // in roster order
  const portfolioSubTypes = useMemo(() => {
    const set = new Set<string>();
    for (const p of portfolios) set.add(p.portfolio_sub_type);
    return [...set];
  }, [portfolios]);
  const portfolioSubTypesKey = portfolioSubTypes.join("|");
  useEffect(() => {
    onPortfolioSubTypes?.(
      portfolioSubTypesKey ? portfolioSubTypesKey.split("|") : [],
    );
  }, [portfolioSubTypesKey, onPortfolioSubTypes]);

  // the host dropdown's focus selects + expands the matching portfolio
  // row (the dropdown choosing the portfolio); a null focus — "all",
  // or a row the click collapsed — never touches the expansion
  useEffect(() => {
    if (subTypeFocus == null) return;
    const hit = portfolios.find(
      (p) => p.portfolio_sub_type === subTypeFocus,
    );
    if (hit != null) setExpandedKey(rowKeyOf(hit));
  }, [subTypeFocus, portfolios]);

  // the focus ALSO narrows the roster: a non-null sub type hides the
  // other subs' rows (trio wash chosen → the spread row does not
  // render; null = all). The reported dropdown options stay the FULL
  // matched set so the choice can still move between subs.
  const visiblePortfolios = useMemo(
    () =>
      subTypeFocus == null
        ? portfolios
        : portfolios.filter((p) => p.portfolio_sub_type === subTypeFocus),
    [portfolios, subTypeFocus],
  );

  const handleRowClick = useCallback(
    (p: PortfolioSignalGroup) => {
      const next = expandedKey === rowKeyOf(p) ? null : rowKeyOf(p);
      setExpandedKey(next);
      onRowSelect?.(next == null ? null : p.portfolio_sub_type);
    },
    [expandedKey, onRowSelect],
  );

  const docs = useDocs();
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
            <Chip
              size="small"
              sx={{ fontSize: "0.65rem", height: 20 }}
              label={portfolioSubLabel(p.portfolio_sub_type)}
            />
          </Box>
        ),
      },
      {
        key: "triggers",
        label: "Trigger conditions",
        info: docs.text("ui.portfolio-rules.col.triggers"),
        render: (p) => {
          const lines = portfolioTriggerLines(p, null);
          if (lines.length === 0) return "—";
          return (
            <Box sx={{ display: "grid", gap: 0.25, py: 0.25 }}>
              {lines.map((l) => (
                <Typography
                  key={l.word}
                  variant="caption"
                  color="text.secondary"
                  sx={{ display: "block", whiteSpace: "normal" }}
                >
                  <Box
                    component="span"
                    sx={{ fontWeight: 700, color: "text.primary" }}
                  >
                    {l.word}
                  </Box>
                  {` — ${l.text}`}
                </Typography>
              ))}
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
    [docs],
  );

  return (
    <Paper variant="outlined" sx={{ px: 1.5, py: 1.25 }}>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: "block", mb: 0.75, px: 0.5 }}
      >
        Portfolio signal rules · {visiblePortfolios.length} — click a row to
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
          rows={visiblePortfolios}
          rowKey={rowKeyOf}
          maxHeight={760}
          onRowClick={handleRowClick}
          selectedRowKey={expandedKey}
          expandedRowKey={expandedKey}
          expandedContent={(p) => (
            <>
              <PortfolioRuleParams ruleParams={p.rule_params} />
              <CellRulesSurface
                portfolio={p}
                byId={byId}
                openMember={openMember}
                onToggleMember={(key) =>
                  setOpenMember(openMember === key ? null : key)}
              />
              <PortfolioSurface portfolio={p} />
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
