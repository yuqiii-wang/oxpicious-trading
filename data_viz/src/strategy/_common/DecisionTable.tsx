/**
 * DecisionTable — expandable trade-decisions table for any strategy backtest.
 *
 * Renders as an MUI Accordion (collapsed by default) with a summary showing
 * the decision count. When expanded, shows a scrollable table of BUY/SELL
 * decisions with columns:
 *   #, Side, Signal, Exec, Conf, Fill, Position, Cash After, P&L, Reason
 *
 * "Conf" is the 0-100 confidence score stored as trade_decision.qty; it
 * drives deployment (BUY deploys conf/100 of allocated cash; SELL closes
 * conf/100 of position). Displayed as an integer with a colored bar so weak
 * vs strong signals are visually distinguishable.
 *
 * Works with any StrategyDecision[] that follows the shared type.
 */
import {
  Accordion, AccordionDetails, AccordionSummary,
  Box, Chip, Typography,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import type { StrategyDecision } from "../../../shared/types";

interface DecisionTableProps {
  decisions: StrategyDecision[];
  /** Accordion expanded state. Defaults to false (collapsed). */
  defaultExpanded?: boolean;
  maxHeight?: number;
}

export default function DecisionTable({
  decisions,
  defaultExpanded = false,
  maxHeight = 400,
}: DecisionTableProps) {
  if (decisions.length === 0) return null;

  const nBuys = decisions.filter((d) => d.side === "BUY").length;
  const nSells = decisions.filter((d) => d.side === "SELL").length;

  return (
    <Accordion
      defaultExpanded={defaultExpanded}
      sx={{
        bgcolor: "background.paper",
        border: 1,
        borderColor: "divider",
        borderRadius: "1.5px !important",
        "&:before": { display: "none" },
      }}
    >
      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1.5, mr: 2 }}>
          <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
            Trade Decisions
          </Typography>
          <Chip label={`${decisions.length}`} size="small" sx={{ fontSize: "0.7rem" }} />
          <Chip label={`${nBuys} BUY`} size="small" color="success" variant="outlined" sx={{ fontSize: "0.7rem" }} />
          <Chip label={`${nSells} SELL`} size="small" color="error" variant="outlined" sx={{ fontSize: "0.7rem" }} />
        </Box>
      </AccordionSummary>
      <AccordionDetails sx={{ p: 1 }}>
        <Box
          sx={{
            maxHeight,
            overflow: "auto",
          }}
        >
          <Box
            component="table"
            sx={{
              width: "100%",
              borderCollapse: "collapse",
              fontSize: "0.75rem",
              "& th, & td": {
                borderBottom: 1,
                borderColor: "divider",
                px: 1,
                py: 0.5,
                textAlign: "left",
              },
              "& th": {
                fontWeight: 600,
                color: "text.secondary",
                position: "sticky",
                top: 0,
                bgcolor: "background.paper",
                zIndex: 1,
              },
            }}
          >
            <thead>
              <tr>
                <th>#</th>
                <th>Side</th>
                <th>Signal</th>
                <th>Exec</th>
                <th>Conf</th>
                <th>Fill</th>
                <th>Position</th>
                <th>Cash After</th>
                <th>P&L</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {decisions.map((d) => (
                <tr key={d.decision_no}>
                  <td>{d.decision_no}</td>
                  <td style={{ color: d.side === "BUY" ? "#4caf50" : "#f44336", fontWeight: 700 }}>
                    {d.side}
                  </td>
                  <td>{d.signal_date}</td>
                  <td>{d.exec_date}</td>
                  <td>
                    <span
                      title={`Confidence: ${d.qty.toFixed(2)} / 100`}
                      style={{
                        display: "inline-block",
                        minWidth: 36,
                        textAlign: "right",
                        fontWeight: 600,
                        color: d.qty >= 66
                          ? "#2e7d32"
                          : d.qty >= 33
                            ? "#ed6c02"
                            : "#9e9e9e",
                      }}
                    >
                      {Math.round(d.qty)}
                    </span>
                  </td>
                  <td>{d.fill_price.toFixed(4)}</td>
                  <td>{d.position_after.toFixed(4)}</td>
                  <td>{d.cash_after.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
                  <td style={{ color: d.realized_pnl >= 0 ? "#4caf50" : "#f44336", fontWeight: 600 }}>
                    {d.realized_pnl !== 0
                      ? `${d.realized_pnl >= 0 ? "+" : ""}${d.realized_pnl.toFixed(0)}`
                      : "—"}
                  </td>
                  <td
                    style={{
                      maxWidth: 300,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {d.signal_reason}
                  </td>
                </tr>
              ))}
            </tbody>
          </Box>
        </Box>
      </AccordionDetails>
    </Accordion>
  );
}
